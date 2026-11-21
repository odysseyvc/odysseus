"""One person's assistant asking a colleague's, inside one running copy.

Until this, nothing passed between two people's assistants (people.py). Here is the one way across, and what it carries:

  the switchboard  one per process. It holds a desk for each person: what code may do on that person's behalf when a
                   colleague's assistant asks. An assistant reaches another only through it. A colleague is found by
                   the register of who is here, from a name the owner used; no model supplies an id, and no stored
                   name is shown to a model.
  an exchange      one request and its answer, with the names a standard for agents of different firms uses for a
                   task and its states (A2A), so that an adapter for other firms can be added later without a change
                   here. Both sides write it to their own log: who, what kind, how it ended, and never the content.
  trust            a list each person owns (trust.json in their folder): for each colleague and each kind, yes or no.
                   Either side saying no stops it both ways. /trust shows it and changes it; no model can.

One kind exists so far:

  times            when are these people all free? The answering side is code and nothing else: it asks the calendar
                   service for busy blocks, which carry no title, guest, place or reason, widens each to a quarter
                   hour, and hands them to the switchboard. The switchboard intersects everyone's and gives the
                   asking model only the stretches when all are free. So the model never sees one colleague's
                   calendar, only what is common. Studies of agents arranging meetings found the leaks in the talk
                   (a reason given for a busy slot, one person's times passed to a third); here there is no talk.

What bounds it: ten lookups of one colleague by another in a day, a window of at most fourteen days, and a line sent
to a person the first time each day a colleague's assistant looks, saying who and what was given.

What it does not give: a colleague can learn, by asking for narrow windows, when another is busy. That is what
free/busy is, in any shared calendar; the count and the daily line are the limit on it.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from .journal import Journal

# The states of a task in the A2A standard, by its names.
SUBMITTED, WORKING, COMPLETED, FAILED, REJECTED = ("TASK_STATE_SUBMITTED", "TASK_STATE_WORKING", "TASK_STATE_COMPLETED",
                                                    "TASK_STATE_FAILED", "TASK_STATE_REJECTED")
TIMES = "times"
DEFAULT = {TIMES: True}          # what a kind is until its owner says otherwise
LOOKUPS_A_DAY = 10               # of one colleague, by one other
WINDOW_DAYS = 14
QUARTER = timedelta(minutes=15)
MOST_PEOPLE = 8
MOST_LINES = 14

Block = tuple[datetime, datetime]        # a stretch of time, both ends with a zone


class Trust:
    """What each colleague's assistant may ask of this person's. Only what differs from the default is written down."""

    def __init__(self, path: Path | None = None):
        self._path, self._set = Path(path) if path else None, {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8")) if self._path else {}
            self._set = {str(k): {str(i): bool(v) for i, v in d.items()} for k, d in raw.items() if isinstance(d, dict)}
        except (OSError, ValueError, AttributeError):
            pass

    def allows(self, peer_id: int, kind: str = TIMES) -> bool:
        return self._set.get(kind, {}).get(str(peer_id), DEFAULT.get(kind, False))

    def set(self, peer_id: int, on: bool, kind: str = TIMES) -> None:
        self._set.setdefault(kind, {})[str(peer_id)] = bool(on)
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._set, indent=1), encoding="utf-8")


@dataclass
class Desk:
    """What code may do for one person when a colleague's assistant asks."""
    user_id: int
    name: str
    trust: Trust
    journal: Journal
    busy: Callable[[datetime, datetime], Awaitable[list[Block]]] | None = None     # the calendar's busy blocks; None: no calendar
    tell: Callable[[str], Awaitable[Any]] | None = None                             # a line to this person's own chat


@dataclass
class Exchange:
    kind: str
    asker: int
    answerer: int
    id: str = field(default_factory=lambda: secrets.token_hex(6))
    state: str = SUBMITTED
    why: str = ""                 # for a refusal or failure: off, limit, no_calendar, failed
    blocks: list[Block] = field(default_factory=list)


def widen(blocks: list[Block], start: datetime, end: datetime) -> list[Block]:
    """Busy blocks cut to the window, each widened to the quarter hour, merged. What leaves the answering side."""
    out: list[Block] = []
    for s, e in sorted(blocks):
        s, e = max(s, start), min(e, end)
        if e <= s:
            continue
        s = s - timedelta(minutes=s.minute % 15, seconds=s.second, microseconds=s.microsecond)
        over = timedelta(minutes=e.minute % 15, seconds=e.second, microseconds=e.microsecond)
        e = e + (QUARTER - over if over else timedelta(0))
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def free_stretches(busy: list[Block], start: datetime, end: datetime, zone: Any, minutes: int,
                   earliest: clock_time, latest: clock_time, weekends: bool) -> list[Block]:
    """The stretches of at least `minutes`, inside the working hours of each day of the window, with nothing busy in them."""
    busy = widen(busy, start, end)
    out: list[Block] = []
    day = start.astimezone(zone).date()
    while day <= end.astimezone(zone).date():
        if weekends or day.weekday() < 5:
            s = max(datetime.combine(day, earliest, zone), start)
            day_end = min(datetime.combine(day, latest, zone), end)
            for b_start, b_end in busy:
                if b_end <= s or b_start >= day_end:
                    continue
                if b_start - s >= timedelta(minutes=minutes):
                    out.append((s, b_start))
                s = max(s, b_end)
            if day_end - s >= timedelta(minutes=minutes):
                out.append((s, day_end))
        day += timedelta(days=1)
    return out


class Switchboard:
    def __init__(self, clock: Callable[[], float] = time.time):
        self._desks: dict[int, Desk] = {}
        self._clock = clock
        self._asked: dict[tuple[int, int], deque] = {}       # (asker, answerer): when, in the last day
        self._told: dict[tuple[int, int], int] = {}          # (answerer, asker): the day the answerer was last told

    def register(self, desk: Desk) -> None:
        self._desks[desk.user_id] = desk

    def remove(self, user_id: int) -> None:
        self._desks.pop(user_id, None)

    def desk(self, user_id: int) -> Desk | None:
        return self._desks.get(user_id)

    def colleagues(self, user_id: int) -> list[Desk]:
        return [d for i, d in self._desks.items() if i != user_id]

    def find(self, asker: int, word: str, by_id: bool = False) -> list[int]:
        """The colleagues a name could mean: the whole name, or one word of it. By id only for a command the owner typed."""
        want = " ".join(str(word or "").lower().split())
        if not want:
            return []
        if by_id and want.isdigit():
            return [d.user_id for d in self.colleagues(asker) if d.user_id == int(want)]
        whole = [d.user_id for d in self.colleagues(asker) if d.name.lower() == want]
        return whole or [d.user_id for d in self.colleagues(asker) if want in d.name.lower().split()]

    def _may_ask(self, asker: int, answerer: int) -> bool:
        seen = self._asked.setdefault((asker, answerer), deque())
        while seen and seen[0] < self._clock() - 86400:
            seen.popleft()
        if len(seen) >= LOOKUPS_A_DAY:
            return False
        seen.append(self._clock())
        return True

    async def times(self, asker: int, answerer: int, start: datetime, end: datetime) -> Exchange:
        """One colleague's busy blocks in a window, if both sides allow it. Code on both sides; no model."""
        x = Exchange(TIMES, asker, answerer)
        me, them = self._desks.get(asker), self._desks.get(answerer)
        if me is None or them is None or asker == answerer:
            x.state, x.why = REJECTED, "unknown"
            return x
        if not (them.trust.allows(asker, TIMES) and me.trust.allows(answerer, TIMES)):
            x.state, x.why = REJECTED, "off"
        elif them.busy is None:
            x.state, x.why = REJECTED, "no_calendar"
        elif not self._may_ask(asker, answerer):
            x.state, x.why = REJECTED, "limit"
        else:
            x.state = WORKING
            try:
                x.blocks = widen(await them.busy(start, end), start, end)
                x.state = COMPLETED
            except Exception as e:
                x.state, x.why = FAILED, "failed"
                them.journal.write("error", where="peers.busy", error=type(e).__name__, message=str(e)[:200])
        for desk, role, peer in ((me, "asker", answerer), (them, "answerer", asker)):
            desk.journal.write("exchange", id=x.id, exchange=TIMES, role=role, peer=peer, state=x.state,
                               **({"why": x.why} if x.why else {}), **({"blocks": len(x.blocks)} if x.state == COMPLETED else {}))
        today = int(self._clock() // 86400)
        if x.state == COMPLETED and them.tell is not None and self._told.get((answerer, asker)) != today:
            self._told[(answerer, asker)] = today
            try:
                await them.tell(f"{me.name or 'A colleague'}'s assistant looked up when you are free. It was given busy and free times only, "
                                "not what your meetings are or who they are with. I will not mention further lookups by them today. "
                                "/trust shows who can do this and turns it off.")
            except Exception as e:
                them.journal.write("error", where="peers.tell", error=type(e).__name__)
        return x

    # --- the command the owner types: no model ------------------------------------------------------------------
    def trust_command(self, user_id: int, text: str) -> str:
        me = self._desks.get(user_id)
        others = self.colleagues(user_id)
        if me is None or not others:
            return "Nobody else uses this copy yet, so there is no other assistant for mine to talk to."
        words = text.split()
        if len(words) >= 3 and words[1].lower() in ("on", "off"):
            found = self.find(user_id, " ".join(words[2:]), by_id=True)
            if len(found) != 1:
                return ("More than one person here matches that. Use the id from /trust." if found
                        else "Nobody here matches that. Send /trust to see who is here.")
            on = words[1].lower() == "on"
            me.trust.set(found[0], on)
            me.journal.write("trust", peer=found[0], exchange=TIMES, on=on)
            who = self._desks[found[0]].name or f"id {found[0]}"
            return (f"On. {who}'s assistant and mine can look up each other's free times again, if they allow it too." if on
                    else f"Off. {who}'s assistant can no longer look up when you are free, and mine will not look up theirs.")
        lines = []
        for d in others:
            mine, theirs = me.trust.allows(d.user_id), d.trust.allows(user_id)
            state = "on" if mine and theirs else "off (you turned it off)" if not mine else "off (they turned it off)"
            lines.append(f"- {d.name or 'no name given'} (id {d.user_id}): {state}")
        return ("What a colleague's assistant and mine may ask each other. One kind exists: when you are free, given as busy and "
                "free times only, never what a meeting is or who it is with.\n\n" + "\n".join(lines)
                + "\n\nTo change one, send /trust off or /trust on followed by a name or id. Off stops it in both directions.")

    # --- the tool the asking model has ----------------------------------------------------------------------------
    def handlers(self, user_id: int, now: Callable[[], datetime], tz: str = "") -> dict[str, Any]:
        zone = ZoneInfo(tz) if tz else datetime.now().astimezone().tzinfo

        async def times_find(args: dict[str, Any]) -> dict[str, Any]:
            me = self._desks.get(user_id)
            names = [str(n).strip() for n in (args.get("people") or []) if str(n).strip()][:MOST_PEOPLE]
            if me is None or me.busy is None:
                return _result("Your own calendar is not connected, so common free times cannot be worked out. /apps shows how to connect it.", True)
            if not names:
                return _result("Name the colleagues to include, as the owner named them.", True)
            try:
                minutes = max(15, min(int(args.get("minutes") or 30), 480))
                days = max(1, min(int(args.get("days") or 7), WINDOW_DAYS))
                first = datetime.strptime(str(args["start"]), "%Y-%m-%d").date() if args.get("start") else now().date()
                earliest, latest = (datetime.strptime(str(args.get(k) or d), "%H:%M").time() for k, d in (("earliest", "09:00"), ("latest", "18:00")))
            except (ValueError, TypeError):
                return _result("`start` is a date as YYYY-MM-DD, `earliest` and `latest` are times as HH:MM, `minutes` and `days` are numbers.", True)
            if latest <= earliest:
                return _result("`latest` must be after `earliest`.", True)
            right_now = now().replace(tzinfo=zone)
            right_now += (QUARTER - timedelta(minutes=right_now.minute % 15, seconds=right_now.second, microseconds=right_now.microsecond)) % QUARTER
            start = max(datetime.combine(first, clock_time(0), zone), right_now)
            end = datetime.combine(first + timedelta(days=days), clock_time(0), zone)
            if end <= start:
                return _result("That window is already over.", True)
            included, left_out, ids = [], [], []
            for name in names:
                found = self.find(user_id, name)
                if len(found) != 1:
                    left_out.append(f"{name}: " + ("more than one person here has that name; ask the owner which" if found else
                                                   "nobody who uses this assistant matches that name; they may be someone to email instead"))
                elif found[0] not in ids:
                    ids.append(found[0])
                    included.append(name)
            try:
                busy = list(await me.busy(start, end))
            except Exception as e:
                me.journal.write("tool", name="times_find", ok=False, error=type(e).__name__)
                return _result(f"Your own calendar could not be read ({type(e).__name__}).", True)
            why = {"off": "looking up free times is turned off between you (/trust)", "no_calendar": "they have not connected a calendar",
                   "limit": "they have been looked up too often today", "failed": "their calendar could not be read just now", "unknown": "not here"}
            for name, peer in zip(list(included), ids):
                x = await self.times(user_id, peer, start, end)
                if x.state == COMPLETED:
                    busy += x.blocks
                else:
                    included.remove(name)
                    left_out.append(f"{name}: {why.get(x.why, 'not available')}")
            me.journal.write("tool", name="times_find", ok=bool(included), people=len(included), left_out=len(left_out))
            if not included:
                return _result("Nobody could be included.\n" + "\n".join(left_out), True)
            free = free_stretches(busy, start, end, zone, minutes, earliest, latest, bool(args.get("weekends")))
            by_day: dict[Any, list[str]] = {}
            for s, e in free:
                s, e = s.astimezone(zone), e.astimezone(zone)
                by_day.setdefault(s.date(), []).append(f"{s:%H:%M} to {e:%H:%M}")
            lines = [f"{day:%a %d %b}: " + "; ".join(spans) for day, spans in list(by_day.items())[:MOST_LINES]]
            text = (f"When the owner and {', '.join(included)} are all free for {minutes} minutes or more, between {earliest:%H:%M} and {latest:%H:%M}"
                    f"{' (' + tz + ' time)' if tz else ''}, from {start:%a %d %b} for {days} day{'s' if days != 1 else ''}:\n"
                    + ("\n".join(lines) if lines else "No time when everyone is free in that window. Try a longer window or a shorter meeting."))
            if left_out:
                text += "\n\nNot included:\n" + "\n".join(left_out)
            text += ("\n\nThis is all that is known: common free times, worked out by code from busy blocks. Nothing about what anyone's "
                     "meetings are was read. Nothing is booked; to book, create the event with the guests as usual.")
            return _result(text)
        return {"times_find": times_find}


def _result(text: str, failed: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], **({"is_error": True} if failed else {})}


def parse_busy(data: Any) -> list[Block]:
    """The busy blocks in a free/busy answer from the calendar service, with their zones."""
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    out: list[Block] = []
    for cal in ((data or {}).get("calendars") or {}).values() if isinstance(data, dict) else ():
        for b in (cal or {}).get("busy") or []:
            try:
                s, e = (datetime.fromisoformat(re.sub(r"Z$", "+00:00", str(b[k]))) for k in ("start", "end"))
            except (KeyError, ValueError, TypeError):
                continue
            out.append((s if s.tzinfo else s.replace(tzinfo=timezone.utc), e if e.tzinfo else e.replace(tzinfo=timezone.utc)))
    return out


SCHEMAS = {
    "times_find": ("Find when the owner and colleagues who also use this assistant are all free. Use it when the owner asks for a time "
                   "with people at their own firm. It returns common free times only, worked out by code; it does not show anyone's "
                   "calendar and it books nothing. Give each colleague's name as the owner said it. People outside the firm are not "
                   "reachable this way: for them, check the owner's calendar and write to the person.",
                   {"type": "object",
                    "properties": {"people": {"type": "array", "items": {"type": "string"}, "description": "Colleagues' names, as the owner said them."},
                                   "minutes": {"type": "integer", "description": "Length of the meeting. Default 30."},
                                   "start": {"type": "string", "description": "First day to look at, YYYY-MM-DD. Default today."},
                                   "days": {"type": "integer", "description": "How many days to look at, 1 to 14. Default 7."},
                                   "earliest": {"type": "string", "description": "Earliest time of day, HH:MM. Default 09:00."},
                                   "latest": {"type": "string", "description": "Latest end time of day, HH:MM. Default 18:00."},
                                   "weekends": {"type": "boolean", "description": "Include Saturday and Sunday. Default false."}},
                    "required": ["people"]}),
}
