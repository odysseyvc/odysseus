"""People to keep in touch with, and the reminding that makes it happen.

A firm keeps a short list of people worth speaking to every month or two: the founders it rates most, the people who
tend to know who is good. The list is easy to write and the speaking is easy to forget. This module keeps the list and
does the reminding. No model is involved in any of it.

  the list       each entry is a person, how often to speak to them, what to ask, and whose job it is: the owner's, or a
                 colleague's (a name and an address).
  the owner's    when one is due the owner gets one message with three buttons: done, in a week, skip this round. If
                 nothing is tapped it is asked again after a few days. After "done" the owner is asked what came of it,
                 and whatever they say next is kept with the entry.
  a colleague's  when one is due, code sends the colleague a fixed email from the owner's mailbox, and again every few
                 days, until they reply. The reply is the confirmation: its words are kept with the entry, noted on the
                 person in the firm's database if their address is known, and passed to the owner. After a set number of
                 reminders with no reply the owner is told, and the entry waits for its next round.

The owner approves a colleague's entry once, when it is set up, and is shown then the exact email that will be sent.
After that nothing in the email can change: its words are the template below and the entry's own name and question.
A colleague's reply is other people's writing. It is quoted, never obeyed, and no model reads it here.
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from .journal import Journal

MAX_PEOPLE = 200
EVERY_MIN, EVERY_MAX, EVERY_DEFAULT = 7, 365, 60
NUDGE_DAYS = 3              # how long before the same thing is asked again
MAX_REMINDERS = 5           # emails to a colleague in one round before the owner is told instead
MAX_PER_DAY = 3             # questions put to the owner in one day; the rest come the next day
AWAIT_MIN = 30              # after "done", how long the owner's next message is taken to be about it
RESULT_MAX = 1500
CHECK_EVERY_S = 1800
REPLY_CHECK_EVERY = timedelta(hours=2)
OPTIONS = ["Done", "Remind me in a week", "Skip this round"]
SUBJECT = "Check-in due: {name}"
_ADDRESS = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_QUOTED = re.compile(r"(?im)^(on .{0,200}wrote:|-{2,} ?original message.*|from: .+|>.*)$")


class CheckinError(Exception):
    """A refusal the model should be told about."""


def _clean(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def reminder_mail(item: dict[str, Any], nth: int = 1) -> tuple[str, str]:
    """The email a colleague is sent. Fixed words, the entry's name and its question, and nothing else."""
    first = (item.get("assignee") or "").split(" ")[0]
    body = (f"Hi{' ' + first if first else ''},\n\n"
            f"It is time for the regular check-in with {item['name']}"
            + (f" (every {item['every']} days)" if nth == 1 else f". This is reminder {nth}") + ".\n"
            + (f"\nWhat to ask: {item['about']}\n" if item.get("about") else "")
            + "\nOnce you have spoken, reply to this email with what came of it. Your reply is recorded against the check-in and "
              "the reminders stop.\n\n"
              f"This is an automatic reminder. It is sent again every {NUDGE_DAYS} days until you reply, {MAX_REMINDERS} times at most.")
    return SUBJECT.format(name=item["name"]), body


def describe_add(a: dict[str, Any], today: date) -> str:
    """The question put to the owner before a colleague's check-in is set up: who, how often, and the email itself."""
    item = _entry(a, today)
    subject, body = reminder_mail(item)
    return (f"Set up this check-in?\n\nWith: {item['name']}\nEvery: {item['every']} days, first on {date.fromisoformat(item['due']):%a %d %b %Y}\n"
            f"Whose job: {item['assignee'] or item['assignee_email']} <{item['assignee_email']}>\n\n"
            f"Each time it is due I will send this from your mailbox to {item['assignee_email']}, without asking you again, and repeat it "
            f"every {NUDGE_DAYS} days until they reply ({MAX_REMINDERS} times at most):\n\nSubject: {subject}\n\n{body}")


def _entry(a: dict[str, Any], today: date) -> dict[str, Any]:
    name = _clean(a.get("name"), 80)
    if not name:
        raise CheckinError("Say who the check-in is with.")
    try:
        every = int(a.get("every_days") or EVERY_DEFAULT)
    except (TypeError, ValueError):
        raise CheckinError("every_days must be a number of days.") from None
    if not EVERY_MIN <= every <= EVERY_MAX:
        raise CheckinError(f"every_days must be between {EVERY_MIN} and {EVERY_MAX}.")
    email, assignee_email = _clean(a.get("email"), 120).lower(), _clean(a.get("assignee_email"), 120).lower()
    for address in (email, assignee_email):
        if address and not _ADDRESS.match(address):
            raise CheckinError(f"{address} is not an email address.")
    first = _clean(a.get("first_due"), 10)
    try:
        due = date.fromisoformat(first) if first else today + timedelta(days=every)
    except ValueError:
        raise CheckinError("first_due must be a date like 2026-12-01.") from None
    if due < today:
        raise CheckinError("first_due is in the past.")
    return {"name": name, "email": email, "about": _clean(a.get("about"), 300), "every": every, "assignee": _clean(a.get("assignee"), 60),
            "assignee_email": assignee_email, "due": due.isoformat(), "done": "", "asked": "", "first_asked": "", "asks": 0, "await_until": "",
            "results": []}


def reply_text(body: Any) -> str:
    """What the colleague wrote, without the reminder quoted beneath it."""
    text = str(body or "").replace("\r\n", "\n")
    m = _QUOTED.search(text)
    return _clean(text[:m.start()] if m else text, RESULT_MAX)


class Checkins:
    def __init__(self, path: Path, today: Callable[[], date] = date.today):
        self._path, self._today = Path(path), today
        self._pending: list[str] = []
        try:
            rows = json.loads(self._path.read_text(encoding="utf-8"))
            self._rows: list[dict[str, Any]] = [r for r in rows if isinstance(r, dict) and r.get("id") and r.get("name")]
        except (OSError, ValueError):
            self._rows = []

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._rows, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._path)

    def all(self) -> list[dict[str, Any]]:
        return sorted(self._rows, key=lambda r: r["due"])

    def get(self, ref: str) -> dict[str, Any] | None:
        """By id, or by name: the whole name, or a part of it that fits one entry only."""
        ref = _clean(ref, 80).lower()
        if not ref:
            return None
        for r in self._rows:
            if r["id"] == ref or r["name"].lower() == ref:
                return r
        part = [r for r in self._rows if ref in r["name"].lower()]
        return part[0] if len(part) == 1 else None

    def add(self, a: dict[str, Any]) -> dict[str, Any]:
        item = _entry(a, self._today())
        if len(self._rows) >= MAX_PEOPLE:
            raise CheckinError(f"The list is full ({MAX_PEOPLE}). Remove one first.")
        if any(r["name"].lower() == item["name"].lower() for r in self._rows):
            raise CheckinError(f"There is already a check-in with {item['name']}. Remove it first to change it.")
        item = {"id": secrets.token_hex(3), **item}
        self._rows.append(item)
        self.save()
        self._pending.append(f"check-in with {item['name']} every {item['every']} days, first on {date.fromisoformat(item['due']):%d %b %Y}"
                             + (f", reminders to {item['assignee_email']}" if item["assignee_email"] else ""))
        return item

    def remove(self, ref: str) -> dict[str, Any] | None:
        item = self.get(ref)
        if item is not None:
            self._rows.remove(item)
            self.save()
            self._pending.append(f"removed the check-in with {item['name']}")
        return item

    def _next_round(self, item: dict[str, Any], from_day: date) -> None:
        item.update(due=(from_day + timedelta(days=item["every"])).isoformat(), asked="", first_asked="", asks=0)

    def done(self, item: dict[str, Any], result: str = "", by: str = "") -> None:
        """It happened. The next one is due a full interval from today, and what came of it is kept."""
        today = self._today()
        item["done"] = today.isoformat()
        self._next_round(item, today)
        self.result(item, result, by)

    def result(self, item: dict[str, Any], text: str, by: str = "") -> None:
        text = _clean(text, RESULT_MAX)
        if text:
            item["results"] = (item.get("results") or [])[-9:] + [{"on": self._today().isoformat(), "by": by[:120], "text": text}]
            item["await_until"] = ""
        self.save()

    def later(self, item: dict[str, Any], days: int = 7) -> None:
        item.update(due=(self._today() + timedelta(days=days)).isoformat(), asked="", first_asked="", asks=0)
        self.save()

    def skip(self, item: dict[str, Any]) -> None:
        self._next_round(item, self._today())
        self.save()

    def awaiting(self, now: datetime) -> dict[str, Any] | None:
        """The entry the owner has just marked done and not yet said anything about, if there is one."""
        live = [r for r in self._rows if r.get("await_until") and r["await_until"] >= now.isoformat(timespec="seconds")]
        return max(live, key=lambda r: r["await_until"]) if live else None

    def line(self, r: dict[str, Any]) -> str:
        last = (r.get("results") or [{}])[-1]
        return (f"{r['id']}  {r['name']}, every {r['every']} days, next {date.fromisoformat(r['due']):%d %b %Y}"
                + (f", {r['assignee'] or r['assignee_email']}'s job" if r["assignee_email"] else "")
                + (f". Ask: {r['about'].rstrip('.')}" if r.get("about") else "")
                + (f". Last, {date.fromisoformat(last['on']):%d %b}: {last['text'][:200]}" if last.get("text") else
                   f". Last done {date.fromisoformat(r['done']):%d %b %Y}" if r.get("done") else ""))

    def listing(self) -> str:
        return "\n".join(self.line(r) for r in self.all()) if self._rows else "(nobody on the list)"

    def pop_changes(self) -> list[str]:
        out, self._pending = self._pending, []
        return out

    def tap(self, offer_id: str, index: int, now: datetime) -> str:
        """The owner tapped a button under a check-in. The id on the button names the entry and the round, so a tap on an
        old message does nothing, and a restart of the program in between loses nothing."""
        item = next((r for r in self._rows if offer_id == offer_for(r)), None)
        if item is None:
            return "This check-in is no longer open."
        if index == 0:
            self.done(item)
            item["await_until"] = (now + timedelta(minutes=AWAIT_MIN)).isoformat(timespec="seconds")
            self.save()
            return f"Done. Next on {date.fromisoformat(item['due']):%d %b %Y}. Tell me what came of it and I'll keep it with the check-in."
        if index == 1:
            self.later(item)
            return f"I'll ask again on {date.fromisoformat(item['due']):%a %d %b}."
        self.skip(item)
        return f"Skipped. Next on {date.fromisoformat(item['due']):%d %b %Y}."


def offer_for(item: dict[str, Any]) -> str:
    return "k" + item["id"] + item["due"].replace("-", "")


def is_offer(offer_id: str) -> bool:
    return bool(re.fullmatch(r"k[0-9a-f]{6}\d{8}", offer_id or ""))


class CheckinLoop:
    """Each working day, at or after the set time: ask about what is due, remind colleagues, and read their replies."""

    def __init__(self, store: Checkins, chat: Any, journal: Journal, now: Callable[[], datetime] = datetime.now, at: str = "09:30",
                 send_mail: Callable[[str, str, str], Awaitable[None]] | None = None,
                 messages: Callable[[str, int], Awaitable[list[dict[str, Any]]]] | None = None,
                 note: Callable[[dict[str, Any], str], Awaitable[bool]] | None = None, on_delivered: Callable[[str], None] | None = None):
        self._store, self._chat, self._journal, self._now, self._at = store, chat, journal, now, at
        self._send_mail, self._messages, self._note, self._on_delivered = send_mail, messages, note, on_delivered or (lambda t: None)
        self._asked_on: tuple[str, int] = ("", 0)
        self._checked: dict[str, datetime] = {}

    async def run_forever(self, every_s: float = CHECK_EVERY_S) -> None:
        while True:
            try:
                await self.tick()
            except Exception as e:      # the loop must outlive a mailbox that cannot be reached
                self._journal.write("error", where="checkins.tick", error=type(e).__name__, message=str(e)[:300])
            await asyncio.sleep(every_s)

    async def _tell(self, text: str) -> None:
        await self._chat.send(text)
        self._on_delivered(text)

    async def _reply(self, item: dict[str, Any], now: datetime) -> str:
        """The colleague's reply to this round's reminder, if one has come. Reads only."""
        if self._messages is None or now - self._checked.get(item["id"], datetime.min) < REPLY_CHECK_EVERY:
            return ""
        self._checked[item["id"]] = now
        since = date.fromisoformat(item["first_asked"] or item["asked"])
        days = max(1, (now.date() - since).days + 1)
        query = f'from:{item["assignee_email"]} subject:"{SUBJECT.format(name=item["name"])}" newer_than:{days}d'
        from .capture import _sender, _when
        best: tuple[datetime, str] | None = None
        for m in await self._messages(query, 5):
            at = _when(m.get("messageTimestamp") or m.get("date"), None)
            if _sender(m.get("sender") or m.get("from"))[1] != item["assignee_email"] or at is None or at.date() < since:
                continue        # the search is the service's; who wrote it and when is checked here
            preview = m.get("preview")
            text = reply_text(m.get("messageText") or m.get("message_text") or (preview.get("body") if isinstance(preview, dict) else preview))
            if text and (best is None or at > best[0]):
                best = (at, text)
        return best[1] if best else ""

    async def tick(self) -> dict[str, int]:
        """Returns what was done, as counts."""
        now = self._now()
        out = {"asked": 0, "mailed": 0, "replies": 0, "stalled": 0}
        if now.weekday() >= 5 or now.strftime("%H:%M") < self._at:
            return out
        today = now.date()
        if self._asked_on[0] != today.isoformat():
            self._asked_on = (today.isoformat(), 0)
        for item in self._store.all():
            if date.fromisoformat(item["due"]) > today:
                continue
            waited = (today - date.fromisoformat(item["asked"])).days if item.get("asked") else None
            if item["assignee_email"]:
                if self._send_mail is None:
                    continue
                who = item["assignee"] or item["assignee_email"]
                if item["asks"]:
                    said = await self._reply(item, now)
                    if said:
                        self._store.done(item, said, by=item["assignee_email"])
                        noted = False
                        if self._note is not None:
                            try:
                                noted = await self._note(item, f"Check-in by {who}, {today:%d %b %Y}. They wrote:\n\n{said}")
                            except Exception as e:
                                self._journal.write("error", where="checkins.note", error=type(e).__name__)
                        await self._tell(f"{who} has checked in with {item['name']} and wrote:\n\n\"{said}\"\n\n"
                                         + ("Noted on their record in Attio. " if noted else "")
                                         + f"Next on {date.fromisoformat(item['due']):%d %b %Y}.")
                        out["replies"] += 1
                        continue
                if waited is not None and waited < NUDGE_DAYS:
                    continue
                if item["asks"] >= MAX_REMINDERS:
                    self._store.skip(item)
                    await self._tell(f"{who} has not replied to {MAX_REMINDERS} reminders about the check-in with {item['name']}. "
                                     f"I've stopped for this round; the next is on {date.fromisoformat(item['due']):%d %b %Y}.")
                    out["stalled"] += 1
                    continue
                # Recorded before it is sent: a crash in between loses one reminder, and never sends the same one twice.
                item.update(asks=item["asks"] + 1, asked=today.isoformat(), first_asked=item.get("first_asked") or today.isoformat())
                self._store.save()
                subject, body = reminder_mail(item, item["asks"])
                await self._send_mail(item["assignee_email"], subject, body)
                out["mailed"] += 1
            else:
                if (waited is not None and waited < NUDGE_DAYS) or self._asked_on[1] >= MAX_PER_DAY:
                    continue
                last = (item.get("results") or [{}])[-1]
                text = (f"Time to check in with {item['name']} (every {item['every']} days)."
                        + (f"\nAsk: {item['about']}" if item.get("about") else "")
                        + (f"\nLast time, {date.fromisoformat(last['on']):%d %b}: {last['text'][:300]}" if last.get("text") else ""))
                item.update(asks=item["asks"] + 1, asked=today.isoformat(), first_asked=item.get("first_asked") or today.isoformat())
                self._store.save()
                await self._chat.choose(text, offer_for(item), OPTIONS)
                self._on_delivered(text)
                self._asked_on = (self._asked_on[0], self._asked_on[1] + 1)
                out["asked"] += 1
        if any(out.values()):
            self._journal.write("checkins", **out)      # counts only: nobody's name goes in the log
        return out
