"""Live check of mail and calendar on the owner's real account, without Telegram.

Reads only, plus one send that is DECLINED, so nothing leaves the account.
The contents of mail are not printed: only counts, statuses and which tools ran.

    .venv/Scripts/python scripts/live_apps.py
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

from odysseus.agent import AgentRunner, conversation_prompt  # noqa: E402
from odysseus.apps import Apps, ComposioBackend  # noqa: E402
from odysseus.config import load  # noqa: E402
from odysseus.conversation import Conversation  # noqa: E402
from odysseus.journal import Journal, Ledger  # noqa: E402
from odysseus.memory import MemoryStore  # noqa: E402
from odysseus.routines import make_routine_runner  # noqa: E402
from odysseus.tools import make_gate, make_tool_server  # noqa: E402


class OwnerChat:
    def __init__(self, conv_getter, owner_id, approve):
        self.sent, self.asks, self._conv, self._owner, self._approve = [], [], conv_getter, owner_id, approve

    async def send(self, text):
        self.sent.append(text)

    async def draft(self, draft_id, text):
        pass

    async def ask(self, text, approval_id):
        self.asks.append(text)

        async def tap():
            await asyncio.sleep(0.5)
            await self._conv().handle_decision(self._owner, approval_id, self._approve)
        asyncio.get_running_loop().create_task(tap())


async def main():
    settings = replace(load(), data_dir=Path(f"var/live/apps-{int(time.time())}"))
    journal = Journal(settings.data_dir / "journal.jsonl")
    ledger = Ledger(journal)
    memory = MemoryStore(settings.data_dir / "memory")
    backend = ComposioBackend(settings.composio_api_key, f"odysseus-{settings.owner_id}")
    apps = Apps(backend, journal, tz=settings.tz)
    spec = Exa(api_key=settings.exa_api_key).tools.web_search()
    gate = make_gate(journal, with_apps=True)
    runner = AgentRunner(settings, journal, make_tool_server(spec, journal, memory, gate=gate, apps=apps), gate=gate,
                         system_prompt=conversation_prompt(True))
    conv = Conversation(settings, journal, ledger, runner, memory, gate=gate, apps=apps)
    out: dict = {"connected": backend.connected()}

    async def ask(label, text, approve=False, show_question=False):
        chat = OwnerChat(lambda: conv, settings.owner_id, approve)
        before = len(journal.read())
        t = time.monotonic()
        await conv.handle(settings.owner_id, text, chat)
        await conv.wait()
        new = journal.read()[before:]
        end = [r for r in new if r["kind"] == "task_end"][-1]
        out[label] = {"asked": text, "status": end["status"], "seconds": round(time.monotonic() - t, 1), "cost_usd": round(end["cost_usd"], 4),
                      "tools": [(r["name"], r["ok"]) for r in new if r["kind"] == "tool"],
                      "gate": [r["verdict"] for r in new if r["kind"] == "gate"],
                      "reply_chars": sum(len(s) for s in chat.sent), "questions_shown": len(chat.asks)}
        if show_question:
            out[label]["question"] = chat.asks
            out[label]["reply"] = chat.sent

    await ask("1_calendar", "How many events are on my calendar in the next seven days? Answer with the number and nothing else.")
    await ask("2_mail", "How many emails have I received in the last two days? Answer with the number and nothing else.")
    await ask("3_read_one", "Open the most recent email in my inbox and tell me only how many words long it is, roughly.")
    # A send on the real account, declined. The address is a reserved example address and nothing is sent.
    await ask("4_send_declined", "Send an email to nobody@example.com with the subject 'Odysseus gate test' and the body "
                                 "'This is a test of the approval gate.'", approve=False, show_question=True)
    await runner.close()

    # An unattended routine reading the real mailbox with the small model.
    run = make_routine_runner(settings, journal, make_tool_server(spec, journal, memory, read_only=True, apps=apps), memory, with_apps=True)
    before = len(journal.read())
    t = time.monotonic()
    status, text, cost = await run("Check the owner's mail from the last day. If any message looks like it needs an urgent reply from "
                                   "the owner today, say which in one line each. Otherwise reply with exactly NO_REPLY.")
    new = journal.read()[before:]
    out["5_routine_reads_mail"] = {"status": status, "quiet": text.strip().upper().strip(" .") == "NO_REPLY", "reply_chars": len(text),
                                   "seconds": round(time.monotonic() - t, 1), "cost_usd": round(cost, 4),
                                   "tools": [(r["name"], r["ok"]) for r in new if r["kind"] == "tool"]}

    out["errors"] = [{k: r.get(k) for k in ("where", "error", "message")} for r in journal.read() if r["kind"] == "error"]
    out["failed_tools"] = [r for r in journal.read() if r["kind"] == "tool" and not r["ok"]]
    out["total_cost_usd"] = round(ledger.spent_today() + cost, 4)
    print(json.dumps(out, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
