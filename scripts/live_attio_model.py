"""Live check of Attio from the chat, with the real model and without Telegram.

Asks the real model two questions that only read Attio and one request that would change it,
and records what it does. The stand-in owner declines every approval, so nothing is changed in
Attio. Run scripts/connect_attio.py first.

    .venv/Scripts/python scripts/live_attio_model.py
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
from odysseus.attio import Attio, FileTokens, McpBackend  # noqa: E402
from odysseus.config import load  # noqa: E402
from odysseus.conversation import Conversation  # noqa: E402
from odysseus.journal import Journal, Ledger  # noqa: E402
from odysseus.memory import MemoryStore  # noqa: E402
from odysseus.tools import make_gate, make_tool_server  # noqa: E402

READ = "In Attio, what stages can a deal be in, and how many deals are in the pipeline right now?"
FIELD = "Which people in Attio have the role Founder, and which company is each with?"
WRITE = ("Add a company to Attio called Example Robotics, website example-robotics.example, and open a Seed deal for it: "
         "sourced inbound, our cheque 500,000 dollars, stage Sourced, next step 'intro call'.")


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
    real = load()
    tokens = FileTokens(Path(real.data_dir) / "attio.json")
    if not tokens.signed_in():
        print("Attio is not connected. Run scripts/connect_attio.py first.")
        return 1
    settings = replace(real, data_dir=Path(tempfile.mkdtemp(prefix="odysseus-attio-")))
    journal = Journal(settings.data_dir / "journal.jsonl")
    ledger = Ledger(journal)
    memory = MemoryStore(settings.data_dir / "memory")
    attio = Attio(McpBackend(tokens), journal)
    spec = Exa(api_key=settings.exa_api_key).tools.web_search()
    gate = make_gate(journal, attio=attio)
    runner = AgentRunner(settings, journal, make_tool_server(spec, journal, memory, gate=gate, attio=attio), gate=gate,
                         system_prompt=conversation_prompt(False, with_attio=True))
    conv = Conversation(settings, journal, ledger, runner, memory, gate=gate, attio=attio)
    attio.warm()            # as the program does at start-up
    await attio.layout()
    out: dict = {}

    async def ask(label, text):
        chat = OwnerChat(lambda: conv, settings.owner_id)
        before = len(journal.read())
        t = time.monotonic()
        await conv.handle(settings.owner_id, text, chat)
        await conv.wait()
        new = journal.read()[before:]
        end = [r for r in new if r["kind"] == "task_end"][-1]
        out[label] = {"asked": text, "status": end["status"], "seconds": round(time.monotonic() - t, 1), "cost_usd": round(end["cost_usd"], 4),
                      "tools": [{k: r.get(k) for k in ("name", "ok", "tool", "action", "tools", "error") if r.get(k) not in (None, "")}
                                for r in new if r["kind"] == "tool"],
                      "gate": [r["verdict"] for r in new if r["kind"] == "gate"],
                      "questions": [q[:1500] for q in chat.asks], "reply": "\n".join(chat.sent)}

    await ask("1_read", READ)
    await ask("2_field", FIELD)
    await ask("3_write_declined", WRITE)
    await runner.close()
    out["errors"] = [{k: r.get(k) for k in ("where", "error", "message")} for r in journal.read() if r["kind"] == "error"]
    out["total_cost_usd"] = round(ledger.spent_today(), 4)
    print(json.dumps(out, indent=2, default=str, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
