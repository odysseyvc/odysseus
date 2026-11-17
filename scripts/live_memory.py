"""Live check of stage 2 (memory), without Telegram. Uses its own empty memory folder.

The stage's test, from Chapter 5: a preference stated in one conversation is used
unprompted in a later one, and a changed fact replaces the old one.

    .venv/Scripts/python scripts/live_memory.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exa_py import Exa  # noqa: E402

from odysseus.agent import AgentRunner  # noqa: E402
from odysseus.config import load  # noqa: E402
from odysseus.conversation import Conversation  # noqa: E402
from odysseus.journal import Journal, Ledger  # noqa: E402
from odysseus.memory import MemoryStore  # noqa: E402
from odysseus.tools import make_tool_server  # noqa: E402


class ConsoleChat:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)

    async def draft(self, draft_id, text):
        pass


async def main():
    stamp = int(time.time())
    settings = replace(load(), data_dir=Path(f"var/live/mem-{stamp}"))
    journal = Journal(settings.data_dir / "journal.jsonl")
    spec = Exa(api_key=settings.exa_api_key).tools.web_search()
    out: dict = {}

    def boot():
        """Everything a restart of the program rebuilds. Only the files on disk carry over."""
        memory = MemoryStore(settings.data_dir / "memory")
        runner = AgentRunner(settings, journal, make_tool_server(spec, journal, memory))
        return memory, runner, Conversation(settings, journal, Ledger(journal), runner, memory)

    async def ask(conv, label, text):
        chat = ConsoleChat()
        t = time.monotonic()
        await conv.handle(settings.owner_id, text, chat)
        await conv.wait()
        end = [r for r in journal.read() if r["kind"] == "task_end"][-1]
        out[label] = {"asked": text, "reply": chat.sent, "seconds": round(time.monotonic() - t, 1),
                      "status": end["status"], "cost_usd": round(end["cost_usd"], 4)}

    # Conversation 1: state two facts.
    memory, runner, conv = boot()
    await ask(conv, "1_state_facts", "A couple of things about me: I'm vegetarian, and I always want an aisle seat on flights.")
    out["profile_after_1"] = [e.text for e in memory.profile()]
    await runner.close()

    # Conversation 2, after a "restart": is the first fact used without being mentioned?
    memory, runner, conv = boot()
    out["new_session"] = runner.session_id is None
    await ask(conv, "2_dinner", "Suggest one dinner dish I could cook tonight. One line.")
    await ask(conv, "3_flight", "If I book a flight next month, what should I ask for when choosing a seat? One line.")
    # A changed fact.
    await ask(conv, "4_change", "Actually, I've started eating fish again, though still no meat.")
    out["profile_after_4"] = [e.text for e in memory.profile()]
    out["history_actions"] = [r["action"] for r in memory.history()]
    chat = ConsoleChat()
    await conv.handle(settings.owner_id, "/memory", chat)
    out["slash_memory"] = chat.sent
    await runner.close()

    # Conversation 3: is the changed fact the one that is used?
    memory, runner, conv = boot()
    await ask(conv, "5_dinner_again", "Suggest one dinner dish for tonight that uses the newest thing I told you about my diet. One line.")
    await runner.close()

    out["tool_calls"] = [{k: r.get(k) for k in ("name", "ok", "replaces", "results")} for r in journal.read() if r["kind"] == "tool"]
    out["errors"] = [r for r in journal.read() if r["kind"] == "error"]
    out["total_cost_usd"] = round(sum(r.get("cost_usd") or 0 for r in journal.read() if r["kind"] == "task_end"), 4)
    print(json.dumps(out, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
