"""The log and the spending ledger.

Every step the agent takes is appended to one file as a line of JSON. The
ledger is derived from the same file and from nothing else, so what was spent
can always be checked against what was done.

The file the program reads at start-up holds the last few weeks only. When the
program starts, lines older than that are moved, unchanged, into one compressed
file per month beside it (journal-2026-10.jsonl.gz). Nothing is thrown away, and
what the program reads when it starts no longer grows with its age.
"""
from __future__ import annotations

import gzip
import json
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable


KEEP_DAYS = 35      # how much of the log stays in the file read at start-up: enough for a month's spending and the last conversation


class Journal:
    # Fields safe to copy to a hosting service's log: statuses and numbers, never contents.
    ECHO_FIELDS = ("status", "ok", "name", "verdict", "outcome", "where", "error", "cost_usd", "seconds", "turns", "parts", "chars", "pages")

    def __init__(self, path: Path, clock: Callable[[], float] = time.time, echo: bool = False, keep_days: int = KEEP_DAYS):
        self.path = path
        self._clock = clock
        self._echo = echo
        self.listeners: list[Callable[[dict[str, Any]], None]] = []     # told of every record as it is written (the ledger)
        path.parent.mkdir(parents=True, exist_ok=True)
        if keep_days:
            try:
                self.archive(keep_days)
            except OSError:
                pass        # a log that could not be tidied is still a log

    def archive(self, keep_days: int = KEEP_DAYS) -> int:
        """Move lines older than `keep_days` into one compressed file per month. Returns how many were moved.

        The old lines are written to their archives first and the shortened log replaces the old one in a single step
        after that. A crash in between leaves lines in both places, never in neither."""
        if not self.path.exists():
            return 0
        oldest = self._clock() - keep_days * 86400
        keep: list[str] = []
        months: dict[str, list[str]] = {}
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    ts = float(json.loads(line).get("ts"))
                except (ValueError, TypeError, AttributeError):
                    ts = None       # a half-written line: it stays where it is
                if ts is None or ts >= oldest or keep:      # the log is in time order: once one line is kept, the rest are
                    keep.append(line)
                else:
                    months.setdefault(datetime.fromtimestamp(ts).strftime("%Y-%m"), []).append(line)
        moved = sum(len(v) for v in months.values())
        if not moved:
            return 0
        for month, lines in months.items():
            with gzip.open(self.path.with_name(f"{self.path.stem}-{month}.jsonl.gz"), "at", encoding="utf-8") as z:
                z.writelines(lines)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8", newline="") as f:
            f.writelines(keep)
        os.replace(tmp, self.path)
        return moved

    def write(self, kind: str, **fields: Any) -> None:
        record = {"ts": round(self._clock(), 3), "kind": kind, **fields}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        for listener in self.listeners:
            listener(record)
        if self._echo:
            shown = " ".join(f"{k}={fields[k]}" for k in self.ECHO_FIELDS if k in fields)
            print(f"odysseus {kind} {shown}".rstrip(), flush=True)

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a half-written last line after a crash
        return out


# What the services that do not report a price per call are assumed to cost. These two are estimates from the
# makers' public price lists, not measured, and should be checked against an invoice.
OCR_USD_PER_PAGE = 0.002
VOICE_USD_PER_MINUTE = 0.003
SPEECH_USD_PER_CHAR = 13 / 1_000_000      # the maker's list price for text turned into speech


def cost_of(rec: dict[str, Any]) -> float:
    """What one journal record cost, in dollars. This function is the whole definition of the ledger.

    task_end   a conversation task or a routine: the model's cost as the agent library reported it, plus any browser run
    event      one check of an incoming event against the watches
    tool       a web search, at the price the search service reported for that call; or a paid read of a connected
               service such as X, estimated from how many items came back and the service's published prices
    file       a file read by the text-recognition service (estimated per page)
    voice      a voice note transcribed (estimated per minute)
    speech     a reply spoken (estimated per character)

    The connector for mail, calendar and apps is billed by subscription, not per call, and is not counted.
    """
    kind = rec.get("kind")
    try:
        if kind in ("task_end", "event"):
            return float(rec.get("cost_usd") or 0.0)
        if kind == "tool" and rec.get("name") != "browse":      # a web search at the price the service reported; a paid read of a
            return float(rec.get("cost_usd") or 0.0)             # connected service, as estimated. A browser run is in its task_end.
        if kind == "file" and rec.get("ok") and rec.get("pages"):
            return float(rec["pages"]) * OCR_USD_PER_PAGE
        if kind == "voice" and rec.get("ok"):
            return float(rec.get("seconds") or 0) / 60 * VOICE_USD_PER_MINUTE
        if kind == "speech" and rec.get("ok"):
            return float(rec.get("chars") or 0) * SPEECH_USD_PER_CHAR
    except (TypeError, ValueError):
        pass
    return 0.0


class Ledger:
    """Money spent per calendar day: every costed record in the journal, from the start of the file and as each is written.

    `tz` is the owner's time zone, so that "today" ends at the owner's midnight and not the machine's.
    """

    def __init__(self, journal: Journal, today: Callable[[], date] | None = None, tz: str = ""):
        self._journal = journal
        self._zone = None
        if tz:
            from zoneinfo import ZoneInfo
            self._zone = ZoneInfo(tz)
        self._today = today or (lambda: datetime.now(self._zone).date())
        self._by_day: dict[str, float] = {}
        # Money being spent right now by tasks that have not finished, each as a function giving its estimate so far.
        # A finished task has a record in the journal; a running one has none yet, and without this two tasks started
        # close together each saw the day's budget as untouched by the other.
        self.in_flight: list[Callable[[], float]] = []
        for rec in journal.read():
            self._count(rec)
        journal.listeners.append(self._count)

    def _count(self, rec: dict[str, Any]) -> None:
        usd = cost_of(rec)
        if usd:
            day = datetime.fromtimestamp(rec["ts"], self._zone).date().isoformat()
            self._by_day[day] = self._by_day.get(day, 0.0) + usd

    def add(self, usd: float) -> None:
        """Count money that has no journal record of its own. The program itself no longer calls this."""
        day = self._today().isoformat()
        self._by_day[day] = self._by_day.get(day, 0.0) + usd

    def recorded_today(self) -> float:
        return self._by_day.get(self._today().isoformat(), 0.0)

    def spent_today(self) -> float:
        """What is recorded for today, and what the tasks running now are estimated to have spent so far."""
        running = 0.0
        for estimate in self.in_flight:
            try:
                running += max(0.0, float(estimate()))
            except Exception:
                pass
        return self.recorded_today() + running


# --- when the network is down ------------------------------------------------------------------------------------------
#
# The loops that run with nobody watching (new mail at the assistant's address, the brief before a meeting, people to
# add to the database) each ask a service something every minute or so. On a laptop the network comes and goes, and
# each of them wrote an "error" every time it did. Read back, the log then said the program was failing when it was
# only waiting. So an outage is recorded as what it is: one "offline" line when it begins and one "online" line, with
# how long it lasted, when the same loop next succeeds. Anything else that goes wrong is still an error, every time.

OFFLINE_WORDS = ("connecterror", "connectionerror", "connection error", "apiconnectionerror", "timeout", "timed out", "networkerror",
                 "could not be reached", "did not answer in time", "temporary failure", "name resolution", "getaddrinfo", "network is unreachable")


def looks_offline(error: Any) -> bool:
    """Whether a failure reads like the network or a service being unreachable, and not like something being wrong."""
    said = f"{type(error).__name__ if isinstance(error, BaseException) else ''} {error}".lower()
    return any(w in said for w in OFFLINE_WORDS)


class Outage:
    def __init__(self, journal: Journal, where: str, clock: Callable[[], float] = time.time):
        self._journal, self._where, self._clock, self._since = journal, where, clock, None

    def failed(self, error: BaseException) -> None:
        if not looks_offline(error):
            self._journal.write("error", where=self._where, error=type(error).__name__, message=str(error)[:300])
        elif self._since is None:
            self._since = self._clock()
            self._journal.write("offline", where=self._where, error=type(error).__name__)

    def worked(self) -> None:
        if self._since is not None:
            self._journal.write("online", where=self._where, after_s=round(self._clock() - self._since))
            self._since = None
