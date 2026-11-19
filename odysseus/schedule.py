"""Acting later: reminders, routines and the loop that runs them.

Two kinds of job:

  reminder   a fixed message, sent at a time. Plain code sends it. No model is
             involved, so it costs nothing and cannot be talked out of it.
  routine    a task for the agent, run at a time with nobody watching. It runs
             on a separate, read-only agent and its result is sent to the owner.

Every run ends as exactly one of three recorded outcomes:

  delivered  something was sent to the owner
  quiet      a routine decided there was nothing worth saying
  failed     it could not be done; the owner is told, so silence never means failure

A reminder that cannot be sent (the network is down, or WhatsApp refuses it because the
owner has not written for 24 hours) is not thrown away. It is tried on the second channel
if there is one, and otherwise again after 1, 5, 15 and 30 minutes, and only then recorded
as failed. A reminder is sent before it is recorded as delivered, so a crash between the
two means it is sent twice, never lost.

Times are local wall-clock times without a zone ("2026-11-18T09:00"), taken from
the same clock that stamps the owner's messages.
"""
from __future__ import annotations

import asyncio
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from .journal import Journal, Ledger

REPEATS = ("once", "hourly", "daily", "weekdays", "weekly")
MAX_JOBS = 50
MAX_TEXT = 600
NO_REPLY = "NO_REPLY"       # what a routine answers when there is nothing worth sending
LATE_AFTER = timedelta(minutes=5)
RETRY_AFTER = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15), timedelta(minutes=30))


class ScheduleError(Exception):
    """A refusal the model should be told about."""


@dataclass
class Job:
    id: str
    kind: str          # reminder | routine
    text: str          # the reminder's message, or the routine's instructions
    next_run: datetime
    repeat: str
    created: datetime

    def describe(self, limit: int | None = None) -> str:
        when = self.next_run.strftime("%a %d %b %Y, %H:%M")
        every = "" if self.repeat == "once" else f", repeating {self.repeat}"
        text = self.text if limit is None or len(self.text) <= limit else self.text[:limit].rstrip() + "…"
        return f"[{self.id}] {self.kind} at {when}{every}: {text}"


def parse_when(value: str, now: datetime) -> datetime:
    try:
        when = datetime.fromisoformat(str(value).strip())
    except ValueError as e:
        raise ScheduleError("`when` must be a local date and time like 2026-11-18T09:00.") from e
    if when.tzinfo is not None:
        raise ScheduleError("Give `when` as local time with no zone or offset, like 2026-11-18T09:00.")
    if when < now - timedelta(minutes=1):
        raise ScheduleError(f"That time ({when:%Y-%m-%d %H:%M}) is in the past. It is now {now:%Y-%m-%d %H:%M}.")
    if when > now + timedelta(days=730):
        raise ScheduleError("That is more than two years away.")
    return when.replace(second=0, microsecond=0)


def next_occurrence(after: datetime, repeat: str, now: datetime) -> datetime | None:
    """The first time later than `now` on this job's rhythm, or None for a one-off."""
    if repeat == "once":
        return None
    step = {"hourly": timedelta(hours=1), "daily": timedelta(days=1), "weekdays": timedelta(days=1),
            "weekly": timedelta(days=7)}[repeat]
    t = after + step
    while t <= now or (repeat == "weekdays" and t.weekday() >= 5):
        t += step
    return t


class ScheduleStore:
    def __init__(self, path: Path, now: Callable[[], datetime] = datetime.now):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._now = now
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("create table if not exists jobs (id text primary key, kind text, text text, "
                         "next_run text, repeat text, created text)")
        self._db.execute("create table if not exists runs (job_id text, kind text, due text, ran text, "
                         "outcome text, detail text, cost_usd real)")
        self._pending: list[str] = []     # human-readable changes since the last pop_changes()

    def add(self, kind: str, text: str, when: str, repeat: str = "once") -> Job:
        text = " ".join(str(text).split())
        if kind not in ("reminder", "routine"):
            raise ScheduleError("kind must be reminder or routine.")
        if not text:
            raise ScheduleError("There is nothing to schedule.")
        if len(text) > MAX_TEXT:
            raise ScheduleError(f"Too long: {len(text)} characters, and the limit is {MAX_TEXT}.")
        repeat = (repeat or "once").strip().lower()
        if repeat not in REPEATS:
            raise ScheduleError("repeat must be one of: " + ", ".join(REPEATS) + ".")
        if len(self.jobs()) >= MAX_JOBS:
            raise ScheduleError(f"There are already {MAX_JOBS} scheduled jobs. Cancel one first.")
        now = self._now()
        taken = {j.id for j in self.jobs()}
        job_id = secrets.token_hex(2)
        while job_id in taken:       # ids are short so they are easy to type; that makes a clash possible
            job_id = secrets.token_hex(2)
        job = Job(job_id, kind, text, parse_when(when, now), repeat, now)
        self._db.execute("insert into jobs values (?,?,?,?,?,?)",
                         (job.id, kind, text, job.next_run.isoformat(), repeat, now.isoformat()))
        self._pending.append("set " + job.describe(limit=140))
        return job

    def jobs(self) -> list[Job]:
        rows = self._db.execute("select id, kind, text, next_run, repeat, created from jobs order by next_run")
        return [Job(r[0], r[1], r[2], datetime.fromisoformat(r[3]), r[4], datetime.fromisoformat(r[5])) for r in rows]

    def due(self) -> list[Job]:
        now = self._now()
        return [j for j in self.jobs() if j.next_run <= now]

    def cancel(self, job_id: str, by: str = "model") -> Job | None:
        job = next((j for j in self.jobs() if j.id == job_id.strip("[] ")), None)
        if job is None:
            return None
        self._db.execute("delete from jobs where id = ?", (job.id,))
        if by == "model":
            self._pending.append("cancelled " + job.describe(limit=140))
        return job

    def postpone(self, job: Job, until: datetime) -> None:
        """Try this job again later, without recording a run."""
        self._db.execute("update jobs set next_run = ? where id = ?", (until.isoformat(), job.id))

    def finish(self, job: Job, outcome: str, detail: str = "", cost_usd: float = 0.0) -> None:
        """Record one run and move the job on: to its next time, or out of the table."""
        now = self._now()
        self._db.execute("insert into runs values (?,?,?,?,?,?,?)",
                         (job.id, job.kind, job.next_run.isoformat(), now.isoformat(), outcome, detail[:500], cost_usd))
        nxt = next_occurrence(job.next_run, job.repeat, now)
        if nxt is None:
            self._db.execute("delete from jobs where id = ?", (job.id,))
        else:
            self._db.execute("update jobs set next_run = ? where id = ?", (nxt.isoformat(), job.id))

    def runs(self) -> list[dict[str, Any]]:
        cols = ("job_id", "kind", "due", "ran", "outcome", "detail", "cost_usd")
        return [dict(zip(cols, r)) for r in self._db.execute("select * from runs order by ran")]

    def listing(self) -> str:
        jobs = self.jobs()
        return "\n".join(j.describe() for j in jobs) if jobs else "(nothing scheduled)"

    def pop_changes(self) -> list[str]:
        out, self._pending = self._pending, []
        return out


class Chat(Protocol):
    async def send(self, text: str) -> None: ...


# Runs a routine's instructions on the read-only agent. Returns (status, text, cost_usd).
RunRoutine = Callable[[str], Awaitable[tuple[str, str, float]]]


class Scheduler:
    def __init__(self, store: ScheduleStore, chat: Chat, journal: Journal, ledger: Ledger, daily_usd: float,
                 run_routine: RunRoutine, now: Callable[[], datetime] = datetime.now,
                 on_delivered: Callable[[str], None] | None = None, tick_s: float = 20.0, fallback: Chat | None = None,
                 history_file: Any = None):
        self._store, self._chat, self._journal, self._ledger = store, chat, journal, ledger
        self._daily_usd, self._run_routine, self._now = daily_usd, run_routine, now
        self._on_delivered = on_delivered or (lambda text: None)
        self._tick_s = tick_s
        self._fallback = fallback                        # a second channel, used when the first refuses a message
        self._retries: dict[str, tuple[int, datetime]] = {}     # job id -> (tries so far, when it was first due)
        self._history_file = history_file                # what each routine sent the last few times, so it does not repeat itself
        try:
            import inspect
            self._takes_previous = len(inspect.signature(run_routine).parameters) >= 2
        except (TypeError, ValueError):
            self._takes_previous = False

    def _sent_before(self, job_id: str) -> tuple[str, ...]:
        try:
            import json
            return tuple(json.loads(self._history_file.read_text(encoding="utf-8")).get(job_id, [])) if self._history_file else ()
        except (OSError, ValueError):
            return ()

    def _remember_sent(self, job_id: str, text: str) -> None:
        if not self._history_file:
            return
        try:
            import json
            try:
                data = json.loads(self._history_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            data[job_id] = (data.get(job_id, []) + [text[:3000]])[-3:]
            self._history_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            self._journal.write("error", where="scheduler.history", error=type(e).__name__)

    async def _send(self, text: str) -> None:
        try:
            await self._chat.send(text)
        except Exception as e:
            if self._fallback is None:
                raise
            self._journal.write("error", where="scheduler.send", error=type(e).__name__, message=str(e)[:200], fallback=True)
            await self._fallback.send(text)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as e:     # the loop must outlive any one bad job
                self._journal.write("error", where="scheduler.tick", error=type(e).__name__, message=str(e)[:300])
            await asyncio.sleep(self._tick_s)

    async def tick(self) -> int:
        """Run every job that is due. Returns how many ran."""
        due = self._store.due()
        for job in due:
            await self._run(job)
        return len(due)

    async def _run(self, job: Job) -> None:
        tries, due = self._retries.get(job.id, (0, job.next_run))
        late = self._now() - due > LATE_AFTER
        why = "I could not send it then" if tries else "I was not running"
        suffix = f" (This was due at {due:%H:%M on %a %d %b} and is late because {why}.)" if late else ""
        outcome, detail, cost = "failed", "", 0.0
        try:
            if job.kind == "reminder":
                await self._send(f"Reminder: {job.text}{suffix}")
                self._on_delivered(f"Reminder: {job.text}")
                outcome = "delivered"
            else:
                outcome, detail, cost = await self._routine(job, suffix)
        except Exception as e:
            outcome, detail = "failed", f"{type(e).__name__}: {e}"
        if outcome == "failed" and job.kind == "reminder" and tries < len(RETRY_AFTER):
            # The message did not leave. Keep the reminder and try again shortly; do not delete it.
            self._retries[job.id] = (tries + 1, due)
            self._store.postpone(job, self._now() + RETRY_AFTER[tries])
            self._journal.write("scheduled_run", job=job.id, job_kind=job.kind, outcome="retrying", detail=detail[:200], attempt=tries + 1)
            return
        self._retries.pop(job.id, None)
        if tries:       # the job's place in its rhythm is counted from when it was really due
            job.next_run = due
        self._store.finish(job, outcome, detail, cost)
        self._journal.write("scheduled_run", job=job.id, job_kind=job.kind, outcome=outcome, detail=detail[:200],
                            cost_usd=cost, late=late)
        if outcome == "failed":
            try:    # silence must never mean failure
                await self._send(f"A scheduled {job.kind} failed and was not carried out: \"{job.text[:120]}\". Reason: {detail or 'unknown'}.")
            except Exception as e:
                self._journal.write("error", where="scheduler.announce", error=type(e).__name__)

    async def _routine(self, job: Job, suffix: str) -> tuple[str, str, float]:
        if self._ledger.spent_today() >= self._daily_usd:
            return "failed", f"today's budget of ${self._daily_usd:.2f} is used up", 0.0
        status, text, cost = await (self._run_routine(job.text, self._sent_before(job.id)) if self._takes_previous else self._run_routine(job.text))
        self._journal.write("task_end", status=status, cost_usd=cost, routine=job.id)      # the ledger counts this line
        text = (text or "").strip()
        if status != "done":
            return "failed", f"the routine ended as '{status}'", cost
        if not text:
            return "failed", "the routine produced nothing", cost
        if text.strip(" .\"'`").upper() == NO_REPLY:
            return "quiet", "", cost
        await self._send(f"{text}\n\n(Scheduled routine [{job.id}]){suffix}")
        self._on_delivered(text)
        self._remember_sent(job.id, text)
        return "delivered", "", cost
