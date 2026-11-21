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

Two kinds exist:

  times            when are these people all free? The answering side is code and nothing else: it asks the calendar
                   service for busy blocks, which carry no title, guest, place or reason, widens each to a quarter
                   hour, and hands them to the switchboard. The switchboard intersects everyone's and gives the
                   asking model only the stretches when all are free. So the model never sees one colleague's
                   calendar, only what is common. Studies of agents arranging meetings found the leaks in the talk
                   (a reason given for a busy slot, one person's times passed to a third); here there is no talk.

  ask              a question in words ("has anyone met this founder?"). Here a model must read the answering person's
                   records, and studies of agents that converse for their owners found them disclosing a third or more
                   of what they were told to keep back. So the model that reads never sends. It drafts, on a run that
                   can read and do nothing else: no web search, no sending, no memory, no further exchange. The
                   question reaches it as other people's writing. The answering person is shown the draft exactly as
                   it would leave, with a count by code of the addresses, links and figures in it, and it goes only on
                   their tap. On the asking side it arrives as a message in the owner's chat, recorded as other
                   people's writing and fingerprinted, so it cannot be passed on to a search or a third person unasked.
                   The asker's budget pays for the drafting.

What bounds it: ten lookups and five questions of one colleague by another in a day, a window of at most fourteen
days, a line sent to a person the first time each day a colleague's assistant looks, and a question that lapses if it
is not answered in two days.

What it does not give: a colleague can learn, by asking for narrow windows, when another is busy. That is what
free/busy is, in any shared calendar; the count and the daily line are the limit on it.
"""
from __future__ import annotations

import asyncio
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
INPUT_REQUIRED, CANCELED = "TASK_STATE_INPUT_REQUIRED", "TASK_STATE_CANCELED"      # waiting for a person's tap; lapsed
TIMES, QUESTION = "times", "ask"
DEFAULT = {TIMES: True, QUESTION: True}      # what a kind is until its owner says otherwise
LOOKUPS_A_DAY = 10               # of one colleague, by one other
QUESTIONS_A_DAY = 5
QUESTION_MAX, DRAFT_MAX = 600, 1500      # characters
LAPSES_S = 48 * 3600
MATERIAL = "a colleague's assistant"
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

    def set(self, peer_id: int, on: bool, kind: str | None = None) -> None:
        """One kind, or every kind when none is named: that is what the owner's command does."""
        for k in ([kind] if kind else list(DEFAULT)):
            self._set.setdefault(k, {})[str(peer_id)] = bool(on)
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
    # For questions in words. As the one who asks: what has been spent today and the day's limit, and where an answer
    # is put (the owner's chat, recorded as other people's writing). As the one asked: a run that reads this person's
    # records and returns (status, draft, cost), and a question to this person with two buttons.
    spent: Callable[[], float] | None = None
    daily_usd: float = 0.0
    receive: Callable[[str, str], Awaitable[Any]] | None = None
    draft: Callable[[str], Awaitable[tuple[str, str, float]]] | None = None
    ask_owner: Callable[[str, str], Awaitable[Any]] | None = None


@dataclass
class Exchange:
    kind: str
    asker: int
    answerer: int
    id: str = field(default_factory=lambda: secrets.token_hex(6))
    state: str = SUBMITTED
    why: str = ""                 # for a refusal or failure: off, limit, no_calendar, failed
    blocks: list[Block] = field(default_factory=list)
    question: str = ""
    draft: str = ""
    called: str = ""              # what the asking model called the colleague: the only name it is ever shown


_LINK = re.compile(r"(?:https?://|www\.)\S+", re.I)
_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_FIGURE = re.compile(r"(?:[$€£₹]\s?\d[\d,.]*|\b\d[\d,.]*\s?(?:k|m|mn|bn|million|billion|crore|lakh|%)(?![a-z])|\b\d{5,}\b)", re.I)


def tidy(text: str) -> str:
    """A draft as it may be shown and sent: plain, no links, cut to a length."""
    return _LINK.sub("[link removed]", " ".join(str(text).split(" ")).strip())[:DRAFT_MAX]


def contains(draft: str, question: str) -> str:
    """Counted by code, for the person deciding: what the draft holds that the question did not. Empty if nothing."""
    found = []
    for pattern, one, many in ((_ADDRESS, "email address", "email addresses"), (_FIGURE, "figure or amount", "figures or amounts")):
        new = {m.lower() for m in pattern.findall(draft)} - {m.lower() for m in pattern.findall(question)}
        if new:
            found.append(f"{len(new)} {one if len(new) == 1 else many}")
    if "[link removed]" in draft:
        found.append("a link, which I removed")
    return ", ".join(found)


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
        self._questions: dict[tuple[int, int], deque] = {}
        self._open: dict[str, Exchange] = {}                 # drafts waiting for the answering person's tap, by the buttons' id
        self._tasks: set = set()

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

    def _may_ask(self, asker: int, answerer: int, kind: str = TIMES) -> bool:
        seen = (self._asked if kind == TIMES else self._questions).setdefault((asker, answerer), deque())
        while seen and seen[0] < self._clock() - 86400:
            seen.popleft()
        if len(seen) >= (LOOKUPS_A_DAY if kind == TIMES else QUESTIONS_A_DAY):
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

    # --- a question in words --------------------------------------------------------------------------------------
    def _log(self, x: Exchange, **more: Any) -> None:
        for uid, role, peer in ((x.asker, "asker", x.answerer), (x.answerer, "answerer", x.asker)):
            desk = self._desks.get(uid)
            if desk is not None:
                desk.journal.write("exchange", id=x.id, exchange=x.kind, role=role, peer=peer, state=x.state, **({"why": x.why} if x.why else {}), **more)

    async def ask(self, asker: int, answerer: int, question: str, called: str = "") -> Exchange:
        """Put a question to a colleague's assistant. Returns at once; the drafting, the tap and the answer come later."""
        x = Exchange(QUESTION, asker, answerer, question=" ".join(str(question).split()), called=called or "the colleague")
        me, them = self._desks.get(asker), self._desks.get(answerer)
        if me is None or them is None or asker == answerer:
            x.state, x.why = REJECTED, "unknown"
            return x
        if not (them.trust.allows(asker, QUESTION) and me.trust.allows(answerer, QUESTION)):
            x.state, x.why = REJECTED, "off"
        elif them.draft is None or them.ask_owner is None or me.receive is None:
            x.state, x.why = REJECTED, "no_drafting"
        elif me.spent is not None and me.spent() >= me.daily_usd:
            x.state, x.why = REJECTED, "budget"
        elif not self._may_ask(asker, answerer, QUESTION):
            x.state, x.why = REJECTED, "limit"
        self._log(x, chars=len(x.question))
        if x.state == SUBMITTED:
            task = asyncio.get_running_loop().create_task(self._answer(x))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return x

    async def _answer(self, x: Exchange) -> None:
        me, them = self._desks[x.asker], self._desks[x.answerer]
        who = me.name or "A colleague"
        try:
            if them.tell is not None:
                await them.tell(f"{who}'s assistant asks:\n\n\"{x.question}\"\n\nI am drafting an answer from your records for you to check. "
                                "Nothing has been sent, and nothing will be unless you approve it.")
            x.state = WORKING
            status, text, cost = await them.draft(x.question)
            # The one who asked pays for the drafting: the cost is written to their log, which is what their budget is counted from.
            me.journal.write("tool", name="colleague_ask", ok=status == "done", cost_usd=round(float(cost or 0), 5), exchange=x.id)
            x.draft = tidy(text)
            if status != "done" or not x.draft:
                raise RuntimeError(f"drafting ended as {status}")
            x.state = INPUT_REQUIRED
            self._open["x" + x.id] = x
            holds = contains(x.draft, x.question)
            await them.ask_owner(f"Send this answer to {who}'s assistant?\n\nThey asked: \"{x.question}\"\n\nMy draft, from your records:\n\n{x.draft}\n\n"
                                 + (f"It contains: {holds}. " if holds else "")
                                 + "It goes exactly as shown, to them only. Decline sends nothing, and they are told you chose not to answer this way.",
                                 "x" + x.id)
            self._log(x, draft_chars=len(x.draft))
            asyncio.get_running_loop().call_later(LAPSES_S, lambda: self._tasks.add(asyncio.ensure_future(self._lapse(x))))
        except Exception as e:
            self._open.pop("x" + x.id, None)
            x.state, x.why = FAILED, "failed"
            them.journal.write("error", where="peers.answer", error=type(e).__name__, message=str(e)[:200])
            self._log(x)
            await self._deliver(x, f"{x.called}'s assistant could not draft an answer to your question \"{x.question}\". Nothing was sent to you. Ask them directly.")
            if them.tell is not None:
                try:
                    await them.tell(f"I could not draft an answer to {who}'s assistant. Nothing was sent.")
                except Exception:
                    pass

    async def _deliver(self, x: Exchange, text: str, private: str = "") -> None:
        me = self._desks.get(x.asker)
        try:
            if me is not None and me.receive is not None:
                await me.receive(text, private)
        except Exception as e:
            me.journal.write("error", where="peers.deliver", error=type(e).__name__, message=str(e)[:200])

    async def _lapse(self, x: Exchange) -> None:
        if self._open.pop("x" + x.id, None) is None:
            return
        x.state, x.why = CANCELED, "lapsed"
        self._log(x)
        await self._deliver(x, f"Your question to {x.called}'s assistant (\"{x.question}\") was not answered in two days and has lapsed.")

    async def decide(self, user_id: int, approval_id: str, approved: bool) -> str | None:
        """The answering person tapped. None if these buttons are not a draft's; otherwise the line to show under them."""
        x = self._open.get(approval_id)
        if x is None:
            return "This draft is no longer waiting: it lapsed, or the program restarted. Nothing was sent." if str(approval_id).startswith("x") else None
        if x.answerer != user_id:
            return ""
        del self._open[approval_id]
        if approved:
            x.state = COMPLETED
            self._log(x, sent_chars=len(x.draft))
            await self._deliver(x, f"{x.called}'s assistant answered your question \"{x.question}\". They read and approved this:\n\n{x.draft}", x.draft)
            return "Sent, exactly as shown."
        x.state, x.why = REJECTED, "declined"
        self._log(x)
        await self._deliver(x, f"{x.called} chose not to answer your question \"{x.question}\" through the assistant. Ask them directly.")
        return "Not sent. They are told you chose not to answer this way."

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
            me.journal.write("trust", peer=found[0], on=on)
            who = self._desks[found[0]].name or f"id {found[0]}"
            return (f"On. {who}'s assistant and mine can look up each other's free times and put questions to each other again, if they allow it too." if on
                    else f"Off. {who}'s assistant can no longer look up when you are free or put questions to mine, and mine will not ask theirs.")
        lines = []
        for d in others:
            mine, theirs = me.trust.allows(d.user_id), d.trust.allows(user_id)
            state = "on" if mine and theirs else "off (you turned it off)" if not mine else "off (they turned it off)"
            lines.append(f"- {d.name or 'no name given'} (id {d.user_id}): {state}")
        return ("What a colleague's assistant and mine may ask each other. Two things: when you are free, given as busy and free "
                "times only, never what a meeting is or who it is with; and a question in words, where I draft an answer from your "
                "records and nothing is sent unless you approve the exact text.\n\n" + "\n".join(lines)
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

        async def colleague_ask(args: dict[str, Any]) -> dict[str, Any]:
            me = self._desks.get(user_id)
            name, question = str(args.get("person") or "").strip(), " ".join(str(args.get("question") or "").split())
            if me is None or not name or len(question) < 8:
                return _result("Give the colleague's name as the owner said it, and the question in a full sentence.", True)
            if len(question) > QUESTION_MAX:
                return _result(f"Too long: {len(question)} characters, and the limit is {QUESTION_MAX}. Ask one thing, briefly.", True)
            found = self.find(user_id, name)
            if len(found) != 1:
                return _result(f"{name}: " + ("more than one person here has that name; ask the owner which." if found else
                                              "nobody who uses this assistant matches that name. They may be someone to email instead."), True)
            x = await self.ask(user_id, found[0], question, called=name)
            if x.state != SUBMITTED:
                why = {"off": "questions between your assistants are turned off (/trust)", "limit": "they have been asked too often today",
                       "budget": "today's budget is used up", "no_drafting": "their assistant cannot draft answers here"}
                return _result(f"Not sent: {why.get(x.why, 'not available')}.", True)
            return _result(f"Put to {name}'s assistant. It drafts an answer from {name}'s records, and {name} decides whether that draft is sent, "
                           "so the answer may take a while, or never come. If it comes it arrives in this chat by itself, as a message "
                           "to the owner. Tell the owner the question was put and that the colleague has to approve the answer. Do not wait, "
                           "and do not ask again.")
        return {"times_find": times_find, "colleague_ask": colleague_ask}


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
    "colleague_ask": ("Put one question to the assistant of a colleague who also uses this assistant, when the owner asks you to ask a "
                      "colleague something their records would answer: whether they have met a person or company, what was agreed in a "
                      "meeting they were in. The colleague's assistant drafts an answer and the colleague approves it before anything "
                      "comes back, so the answer arrives later, as its own message. Ask one specific question. Do not put in it "
                      "anything from the owner's mail or files that the question does not need.",
                      {"type": "object",
                       "properties": {"person": {"type": "string", "description": "The colleague's name, as the owner said it."},
                                      "question": {"type": "string", "description": "One question, in a full sentence, at most 600 characters."}},
                       "required": ["person", "question"]}),
}
