"""Live check of stage 3 (reminders and routines), without Telegram.

The real model sets the jobs. The clock is then moved forward by hand so they
fall due, and the real background model runs the routines. Uses its own folder.

    .venv/Scripts/python scripts/live_schedule.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exa_py import Exa  # noqa: E402

from odysseus.agent import AgentRunner  # noqa: E402
from odysseus.config import load  # noqa: E402
from odysseus.conversation import Conversation  # noqa: E402
from odysseus.journal import Journal, Ledger  # noqa: E402
from odysseus.memory import MemoryStore  # noqa: E402
from odysseus.routines import make_routine_runner  # noqa: E402
from odysseus.schedule import Scheduler, ScheduleStore  # noqa: E402
from odysseus.tools import make_tool_server  # noqa: E402


class Clock:
    """The real clock plus an offset the script controls."""
    def __init__(self):
        self.offset = timedelta()

    def __call__(self):
        return datetime.now() + self.offset


class ConsoleChat:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)

    async def draft(self, draft_id, text):
        pass


async def main():
    settings = replace(load(), data_dir=Path(f"var/live/sched-{int(time.time())}"))
    now = Clock()
    journal = Journal(settings.data_dir / "journal.jsonl")
    ledger = Ledger(journal)
    memory = MemoryStore(settings.data_dir / "memory", now=now)
    store = ScheduleStore(settings.data_dir / "schedule.db", now=now)
    spec = Exa(api_key=settings.exa_api_key).tools.web_search()
    runner = AgentRunner(settings, journal, make_tool_server(spec, journal, memory, store))
    conv = Conversation(settings, journal, ledger, runner, memory, now=now, schedule=store)
    pushed = ConsoleChat()      # what the scheduler sends unprompted
    run_routine = make_routine_runner(settings, journal, make_tool_server(spec, journal, memory, read_only=True), memory, now)
    sched = Scheduler(store, pushed, journal, ledger, settings.daily_usd, run_routine, now=now, on_delivered=conv.record_delivery)
    out: dict = {"started_at": now().strftime("%a %Y-%m-%d %H:%M")}

    async def ask(label, text):
        chat = ConsoleChat()
        t = time.monotonic()
        await conv.handle(settings.owner_id, text, chat)
        await conv.wait()
        end = [r for r in journal.read() if r["kind"] == "task_end"][-1]
        out[label] = {"asked": text, "reply": chat.sent, "seconds": round(time.monotonic() - t, 1), "cost_usd": round(end["cost_usd"], 4)}

    await ask("1_reminder", "Remind me in two minutes to stretch.")
    await ask("2_routine", "Every weekday at 8am, send me the single biggest AI news headline of the day with its source.")
    await ask("3_quiet_routine", "Each morning at 7, check whether a stable Python version newer than 3.15.2 has been released. "
                                 "Tell me only if it has; otherwise stay silent.")
    out["jobs_set"] = [j.describe() for j in store.jobs()]

    # Three minutes later: the reminder is due.
    now.offset = timedelta(minutes=3)
    ran = await sched.tick()
    out["after_3_minutes"] = {"ran": ran, "pushed": list(pushed.sent)}

    # Jump to 08:01 on the next weekday: both routines are due.
    target = (datetime.now() + timedelta(days=1)).replace(hour=8, minute=1, second=0, microsecond=0)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    now.offset = target - datetime.now()
    before = len(pushed.sent)
    t = time.monotonic()
    ran = await sched.tick()
    out["next_weekday_0801"] = {"clock": now().strftime("%a %Y-%m-%d %H:%M"), "ran": ran,
                                "seconds": round(time.monotonic() - t, 1), "pushed": pushed.sent[before:]}
    out["runs"] = [{k: r[k] for k in ("kind", "due", "outcome", "detail", "cost_usd")} for r in store.runs()]
    out["jobs_after"] = [j.describe() for j in store.jobs()]

    # A follow-up in the conversation about what the routine sent.
    await ask("4_follow_up", "What was that headline you just sent me about? One line.")
    chat = ConsoleChat()
    await conv.handle(settings.owner_id, "/schedule", chat)
    out["slash_schedule"] = chat.sent
    await runner.close()

    out["tool_calls"] = [{k: r.get(k) for k in ("name", "ok", "job_kind", "when", "repeat", "results", "error")}
                         for r in journal.read() if r["kind"] == "tool"]
    out["errors"] = [r for r in journal.read() if r["kind"] == "error"]
    out["total_cost_usd"] = round(ledger.spent_today(), 4)
    print(json.dumps(out, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
