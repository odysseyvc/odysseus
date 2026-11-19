"""Live check of a pitch deck sent as a file: real text recognition, the real model, a real web search, no Telegram.

The deck is sent with no message, as an owner would forward one. The firm's database is the made-up one the
evaluations use, so nothing is written to a real workspace. Afterwards one question is asked that only a reader who
saw the pages can answer. Make the deck first:

    .venv/Scripts/python scripts/make_sample_deck.py var/sample-deck.pdf
    .venv/Scripts/python scripts/live_deck.py var/sample-deck.pdf
    .venv/Scripts/python scripts/live_deck.py var/sample-deck.pdf "Which month in the chart is lower than the month before it?"
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import warnings
from dataclasses import replace
from pathlib import Path

warnings.simplefilter("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exa_py import Exa  # noqa: E402

from evals.crm import FakeAttio, FakeGranola  # noqa: E402
from evals.partner import THESIS, seed  # noqa: E402
from odysseus.agent import AgentRunner, conversation_prompt  # noqa: E402
from odysseus.attio import Attio  # noqa: E402
from odysseus.config import load  # noqa: E402
from odysseus.conversation import Conversation  # noqa: E402
from odysseus.journal import Journal, Ledger  # noqa: E402
from odysseus.memory import MemoryStore  # noqa: E402
from odysseus.ocr import MistralOcr  # noqa: E402
from odysseus.tools import make_gate, make_tool_server  # noqa: E402


class Chat:
    def __init__(self):
        self.sent, self.asks = [], []

    async def send(self, text):
        self.sent.append(text)

    async def draft(self, draft_id, text):
        pass

    async def ask(self, text, approval_id):
        self.asks.append(text)


async def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "var/sample-deck.pdf")
    question = sys.argv[2] if len(sys.argv) > 2 else "Look at the chart on the traction page. Which month is the highest, and is any month lower than the one before it?"
    real = load()
    if not real.mistral_api_key or not path.exists():
        print("This needs a text-recognition key in the settings and the deck's file.")
        return 1
    settings = replace(real, data_dir=Path(f"var/live/deck-{int(time.time())}"))
    journal = Journal(settings.data_dir / "journal.jsonl")
    memory = MemoryStore(settings.data_dir / "memory")
    memory.remember(THESIS)
    crm = FakeAttio()
    seed(crm, FakeGranola())
    attio = Attio(crm, journal)
    gate = make_gate(journal, timeout_s=30, attio=attio, auto_small=True)
    search = Exa(api_key=settings.exa_api_key).tools.web_search()
    runner = AgentRunner(settings, journal, make_tool_server(search, journal, memory, gate=gate, attio=attio), gate=gate,
                         system_prompt=conversation_prompt(False, with_files=True, with_attio=True))
    conv = Conversation(settings, journal, Ledger(journal), runner, memory, gate=gate, attio=attio, ocr=MistralOcr(real.mistral_api_key))
    chat = Chat()
    attio.warm()
    await attio.layout()
    data = path.read_bytes()
    t = time.monotonic()
    await conv.handle_file(settings.owner_id, data, path.name, "application/pdf", len(data), "", chat)
    await conv.wait()
    first = time.monotonic() - t
    replies = len(chat.sent)
    await conv.handle(settings.owner_id, question, chat)
    await conv.wait()
    await runner.close()
    print(f"=== the deck, sent with no message ({first:.0f}s) ===\n" + "\n\n".join(chat.sent[:replies]))
    print(f"\n=== {question} ===\n" + "\n\n".join(chat.sent[replies:]))
    print("\n=== what happened ===")
    for r in journal.read():
        if r["kind"] == "file":
            print("file", {k: r.get(k) for k in ("name", "ok", "type", "bytes", "pages", "chars", "seen", "error")})
        elif r["kind"] == "tool":
            print("tool", r.get("name"), r.get("tool", ""), "ok" if r.get("ok") else f"REFUSED/FAILED: {str(r.get('error', ''))[:120]}")
        elif r["kind"] in ("task_end", "error"):
            print(r["kind"], {k: v for k, v in r.items() if k not in ("ts", "kind")})
    print("asked the owner:", len(chat.asks))
    print("written to the database:", json.dumps([(n, {k: v for k, v in a.items() if k != "content"} | ({"content": a["content"][:600]} if "content" in a else {}))
                                                  for n, a in crm.writes], indent=1, ensure_ascii=False, default=str))
    print(f"cost: ${sum(r.get('cost_usd') or 0 for r in journal.read() if r['kind'] == 'task_end'):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
