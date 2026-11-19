"""Live check of a connected service (see odysseus/mcpapp.py) from the chat, with the real model and without Telegram.

Asks the real model one question that only reads, and records what it does. The stand-in owner declines every
approval, so nothing is changed. Connect the service first with scripts/connect_mcp.py.

    .venv/Scripts/python scripts/live_service_model.py granola "What was my most recent meeting about?"
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import warnings
from dataclasses import replace
from pathlib import Path

warnings.simplefilter("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exa_py import Exa  # noqa: E402

from odysseus.agent import AgentRunner, conversation_prompt  # noqa: E402
from odysseus.config import load  # noqa: E402
from odysseus.conversation import Conversation  # noqa: E402
from odysseus.journal import Journal, Ledger  # noqa: E402
from odysseus.mcpapp import connected  # noqa: E402
from odysseus.memory import MemoryStore  # noqa: E402
from odysseus.tools import make_gate, make_tool_server  # noqa: E402


class OwnerChat:
    def __init__(self, conv_getter, owner_id):
        self.sent, self.asks, self._conv, self._owner = [], [], conv_getter, owner_id

    async def send(self, text):
        self.sent.append(text)

    async def draft(self, draft_id, text):
        pass

    async def ask(self, text, approval_id):
        self.asks.append(text)

        async def tap():
            await asyncio.sleep(0.5)
            await self._conv().handle_decision(self._owner, approval_id, False)     # always decline: nothing is changed
        asyncio.get_running_loop().create_task(tap())


async def main():
    want = sys.argv[1] if len(sys.argv) > 1 else ""
    question = sys.argv[2] if len(sys.argv) > 2 else "What was my most recent meeting about, and who was in it?"
    real = load()
    settings = replace(real, data_dir=Path(tempfile.mkdtemp(prefix="odysseus-service-")))
    journal = Journal(settings.data_dir / "journal.jsonl")
    services = [s for s in connected(Path(real.data_dir), journal) if s.slug == want]
    if not services:
        print(f"'{want}' is not connected. Run scripts/connect_mcp.py {want} first.")
        return 1
    ledger, memory = Ledger(journal), MemoryStore(settings.data_dir / "memory")
    gate = make_gate(journal, services=services)
    spec = Exa(api_key=settings.exa_api_key).tools.web_search()
    runner = AgentRunner(settings, journal, make_tool_server(spec, journal, memory, gate=gate, services=services), gate=gate,
                         system_prompt=conversation_prompt(False) + "".join(s.prompt() for s in services))
    conv = Conversation(settings, journal, ledger, runner, memory, gate=gate, services=services)
    chat = OwnerChat(lambda: conv, settings.owner_id)
    t = time.monotonic()
    await conv.handle(settings.owner_id, question, chat)
    await conv.wait()
    recs = journal.read()
    end = [r for r in recs if r["kind"] == "task_end"][-1]
    await runner.close()
    print(json.dumps({"asked": question, "status": end["status"], "seconds": round(time.monotonic() - t, 1), "cost_usd": round(end["cost_usd"], 4),
                      "tools": [(r.get("tool") or r.get("name"), bool(r.get("ok"))) for r in recs if r["kind"] == "tool"],
                      "gate": [r["verdict"] for r in recs if r["kind"] == "gate"], "questions": chat.asks, "reply": "\n".join(chat.sent),
                      "errors": [{k: r.get(k) for k in ("where", "error", "message")} for r in recs if r["kind"] == "error"]},
                     indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
