"""Live check of stage 5 (the browser), without Telegram.

Runs the vendor's browsing agent on two ordinary public pages that welcome
automated visitors, then checks that a signed-in run is held for approval and,
when declined, never starts. Spends vendor credit (cents).

    .venv/Scripts/python scripts/live_browser.py
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
from odysseus.browser import Browser, BrowserUseBackend  # noqa: E402
from odysseus.config import load  # noqa: E402
from odysseus.conversation import Conversation  # noqa: E402
from odysseus.journal import Journal, Ledger  # noqa: E402
from odysseus.memory import MemoryStore  # noqa: E402
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
    settings = replace(load(), data_dir=Path(f"var/live/browser-{int(time.time())}"))
    journal = Journal(settings.data_dir / "journal.jsonl")
    ledger = Ledger(journal)
    memory = MemoryStore(settings.data_dir / "memory")
    backend = BrowserUseBackend(settings.browser_use_api_key, settings.data_dir / "browser.json")
    browser = Browser(backend, journal, settings.task_usd)
    spec = Exa(api_key=settings.exa_api_key).tools.web_search()
    gate = make_gate(journal, with_browser=True)
    runner = AgentRunner(settings, journal, make_tool_server(spec, journal, memory, gate=gate, browser=browser), gate=gate,
                         system_prompt=conversation_prompt(False, True))
    conv = Conversation(settings, journal, ledger, runner, memory, gate=gate, browser=browser)
    out: dict = {}

    async def ask(label, text, approve=False):
        chat = OwnerChat(lambda: conv, settings.owner_id, approve)
        before = len(journal.read())
        t = time.monotonic()
        await conv.handle(settings.owner_id, text, chat)
        await conv.wait()
        new = journal.read()[before:]
        end = [r for r in new if r["kind"] == "task_end"][-1]
        out[label] = {"asked": text, "status": end["status"], "seconds": round(time.monotonic() - t, 1),
                      "cost_usd": round(end["cost_usd"], 4), "browser_usd": round(end.get("browser_usd") or 0, 4),
                      "tools": [{k: r.get(k) for k in ("name", "ok", "signed_in", "status", "cost_usd", "error") if r.get(k) not in (None, "")}
                                for r in new if r["kind"] == "tool"],
                      "gate": [r["verdict"] for r in new if r["kind"] == "gate"],
                      "question": chat.asks, "reply": chat.sent}

    await ask("1_read_a_page", "Use the browser to open https://example.com and tell me the main heading on the page, exactly as written.")
    await ask("2_work_through_a_site", "Use the browser: on https://books.toscrape.com, open the 'Travel' category and tell me the "
                                       "title and price of the first book listed.")
    await ask("3_signed_in_declined", "Use the browser signed in as me to open https://example.com and report the heading.", approve=False)
    await runner.close()

    out["errors"] = [{k: r.get(k) for k in ("where", "error", "message")} for r in journal.read() if r["kind"] == "error"]
    out["total_cost_usd"] = round(ledger.spent_today(), 4)
    print(json.dumps(out, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
