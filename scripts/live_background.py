"""Two things at once, live: a deck dealt with in the background while a question is answered in the conversation.

    python scripts/live_background.py [var/sample-deck.pdf]

The real model on both agents, the real text recognition and page reader, a stand-in database with invented records.
Nothing real is filed or sent. Prints when each thing started and ended, so that the overlap can be seen.
"""
from __future__ import annotations

import asyncio
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
from odysseus.pages import make_page_reader  # noqa: E402
from odysseus.tools import empty_server, make_gate, make_tool_server  # noqa: E402

T0 = time.monotonic()


class Chat:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append((round(time.monotonic() - T0), text))

    async def draft(self, draft_id, text):
        pass

    async def ask(self, text, approval_id, thread=False):
        self.sent.append((round(time.monotonic() - T0), "[a question to the owner] " + text[:80]))


async def main() -> int:
    global T0
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "var/sample-deck.pdf")
    real = load()
    settings = replace(real, data_dir=Path(f"var/live/background-{int(time.time())}"))
    journal = Journal(settings.data_dir / "journal.jsonl")
    memory = MemoryStore(settings.data_dir / "memory")
    memory.remember(THESIS)
    crm = FakeAttio()
    seed(crm, FakeGranola())
    attio = Attio(crm, journal)
    gate = make_gate(journal, timeout_s=30, attio=attio, auto_small=True)
    search = Exa(api_key=settings.exa_api_key).tools.web_search()
    prompt = conversation_prompt(False, with_files=True, with_attio=True)

    def tools():
        return make_tool_server(search, journal, memory, gate=gate, attio=attio)
    runner = AgentRunner(settings, journal, tools(), gate=gate, system_prompt=prompt)
    worker = AgentRunner(settings, journal, tools(), gate=gate, system_prompt=prompt)
    conv = Conversation(settings, journal, Ledger(journal), runner, memory, gate=gate, attio=attio, ocr=MistralOcr(real.mistral_api_key),
                        pages=make_page_reader(settings, journal, empty_server()), worker=[worker])
    chat = Chat()
    attio.warm()
    await attio.layout()
    await asyncio.gather(runner.start(), worker.start())
    T0 = time.monotonic()
    data = path.read_bytes()
    await conv.handle_file(settings.owner_id, data, path.name, "application/pdf", len(data), "", chat)       # the deck, with no message
    sent_deck = round(time.monotonic() - T0)
    await conv.handle(settings.owner_id, "Which of our deals are at Diligence right now? One line.", chat)        # asked straight after
    await conv.wait()
    await asyncio.gather(runner.close(), worker.close())
    print(f"deck handed over at {sent_deck}s; question asked at {sent_deck}s")
    for at, text in chat.sent:
        print(f"\n--- at {at}s ---\n{text[:700]}")
    print("\n--- the journal's account ---")
    start = journal.read()[0]["ts"]
    for r in journal.read():
        if r["kind"] in ("task_start", "task_end"):
            print(f"{r['ts'] - start:6.1f}s  {r['kind']:10} {'background' if r.get('background') else 'conversation':12} "
                  + (f"{r.get('status')} ${r.get('cost_usd', 0):.3f} {r.get('seconds')}s" if r["kind"] == "task_end" else ""))
    print("written to the stand-in database:", [n for n, _ in crm.writes])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
