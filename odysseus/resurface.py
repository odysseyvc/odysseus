"""The daily review: three of the owner's own past notes, sent back to them each morning.

Rereading highlights does little for memory; meeting one's own thoughts, questions and disagreements again does more.
So each morning the program reads the notes the owner has filed on their site (booknotes.py writes them; the site's
editor, the Studio, writes them too), picks three that are in the owner's own words, and sends them, with up to three
questions still open. Private notes count: the message goes only to the owner.

Everything here is code. Which three is fixed by the date, so the same day always gives the same three, and the
Studio's Today desk, which does the same, can show the same ones:

  pool     every note with `text` (the owner's words, not a bare quote), older than seven days; all of them if fewer
           than three are that old
  order    the pool in file order (reading, then problems; files by name; notes as written), shuffled by Fisher-Yates
           with a generator seeded from the day's date in India time (YYYY-MM-DD): FNV-1a over the string, then the
           mixing step the Studio uses
  picks    the first three
  open     notes of kind question not marked resolved, newest first, at most three

Nothing is sent on a day with nothing to send, and nothing is sent twice in a day: the day is recorded before sending.
"""
from __future__ import annotations

import asyncio
import html
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

from .booknotes import FOLDER, IST, PROBLEMS

PICKS = 3
OPEN_QUESTIONS = 3
OLDER_THAN = timedelta(days=7)
CHECK_EVERY_S = 300
MAX_TEXT = 700          # a long note is cut, so that the whole message stays inside Telegram's 4,096 characters

KIND_NAMES = {"thought": "Thought", "quote": "Quote", "question": "Question", "disagree": "Disagree",
              "connection": "Connection", "idea": "Idea", "progress": "Progress", "resource": "Resource"}

_M = 0xFFFFFFFF


def _imul(a: int, b: int) -> int:
    return (a * b) & _M


def seeded(seed: str) -> Callable[[], float]:
    """The Studio's generator, bit for bit: FNV-1a over the seed (one step per character, by its first UTF-16 unit,
    as `c.charCodeAt(0)` does), then a 32-bit mixing step. Returns numbers in [0, 1)."""
    h = 2166136261
    for ch in seed:
        unit = ord(ch) if ord(ch) < 0x10000 else 0xD800 + ((ord(ch) - 0x10000) >> 10)
        h = _imul(h ^ unit, 16777619)
    state = [h]

    def rand() -> float:
        x = state[0]
        state[0] = _imul(x ^ (x >> 15), 2246822507) ^ _imul(x ^ (x >> 13), 3266489909)
        return state[0] / 4294967296
    return rand


def shuffled(items: list, rand: Callable[[], float]) -> list:
    """Fisher-Yates, from the end: for i from n-1 down to 1, swap i with floor(rand() * (i + 1))."""
    out = list(items)
    for i in range(len(out) - 1, 0, -1):
        j = int(rand() * (i + 1))
        out[i], out[j] = out[j], out[i]
    return out


@dataclass
class Mine:
    where: str          # the book's or problem's title
    slug: str
    folder: str         # FOLDER or PROBLEMS
    kind: str
    at: str
    text: str = ""
    quote: str = ""
    resolved: bool = False

    def when(self) -> datetime | None:
        try:
            t = datetime.fromisoformat(self.at.replace("Z", "+00:00"))
        except ValueError:
            try:
                t = datetime.combine(date.fromisoformat(self.at[:10]), datetime.min.time())
            except ValueError:
                return None
        return t if t.tzinfo else t.replace(tzinfo=IST)


def _str(v: Any) -> str:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return "" if v is None else str(v)


def notes_of(slug: str, raw: str, folder: str) -> list[Mine]:
    try:
        doc = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(doc, dict):
        return []
    title = _str(doc.get("title")) or slug
    default = "thought" if folder == FOLDER else "idea"
    out = []
    for n in doc.get("notes") or []:
        if not isinstance(n, dict):
            continue
        out.append(Mine(where=title, slug=slug, folder=folder, kind=_str(n.get("kind")) or default, at=_str(n.get("at")),
                        text=_str(n.get("text")).strip(), quote=_str(n.get("quote")).strip(), resolved=n.get("resolved") is True))
    return out


def day_of(now: datetime) -> str:
    return now.astimezone(IST).date().isoformat()


def pick(notes: list[Mine], now: datetime) -> list[Mine]:
    own = [n for n in notes if n.text]
    week_ago = now - OLDER_THAN
    old = [n for n in own if (w := n.when()) is not None and w < week_ago]
    pool = old if len(old) >= PICKS else own
    return shuffled(pool, seeded(day_of(now)))[:PICKS]


def open_questions(notes: list[Mine]) -> list[Mine]:
    qs = [n for n in notes if n.kind == "question" and not n.resolved and (n.text or n.quote)]
    return sorted(qs, key=lambda n: n.when() or datetime.min.replace(tzinfo=IST), reverse=True)[:OPEN_QUESTIONS]


def _cut(s: str) -> str:
    s = " ".join(s.split())
    return s if len(s) <= MAX_TEXT else s[:MAX_TEXT].rstrip() + "…"


def _date(n: Mine) -> str:
    w = n.when()
    return f"{w.astimezone(IST):%d %b %Y}".lstrip("0") if w else ""


def message(picks: list[Mine], questions: list[Mine], studio: str = "") -> str:
    """Telegram HTML. Everything taken from the notes is escaped; the only markup is the program's own. Telegram knows
    only &lt; &gt; &amp; and &quot; by name, so apostrophes are left as they are."""
    def e(s: str) -> str:
        return html.escape(s, quote=False)

    def title(n: Mine) -> str:
        if not studio:
            return f"<b>{e(n.where)}</b>"
        href = f"{studio.rstrip('/')}/#/{'b' if n.folder == FOLDER else 'p'}/{n.slug}"
        return f'<a href="{e(href).replace(chr(34), "&quot;")}">{e(n.where)}</a>'

    parts = ["<b>From your notes</b>"]
    for n in picks:
        head = " · ".join(x for x in (e(KIND_NAMES.get(n.kind, n.kind.title())), title(n), e(_date(n))) if x)
        body = [head]
        if n.quote:
            body.append(f"<i>{e(_cut(n.quote))}</i>")
        body.append(e(_cut(n.text)))
        parts.append("\n".join(body))
    if questions:
        lines = ["<b>Open questions</b>"]
        for q in questions:
            lines.append(f"• {e(_cut(q.text or q.quote))} — {title(q)}" + (f", {e(_date(q))}" if _date(q) else ""))
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def plain(text: str) -> str:
    """The same message without markup, for a channel that cannot show it."""
    import re
    out = re.sub(r'<a href="([^"]*)">([^<]*)</a>', lambda m: f"{m.group(2)} ({html.unescape(m.group(1))})", text)
    return html.unescape(re.sub(r"</?(b|i)>", "", out))


class Review:
    """Once a day, at or after `at` (HH:MM, India time): read every note, pick, send. Code only; reads only."""

    def __init__(self, files: Any, send: Callable[[str], Awaitable[None]], journal: Any, state_file: Path, at: str = "08:00",
                 studio: str = "", now: Callable[[], datetime] | None = None, on_delivered: Callable[[str], None] | None = None):
        self._files, self._send, self._journal, self._state, self._at = files, send, journal, Path(state_file), at
        self._studio, self._now = studio, now or (lambda: datetime.now(IST))
        self._on_delivered = on_delivered or (lambda t: None)

    def _last(self) -> str:
        try:
            return str(json.loads(self._state.read_text(encoding="utf-8")).get("sent", ""))
        except (OSError, ValueError, AttributeError):
            return ""

    def _mark(self, day: str) -> None:
        self._state.parent.mkdir(parents=True, exist_ok=True)
        self._state.write_text(json.dumps({"sent": day}), encoding="utf-8")

    async def everything(self) -> list[Mine]:
        out: list[Mine] = []
        for folder in (FOLDER, PROBLEMS):
            for name in await self._files.list(folder):
                if not name.endswith((".yaml", ".yml")):
                    continue
                got = await self._files.get(f"{folder}/{name}")
                if got is not None:
                    out += notes_of(name.rsplit(".", 1)[0], got[0], folder)
        return out

    async def compose(self, now: datetime | None = None) -> str:
        """Today's message, or "" if there is nothing to send."""
        now = now or self._now()
        notes = await self.everything()
        picks, questions = pick(notes, now), open_questions(notes)
        return message(picks, questions, self._studio) if picks or questions else ""

    async def tick(self) -> str:
        """Returns what happened: "early", "done", "empty" or "sent"."""
        now = self._now().astimezone(IST)
        day = now.date().isoformat()
        if now.strftime("%H:%M") < self._at:
            return "early"
        if self._last() == day:
            return "done"
        text = await self.compose(now)
        before = self._last()
        self._mark(day)          # before sending: a crash half-way must not send the same review twice
        if not text:
            self._journal.write("review", day=day, outcome="empty")
            return "empty"
        try:
            await self._send(text)
        except Exception:
            self._mark(before)   # not sent: the next look tries again
            raise
        self._on_delivered(plain(text))
        self._journal.write("review", day=day, outcome="sent", chars=len(text))
        return "sent"

    async def run_forever(self, every_s: float = CHECK_EVERY_S) -> None:
        from .journal import Outage
        outage = Outage(self._journal, "review.tick")       # the network being down is recorded once, not each time
        while True:
            try:
                await self.tick()
                outage.worked()
            except Exception as e:      # the loop must outlive a repository that cannot be reached
                outage.failed(e)
            await asyncio.sleep(every_s)
