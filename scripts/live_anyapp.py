"""Live check of reaching any app from the chat, without Telegram.

Asks the real model for a job in an app that may not be connected and records
what it does: whether it looks the tools up, whether it offers a connection
link instead of refusing, and, if the app is connected, whether the gate asks
before the first change. The stand-in owner declines every approval, so nothing
is changed anywhere. Links are not printed.

    .venv/Scripts/python scripts/live_anyapp.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import warnings
from dataclasses import replace
from pathlib import Path

warnings.simplefilter("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exa_py import Exa  # noqa: E402

from odysseus.agent import AgentRunner, conversation_prompt  # noqa: E402
from odysseus.anyapp import AnyApp  # noqa: E402
from odysseus.apps import ComposioBackend  # noqa: E402
from odysseus.config import load  # noqa: E402
from odysseus.conversation import Conversation  # noqa: E402
from odysseus.journal import Journal, Ledger  # noqa: E402
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
    settings = replace(load(), data_dir=Path(f"var/live/anyapp-{int(time.time())}"))
    journal = Journal(settings.data_dir / "journal.jsonl")
    ledger = Ledger(journal)
    memory = MemoryStore(settings.data_dir / "memory")
    connector = ComposioBackend(settings.composio_api_key, f"odysseus-{settings.owner_id}")
    anyapp = AnyApp(connector, journal)
    spec = Exa(api_key=settings.exa_api_key).tools.web_search()
    gate = make_gate(journal, anyapp=anyapp)
    runner = AgentRunner(settings, journal, make_tool_server(spec, journal, memory, gate=gate, anyapp=anyapp), gate=gate,
                         system_prompt=conversation_prompt(False, with_anyapp=True))
    conv = Conversation(settings, journal, ledger, runner, memory, gate=gate, anyapp=anyapp)
    out: dict = {"sheets_connected_before": dict((s, c) for s, _, c in connector.lookup("googlesheets")).get("googlesheets")}

    async def ask(label, text):
        chat = OwnerChat(lambda: conv, settings.owner_id)
        before = len(journal.read())
        t = time.monotonic()
        await conv.handle(settings.owner_id, text, chat)
        await conv.wait()
        new = journal.read()[before:]
        end = [r for r in new if r["kind"] == "task_end"][-1]
        reply = "\n".join(chat.sent)
        out[label] = {"asked": text, "status": end["status"], "seconds": round(time.monotonic() - t, 1), "cost_usd": round(end["cost_usd"], 4),
                      "tools": [{k: r.get(k) for k in ("name", "ok", "tool", "action", "app", "already", "tools", "error") if r.get(k) not in (None, "")}
                                for r in new if r["kind"] == "tool"],
                      "gate": [r["verdict"] for r in new if r["kind"] == "gate"],
                      "questions": [q[:400] for q in chat.asks],
                      "link_in_reply": bool(re.search(r"https://connect\.composio\.dev/\S+", reply)),
                      "reply": re.sub(r"https://connect\.composio\.dev/\S+", "<connect link>", reply)}

    await ask("1_sheets", "I want to use Google Sheets. Add a row to a sheet called 'Odysseus test' with today's date and the word hello.")
    await ask("2_notion", "Can you look at my Notion and tell me the titles of my three most recent pages?")
    await runner.close()
    out["errors"] = [{k: r.get(k) for k in ("where", "error", "message")} for r in journal.read() if r["kind"] == "error"]
    out["total_cost_usd"] = round(ledger.spent_today(), 4)
    print(json.dumps(out, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
