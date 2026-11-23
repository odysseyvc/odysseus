"""Reading notes: what the owner thinks while reading, filed from the chat into their own website's repository.

The owner reads a book and sends one line:

    reading Poor Charlie's Almanack p.120: incentives explain more than intelligence does

and the line becomes a dated note in that book's file on their site. The site shows the notes in order, so a reader
sees what the owner thought at each point, not only at the end.

Everything here is code. No model reads these messages: the grammar is small and fixed, so a parser does it at no
cost and in no time, and a message that does not fit it goes to the model as any other message would.

  reading <book> [<where>] [<kind>]: <text>    a note; a new book is created, private
  keep that private                            the last note is never published
  publish <book>                               the book goes public on the site, after the owner approves what will show
  finished <book>[: <takeaway>]                status, finished date and the takeaway
  why <book>: <reason>                         why the owner picked it up
  save <link or title> / to read <...>         a book or link for the To read list (status queued, private)
  must read <book>: <why everyone should>      the Must read shelf; makes it public, so it asks first
  problem <title>: <why>                       a new open problem (src/content/problems), private
  problem <title> idea|question|progress|resource: <text or link>    an entry under it

The kind of a note (thought, quote, question, disagree, connection) is read from the message: quotation marks make a
quote, a question mark a question, and so on; or the owner says it outright ("quote:", "question:").

Where the notes go is fixed by the site's own contract (its README, "Contract for Odysseus"): one YAML file per book
at src/content/reading/<slug>.yaml, committed to `main` through GitHub's contents API with the message
"reading: <title>". Notes are only ever appended. The earlier notes are kept byte for byte, and that is checked before
each commit, as is the whole file against the site's schema. Every push to `main` deploys the site, but a book is not
shown until it is public, and a private note never is.

Adding a note is not asked about: it is the owner's own words, going into the owner's own store, and it stays private.
Publishing is: it changes what the world can read, so the owner is shown exactly what will go public and taps Approve.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import string
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

# --- the site's schema (PersonalSite/src/content.config.ts, collection `reading`) ---------------------------------
NOTE_KINDS = ("thought", "quote", "question", "disagree", "connection")
BOOK_KINDS = ("book", "article", "paper", "essay", "podcast", "video")
STATUSES = ("queued", "reading", "finished", "paused", "abandoned")     # queued: on the To read list
TOPICS = ("ai", "venture", "science", "ideas")        # PersonalSite/src/lib/topics.ts
FOLDER = "src/content/reading"
# Open problems (collection `problems`): the same shape, metadata then `notes:` last.
PROBLEMS = "src/content/problems"
PROBLEM_STATUSES = ("open", "exploring", "building", "solved", "parked")
PROBLEM_KINDS = ("idea", "question", "progress", "resource", "connection")
LINK_TYPES = ("writing", "notes", "reading", "problems", "projects", "zines")     # `links: ["type/slug"]`
QUOTE_MAX = 600                                       # the site caps a quote at this many characters
IST = timezone(timedelta(hours=5, minutes=30))        # the contract: `at` carries the +05:30 offset
PRIVATE_FOR = timedelta(minutes=30)                   # how long after a note "keep that private" still means that note


class BookError(Exception):
    """Something the owner should hear about, in words for them."""


# --- reading a message ----------------------------------------------------------------------------------------------

@dataclass
class Note:
    at: datetime
    kind: str = "thought"
    text: str = ""
    quote: str = ""
    where: str = ""

    def fields(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.where:
            out["where"] = self.where
        out["kind"] = self.kind
        if self.quote:
            out["quote"] = self.quote
        if self.text:
            out["text"] = self.text
        return out


@dataclass
class Command:
    action: str                   # note | private | publish | finish | why | save | must | problem | entry
    book: str = ""                # the book (or problem) as the owner wrote it
    note: Note | None = None
    text: str = ""                # the takeaway, the reason, the pitch, or a problem entry's text
    url: str = ""                 # a link: the page to save, or a problem entry's resource
    kind: str = ""                # a problem entry's kind
    at: datetime | None = None    # when the owner sent it


_KIND_WORDS = {"quote": "quote", "q": "quote", "question": "question", "disagree": "disagree", "disagreement": "disagree",
               "connection": "connection", "thought": "thought", "idea": "thought"}
_LOC = (r"(?:(?:pages|page|pp|pg|p|location|loc|chapter|chap|ch|section|sec|§)\.?\s*[0-9ivxlc]+(?:\s*[-–]\s*[0-9ivxlc]+)?"
        r"|\d{1,3}\s?%|@?\s*\d{1,2}:\d{2}(?::\d{2})?)")
_TAIL_LOC = re.compile(r"(?i)(?:^|[\s,(])(" + _LOC + r")\)?\s*$")
_TAIL_KIND = re.compile(r"(?i)(?:^|[\s,])(quote|question|disagree|disagreement|connection|thought)\s*$")
_BODY_KIND = re.compile(r"(?i)^(quote|q|question|disagree|disagreement|connection|thought|idea)\s*[:\-–—]\s*")
_OPEN = {'"': '"', "“": "”", "'": "'", "‘": "’", "«": "»", "„": "“"}
_DISAGREE = re.compile(r"(?i)^(?:i\s+)?(?:disagree|don'?t\s+(?:buy|agree)|do\s+not\s+(?:buy|agree)|not\s+convinced|unconvinced|wrong\b|nope\b)"
                       r"|\bi\s+disagree\b|\bi\s+don'?t\s+buy\b")
_CONNECT = re.compile(r"(?i)\b(?:reminds\s+me\s+of|connects?\s+(?:to|with)|connection\s+(?:to|with)|similar\s+to|same\s+(?:idea|point|argument)\s+as"
                      r"|echoes|see\s+also|cf\.)")
_PRIVATE = re.compile(r"(?i)^(?:please\s+)?(?:keep|make|mark)\s+(?:that|it|this|the\s+last\s+(?:one|note))\s+private[.!]?$")
_PUBLISH = re.compile(r"(?is)^publish\s+(.+?)[.!]?$")
_FINISH = re.compile(r"(?is)^(?:finished|done\s+with)\s+(.+?)(?:\s*:\s+(.+))?$")
_WHY = re.compile(r"(?is)^why\s+(.+?)\s*:\s+(.+)$")
_READING = re.compile(r"(?is)^reading\s+(.+)$")
_SAVE = re.compile(r"(?is)^(?:save|to\s+read)\s*:?\s+(.+?)[.!]?$")
_MUST = re.compile(r"(?is)^must[\s-]+read\s+(.+?)\s*:\s+(.+)$")
_ENTRY = re.compile(r"(?is)^problem\s+(.+?)\s+(idea|question|progress|resource|link|connection)\s*:\s*(.+)$")
_PROBLEM = re.compile(r"(?is)^problem\s+(.+?)\s*:\s+(.+)$")
_URL = re.compile(r"https?://[^\s<>\"']+")
# "save that", "save it to memory": not a book. Those go to the model as before.
_NOT_A_TITLE = re.compile(r"(?i)^(?:that|this|it|these|those|them|me|my|the\s+last|for\s+later|later)\b|\b(?:memory|remember|draft|contact)\b")


def where_of(loc: str) -> str:
    """'p.120', 'page 120', 'p120' -> 'p. 120'; other places as written."""
    loc = " ".join(loc.split()).lstrip("@ ").strip()
    m = re.match(r"(?i)^(pages|page|pp|pg|p|location|loc|chapter|chap|ch|section|sec|§)\.?\s*(.+)$", loc)
    if not m:
        return loc.replace(" %", "%")
    word = m.group(1).lower()
    short = {"p": "p.", "pg": "p.", "page": "p.", "pp": "pp.", "pages": "pp.", "loc": "loc.", "location": "loc.", "ch": "ch.",
             "chap": "ch.", "chapter": "ch.", "sec": "§", "section": "§", "§": "§"}[word]
    return f"{short} {m.group(2).replace(' ', '')}"


def _split_quote(body: str) -> tuple[str, str] | None:
    """'"the passage" - my comment' -> (passage, comment). None if the body does not open with a quotation mark."""
    if not body or body[0] not in _OPEN:
        return None
    close = _OPEN[body[0]]
    end = body.rfind(close) if close != body[0] or body.count(close) >= 2 else -1
    if body[0] in "'‘" and end <= 0:
        return None
    end = body.find(close, 1) if end <= 0 else end
    if end <= 1:
        return None
    # The passage ends at the last closing mark that is followed by nothing, or by a separator and a comment.
    for m in reversed([m.start() for m in re.finditer(re.escape(close), body[1:])]):
        at = m + 1
        rest = body[at + 1:]
        if not rest.strip() or re.match(r"^\s*(?:[-–—:|/]+|\.\s|,\s)", rest):
            comment = re.sub(r"^\s*[-–—:|/.,]+\s*", "", rest).strip()
            return body[1:at].strip(), comment
    return None


def kind_of(body: str, stated: str = "") -> tuple[str, str, str]:
    """(kind, text, quote) for the body of a note. `stated` is a kind the owner named before the colon."""
    body = body.strip()
    m = _BODY_KIND.match(body)
    if m and not stated:
        stated, body = _KIND_WORDS[m.group(1).lower()], body[m.end():].strip()
    if stated == "quote":
        split = _split_quote(body)
        return ("quote", split[1], split[0]) if split else ("quote", "", body)
    if stated:
        return stated, body, ""
    split = _split_quote(body)
    if split:
        return "quote", split[1], split[0]
    if body.rstrip().endswith("?"):
        return "question", body, ""
    if _DISAGREE.search(body):
        return "disagree", body, ""
    if _CONNECT.search(body):
        return "connection", body, ""
    return "thought", body, ""


def _head_and_body(rest: str) -> tuple[str, str] | None:
    """Split 'Sapiens: A Brief History p.12: the text' at the colon that ends the book and its place. The first ': '
    whose left side ends in a place (p.12, 40%, 1:02:33) wins; otherwise the first ': '."""
    cuts = [m.start() for m in re.finditer(r":\s", rest)]
    if not cuts:
        return None
    for cut in cuts:
        head = rest[:cut].strip()
        if _TAIL_LOC.search(_TAIL_KIND.sub("", head).strip()) or _TAIL_KIND.search(head) and _TAIL_LOC.search(head[:_TAIL_KIND.search(head).start()]):
            return head, rest[cut + 1:].strip()
    return rest[:cuts[0]].strip(), rest[cuts[0] + 1:].strip()


def parse(text: str, at: datetime) -> Command | None:
    """The message as a reading command, or None if it is not one (it then goes to the model as usual)."""
    t = " ".join((text or "").strip().split(" ")).strip()
    if not t:
        return None
    if _PRIVATE.match(t):
        return Command("private")
    m = _PUBLISH.match(t)
    if m:
        return Command("publish", book=m.group(1).strip())
    m = _MUST.match(t)
    if m:
        return Command("must", book=m.group(1).strip(), text=m.group(2).strip())
    m = _WHY.match(t)
    if m:
        return Command("why", book=m.group(1).strip(), text=m.group(2).strip())
    m = _FINISH.match(t)
    if m:
        return Command("finish", book=m.group(1).strip(), text=(m.group(2) or "").strip())
    stamp = at.astimezone(IST).replace(microsecond=0)
    m = _ENTRY.match(t)
    if m:
        body = m.group(3).strip()
        link = _URL.search(body)
        url = link.group(0).rstrip(").,;") if link else ""
        rest = " ".join((body[:link.start()] + body[link.end():]).split()).strip(" -–—:,") if link else body
        kind = "resource" if m.group(2).lower() == "link" else m.group(2).lower()
        return Command("entry", book=m.group(1).strip(), kind=kind, text=rest, url=url, at=stamp)
    m = _PROBLEM.match(t)
    if m:
        return Command("problem", book=m.group(1).strip(), text=m.group(2).strip(), at=stamp)
    m = _SAVE.match(t)
    if m:
        what = m.group(1).strip()
        link = _URL.search(what)
        if link:
            rest = " ".join((what[:link.start()] + what[link.end():]).split()).strip(" -–—:,")
            return Command("save", book=rest, url=link.group(0).rstrip(").,;"), at=stamp)
        if _NOT_A_TITLE.search(what) or len(what) > 200:
            return None
        return Command("save", book=what, at=stamp)
    m = _READING.match(t)
    if not m:
        return None
    split = _head_and_body(m.group(1))
    if split is None:
        return None
    head, body = split
    stated, where = "", ""
    for _ in range(2):           # the kind and the place may come in either order
        k = _TAIL_KIND.search(head)
        if k and not stated and head[:k.start()].strip():
            stated, head = _KIND_WORDS[k.group(1).lower()], head[:k.start()].strip(" ,")
            continue
        p = _TAIL_LOC.search(head)
        if p and not where and head[:p.start(1)].strip(" ,("):
            where, head = where_of(p.group(1)), head[:p.start(1)].strip(" ,(")
    if not head or not body:
        return None
    kind, note_text, quote = kind_of(body, stated)
    if not note_text and not quote:
        return None
    return Command("note", book=head, note=Note(at=at.astimezone(IST).replace(microsecond=0), kind=kind, text=note_text, quote=quote, where=where))


# --- the book's file ------------------------------------------------------------------------------------------------

def slugify(title: str) -> str:
    """The contract: lower case, ASCII letters and digits, words joined by '-'."""
    ascii_ = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
    ascii_ = ascii_.replace("'", "").replace("’", "")
    return "-".join(re.findall(r"[a-z0-9]+", ascii_))


def title_of(written: str) -> str:
    """A title as the owner typed it; all in lower case, it gets capitals."""
    written = " ".join(written.split()).strip(" .")
    return string.capwords(written) if written == written.lower() else written


def _scalar(key: str, value: Any) -> str:
    """One top-level `key: value`, quoted as YAML needs."""
    return yaml.safe_dump({key: value}, sort_keys=False, allow_unicode=True, width=10 ** 6, default_flow_style=False)


def _at(value: datetime) -> str:
    return value.astimezone(IST).replace(microsecond=0).isoformat()


@dataclass
class Entry:
    """One entry under an open problem: an idea, a question, progress, a resource or a connection."""
    at: datetime
    kind: str = "idea"
    text: str = ""
    url: str = ""

    def fields(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind}
        if self.text:
            out["text"] = self.text
        if self.url:
            out["url"] = self.url
        return out


def render_note(note: Note | Entry, dash: int = 2) -> str:
    """The note as YAML lines, the dash indented by `dash` spaces."""
    pad = " " * dash
    lines = [f"{pad}- at: {_at(note.at)}"]
    for line in yaml.safe_dump(note.fields(), sort_keys=False, allow_unicode=True, width=10 ** 6, default_flow_style=False).splitlines():
        lines.append(f"{pad}  {line}" if line.strip() else "")
    return "\n".join(lines) + "\n"


def new_book(title: str, author: str, today: date, note: Note | None = None, status: str = "reading", kind: str = "book", url: str = "") -> str:
    """A new book's file. Private. A book started now carries `started`; one saved for later is `queued`."""
    out = _scalar("title", title)
    if author:
        out += _scalar("author", author)
    out += f"kind: {kind}\n"
    if url:
        out += _scalar("url", url)
    out += f"status: {status}\nadded: {today.isoformat()}\n"
    if status == "reading":
        out += f"started: {today.isoformat()}\n"
    out += "public: false\n"
    return out + ("notes:\n" + render_note(note) if note is not None else "")


def new_problem(title: str, why: str, today: date) -> str:
    out = _scalar("title", title) + f"status: open\nadded: {today.isoformat()}\npublic: false\n"
    return out + (_scalar("why", why) if why else "")


def _lines(raw: str) -> list[str]:
    return raw.splitlines(keepends=True)


def _is_top(line: str) -> bool:
    """A line that starts a top-level key (not indented, not a comment, not blank, not a list item)."""
    return bool(line) and not line[0].isspace() and not line.startswith(("#", "-", "\n", "\r"))


@dataclass
class Block:
    key_line: int                  # index of the `notes:` line
    start: int                     # first line after it
    end: int                       # one past the last line of the block's content (trailing blank lines excluded)
    items: list[int] = field(default_factory=list)   # index of each item's first line
    dash: int = 2


def notes_block(raw: str) -> Block | None:
    lines = _lines(raw)
    key = next((i for i, l in enumerate(lines) if re.match(r"^notes\s*:", l)), None)
    if key is None:
        return None
    end = key + 1
    while end < len(lines) and not _is_top(lines[end]):
        end += 1
    while end > key + 1 and not lines[end - 1].strip():
        end -= 1
    items, dash = [], None
    for i in range(key + 1, end):
        m = re.match(r"^( *)- ", lines[i]) or re.match(r"^( *)-$", lines[i].rstrip("\r\n"))
        if m and (dash is None or len(m.group(1)) == dash):
            dash = len(m.group(1)) if dash is None else dash
            items.append(i)
    return Block(key, key + 1, end, items, 2 if dash is None else dash)


def append_note(raw: str, note: Note | Entry) -> str:
    """The file with one more note at the end of `notes`. Nothing before it changes."""
    if not raw.endswith("\n"):
        raw += "\n"
    block = notes_block(raw)
    lines = _lines(raw)
    if block is None:
        return raw + "notes:\n" + render_note(note)
    if re.match(r"^notes\s*:\s*\[\s*\]\s*(#.*)?$", lines[block.key_line].rstrip("\r\n")):
        lines[block.key_line] = "notes:\n"
    elif not re.match(r"^notes\s*:\s*(#.*)?$", lines[block.key_line].rstrip("\r\n")):
        raise BookError("This book's notes are written in a form I can't add to safely. Add this one by hand.")
    if block.end > block.start and lines[block.end - 1] and not lines[block.end - 1].endswith("\n"):
        lines[block.end - 1] += "\n"
    lines.insert(block.end, render_note(note, block.dash))
    return "".join(lines)


def mark_last_private(raw: str) -> str:
    """The file with its last note marked `private: true`. Every other line stays as it was."""
    block = notes_block(raw)
    if block is None or not block.items:
        raise BookError("That book has no notes yet.")
    lines = _lines(raw)
    first = block.items[-1]
    pad = " " * (block.dash + 2)
    for i in range(first, block.end):
        # The item's own fields sit at the dash's column + 2 (on the first line, just after "- ").
        if (i == first or lines[i].startswith(pad)) and re.match(r"^private\s*:", lines[i][len(pad):]):
            lines[i] = lines[i][:len(pad)] + "private: true\n"
            return "".join(lines)
    last = block.end - 1
    if not lines[last].endswith("\n"):
        lines[last] += "\n"
    lines.insert(block.end, f"{pad}private: true\n")
    return "".join(lines)


def set_fields(raw: str, values: dict[str, Any]) -> str:
    """The file with these top-level fields set. Each replaces its own lines; a new one goes in before `notes`."""
    if "notes" in values:
        raise ValueError("notes are only ever appended")
    lines = _lines(raw if raw.endswith("\n") else raw + "\n")
    for key, value in values.items():
        new = _scalar(key, value)
        at = next((i for i, l in enumerate(lines) if re.match(rf"^{re.escape(key)}\s*:", l)), None)
        if at is None:
            block = notes_block("".join(lines))
            lines.insert(block.key_line if block else len(lines), new)
            continue
        end = at + 1
        while end < len(lines) and not _is_top(lines[end]) and (lines[end].startswith((" ", "\t")) or not lines[end].strip()) \
                and not lines[end].lstrip().startswith("#"):
            end += 1
        while end > at + 1 and not lines[end - 1].strip():
            end -= 1
        lines[at:end] = [new]
    return "".join(lines)


# --- checking a file before it is committed ---------------------------------------------------------------------------

def _is_date(v: Any) -> bool:
    if isinstance(v, (date, datetime)):
        return True
    if isinstance(v, str):
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
            return True
        except ValueError:
            return False
    return False


def validate(raw: str, folder: str = FOLDER) -> dict[str, Any]:
    """The book (or, with folder=PROBLEMS, the problem), parsed, if it parses as YAML and fits the site's schema.
    Raises BookError saying what is wrong."""
    try:
        book = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise BookError(f"The file would not parse as YAML ({type(e).__name__}).") from e
    if not isinstance(book, dict):
        raise BookError("The file is not a set of fields.")
    problems: list[str] = []

    def text(key: str, required: bool = False, where: dict | None = None, label: str = "") -> None:
        src = book if where is None else where
        if key not in src or src[key] is None:
            if required:
                problems.append(f"{label}{key} is missing")
            return
        if not isinstance(src[key], str) or (required and not src[key].strip()):
            problems.append(f"{label}{key} must be text")

    def one_of(key: str, allowed: tuple, where: dict | None = None, label: str = "") -> None:
        src = book if where is None else where
        if key in src and src[key] is not None and src[key] not in allowed:
            problems.append(f"{label}{key} must be one of {', '.join(allowed)}")

    def address(value: Any) -> bool:
        return isinstance(value, str) and bool(re.match(r"^[a-z][a-z0-9+.-]*://\S+$", value))

    def a_date(key: str, src: dict | None = None, label: str = "") -> None:
        src = book if src is None else src
        if src.get(key) is not None and not _is_date(src[key]):
            problems.append(f"{label}{key} must be a date")

    is_problem = folder == PROBLEMS
    text("title", required=True)
    for key in ("why",) if is_problem else ("author", "why", "takeaway", "pitch"):
        text(key)
    one_of("status", PROBLEM_STATUSES if is_problem else STATUSES)
    if not is_problem:
        one_of("kind", BOOK_KINDS)
        if book.get("url") is not None and not address(book["url"]):
            problems.append("url must be an address")
        for key in ("started", "finished"):
            a_date(key)
    a_date("added")
    for key in ("public", "recommended"):
        if book.get(key) is not None and not isinstance(book[key], bool):
            problems.append(f"{key} must be true or false")
    links = book.get("links")
    if links is not None and not (isinstance(links, list) and all(isinstance(x, str) and re.match(rf"^({'|'.join(LINK_TYPES)})/[a-z0-9-]+$", x) for x in links)):
        problems.append("links must be a list of type/slug, the types being " + ", ".join(LINK_TYPES))
    topics = book.get("topics")
    if topics is not None and not (isinstance(topics, list) and all(t in TOPICS for t in topics)):
        problems.append(f"topics must be a list from {', '.join(TOPICS)}")
    notes = book.get("notes")
    if notes is not None and not isinstance(notes, list):
        problems.append("notes must be a list")
    for n, note in enumerate(notes or [], 1):
        label = f"note {n}: "
        if not isinstance(note, dict):
            problems.append(f"{label}not a set of fields")
            continue
        if not _is_date(note.get("at")):
            problems.append(f"{label}at must be a date and time")
        if is_problem:
            text("text", where=note, label=label)
            one_of("kind", PROBLEM_KINDS, where=note, label=label)
            if note.get("url") is not None and not (address(note["url"]) and note["url"].startswith(("http://", "https://"))):
                problems.append(f"{label}url must be a web address")
            if not note.get("text") and not note.get("url"):
                problems.append(f"{label}needs text or a link")
        else:
            for key in ("where", "quote", "text"):
                text(key, where=note, label=label)
            one_of("kind", NOTE_KINDS, where=note, label=label)
            if isinstance(note.get("quote"), str) and len(note["quote"]) > QUOTE_MAX:
                problems.append(f"{label}a quote can be at most {QUOTE_MAX} characters (this one is {len(note['quote'])}); "
                                "file the passage in parts, or the rest as text")
            if not note.get("text") and not note.get("quote"):
                problems.append(f"{label}needs text or a quote")
        for key in ("private", "resolved"):
            if note.get(key) is not None and not isinstance(note[key], bool):
                problems.append(f"{label}{key} must be true or false")
    if problems:
        raise BookError("The file would not fit the site's schema: " + "; ".join(problems[:5]) + ".")
    return book


def earlier_kept(before: str, after: str, keep: int) -> bool:
    """True if the first `keep` notes of `after` are byte for byte the first `keep` notes of `before`."""
    if keep == 0:
        return True
    b, a = notes_block(before), notes_block(after)
    if b is None or a is None or len(b.items) < keep or len(a.items) < keep:
        return False
    bl, al = _lines(before), _lines(after)
    b_end = b.items[keep] if len(b.items) > keep else b.end
    a_end = a.items[keep] if len(a.items) > keep else a.end
    old = "".join(bl[b.items[0]:b_end]).rstrip("\r\n")
    new = "".join(al[a.items[0]:a_end]).rstrip("\r\n")
    if old != new:
        return False
    parsed_b, parsed_a = yaml.safe_load(before) or {}, yaml.safe_load(after) or {}
    return (parsed_b.get("notes") or [])[:keep] == (parsed_a.get("notes") or [])[:keep]


# --- the repository -----------------------------------------------------------------------------------------------------

class GitHubFiles:
    """A folder of one GitHub repository, through the contents API. The token needs Contents read and write on that
    repository and nothing else."""

    API = "https://api.github.com"

    def __init__(self, repo: str, token: str, branch: str = "main", client: Any = None):
        import httpx
        self._repo, self._branch = repo.strip().strip("/"), branch
        self._client = client or httpx.AsyncClient(timeout=20.0, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "odysseus-reading"})

    def _url(self, path: str) -> str:
        return f"{self.API}/repos/{self._repo}/contents/{path}"

    async def list(self, folder: str) -> list[str]:
        r = await self._client.get(self._url(folder), params={"ref": self._branch})
        if r.status_code == 404:
            return []
        self._check(r)
        return [e["name"] for e in r.json() if isinstance(e, dict) and e.get("type") == "file"]

    async def get(self, path: str) -> tuple[str, str] | None:
        """(text, sha), or None if there is no such file."""
        r = await self._client.get(self._url(path), params={"ref": self._branch})
        if r.status_code == 404:
            return None
        self._check(r)
        body = r.json()
        return base64.b64decode(body.get("content", "")).decode("utf-8"), body["sha"]

    async def put(self, path: str, text: str, sha: str | None, message: str) -> str:
        """Commit the file. Returns the commit's sha. Raises Conflict if the file changed since `sha` was read."""
        payload = {"message": message, "content": base64.b64encode(text.encode("utf-8")).decode("ascii"), "branch": self._branch}
        if sha:
            payload["sha"] = sha
        r = await self._client.put(self._url(path), json=payload)
        if r.status_code in (409, 422):      # 409: the sha is stale; 422: a file is there and no sha was given
            raise Conflict()
        self._check(r)
        return r.json().get("commit", {}).get("sha", "")

    async def delete(self, path: str, sha: str, message: str) -> None:
        r = await self._client.request("DELETE", self._url(path), json={"message": message, "sha": sha, "branch": self._branch})
        self._check(r)

    @staticmethod
    def _check(r: Any) -> None:
        if r.status_code in (401, 403):
            raise BookError("GitHub refused the reading-notes key (it may have expired, or lack write access to the site's repository).")
        if r.status_code >= 400:
            raise BookError(f"GitHub answered {r.status_code}.")


class Conflict(Exception):
    """The file changed between reading it and writing it."""


# --- the owner's side -------------------------------------------------------------------------------------------------

Asker = Callable[[Any], Awaitable[None]]
ATTEMPTS = 3          # the site's Studio writes the same files: a write against an old version is read again and redone


def link_kind(url: str) -> str:
    """What kind of thing a saved link is, from its address alone."""
    u = url.lower()
    if re.search(r"(youtube\.com|youtu\.be|vimeo\.com)/", u):
        return "video"
    if re.search(r"(arxiv\.org|biorxiv\.org|ssrn\.com|doi\.org)/", u) or u.split("?")[0].endswith(".pdf"):
        return "paper"
    if re.search(r"(podcasts\.apple\.com|open\.spotify\.com/episode|overcast\.fm|pca\.st)/", u):
        return "podcast"
    return "article"


def _title_from_address(url: str) -> str:
    from urllib.parse import unquote, urlparse
    u = urlparse(url)
    last = unquote(u.path.rstrip("/").split("/")[-1]) if u.path.strip("/") else ""
    last = re.sub(r"\.(pdf|html?)$", "", last, flags=re.I)
    words = " ".join(re.split(r"[-_+]+", last)).strip()
    return string.capwords(words) if words and not words.isdigit() else u.netloc.removeprefix("www.")


async def fetch_title(url: str) -> str:
    """The title a page gives itself (og:title, else <title>), or a title made from its address. The page is someone
    else's: what comes back is only ever used as a title, and is checked like any other field before it is written."""
    import html as html_mod
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (odysseus-reading)"}) as client:
            async with client.stream("GET", url) as r:
                if r.status_code >= 400 or "html" not in r.headers.get("content-type", ""):
                    return _title_from_address(url)
                body = b""
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > 512_000 or b"</head>" in body.lower():
                        break
    except Exception:
        return _title_from_address(url)
    page = body.decode("utf-8", "replace")
    m = (re.search(r"<meta[^>]+property=[\"']og:title[\"'][^>]*content=[\"']([^\"']+)", page, re.I)
         or re.search(r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]*property=[\"']og:title", page, re.I)
         or re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S))
    title = " ".join(html_mod.unescape(m.group(1)).split())[:200] if m else ""
    return title or _title_from_address(url)


@dataclass
class Found:
    slug: str = ""
    title: str = ""
    author: str = ""
    exists: bool = False
    choices: list[str] = field(default_factory=list)      # more than one book fits what was written


def _noun(folder: str) -> str:
    return "problems" if folder == PROBLEMS else "reading"


class Shelf:
    """The owner's books and open problems, in their site's repository. One change at a time."""

    def __init__(self, files: GitHubFiles, journal: Any, state: Path | None = None, now: Callable[[], datetime] | None = None,
                 title_of_link: Callable[[str], Awaitable[str]] | None = None):
        self._files, self._journal, self._state = files, journal, state
        self._now = now or (lambda: datetime.now(IST))
        self._title_of_link = title_of_link or fetch_title
        self._lock = asyncio.Lock()

    def _today(self) -> date:
        return self._now().astimezone(IST).date()

    # -- finding a book or a problem --
    async def find(self, written: str, creating: bool = False, folder: str = FOLDER) -> Found:
        slugs = [n[:-5] if n.endswith(".yaml") else n[:-4] for n in await self._files.list(folder) if n.endswith((".yaml", ".yml"))]
        written = " ".join(written.split()).strip(" .")

        def match(s: str) -> list[str]:
            if not s:
                return []
            if s in slugs:
                return [s]
            # The words written, from the start of a word in the title; too short a phrase matches nothing but itself.
            return [x for x in slugs if f"-{s}" in f"-{x}"] if len(s) >= 4 else []

        hits = match(slugify(written))
        title, author = written, ""
        if not hits and folder == FOLDER and " by " in written.lower():
            cut = written.lower().rfind(" by ")
            title, author = written[:cut].strip(), written[cut + 4:].strip()
            hits = match(slugify(title))
        if len(hits) == 1:
            return Found(slug=hits[0], exists=True)
        if len(hits) > 1:
            return Found(choices=hits)
        if not creating or not slugify(title):
            return Found()
        return Found(slug=slugify(title)[:80].strip("-"), title=title_of(title), author=author)

    async def _read(self, slug: str, folder: str = FOLDER) -> tuple[str, str]:
        got = await self._files.get(f"{folder}/{slug}.yaml")
        if got is None:
            raise BookError("That file is not there any more.")
        return got

    async def _commit(self, slug: str, change: Callable[[str], str], keep: Callable[[str], int], folder: str = FOLDER) -> tuple[dict[str, Any], str]:
        """Read the file, apply `change`, check it, commit it. `keep(before)` is how many earlier notes must come
        through byte for byte. If the file moved underneath (the Studio, or a second device), read it and do it again."""
        for _ in range(ATTEMPTS):
            before, sha = await self._read(slug, folder)
            after = change(before)
            book = validate(after, folder)
            if not earlier_kept(before, after, keep(before)):
                raise BookError("That change would have altered earlier notes, so I did not make it.")
            try:
                commit = await self._files.put(f"{folder}/{slug}.yaml", after, sha, f"{_noun(folder)}: {book['title']}")
                return book, commit
            except Conflict:
                continue
        raise BookError("The file kept changing while I wrote to it. Try again in a moment.")

    async def _create(self, slug: str, raw: str, folder: str = FOLDER) -> tuple[dict[str, Any], str] | None:
        """Commit a new file. None if one appeared under that name meanwhile."""
        book = validate(raw, folder)
        try:
            return book, await self._files.put(f"{folder}/{slug}.yaml", raw, None, f"{_noun(folder)}: {book['title']}")
        except Conflict:
            return None

    # -- the commands --
    async def add_note(self, cmd: Command) -> str:
        assert cmd.note is not None
        note, started = cmd.note, False
        async with self._lock:
            for _ in range(ATTEMPTS):
                found = await self.find(cmd.book, creating=True)
                if found.choices:
                    return "More than one book fits that: " + ", ".join(found.choices) + ". Write a little more of the title."
                if not found.slug:
                    return "I couldn't make a file name from that title."
                if found.exists:
                    def change(raw: str) -> str:
                        nonlocal started
                        now = yaml.safe_load(raw) or {}
                        if now.get("status") == "queued":
                            # The first note on a book from the To read list: reading has started.
                            raw = set_fields(raw, {"status": "reading", **({} if now.get("started") else {"started": self._today()})})
                            started = True
                        return append_note(raw, note)
                    book, commit = await self._commit(found.slug, change, lambda raw: len(_notes(raw)))
                    count, new = len(book.get("notes") or []), False
                    break
                made = await self._create(found.slug, new_book(found.title, found.author, note.at.astimezone(IST).date(), note))
                if made is not None:
                    (book, commit), count, new = made, 1, True
                    break
            else:
                raise BookError("That book's file kept changing while I wrote to it. Try again in a moment.")
            self._remember(found.slug, note.at, FOLDER)
            self._journal.write("reading", action="note", new_book=new, note_kind=note.kind, commit=commit[:10])
        where = f" at {note.where}" if note.where else ""
        head = f"New book, private until you publish it: {book['title']}" + (f" by {book['author']}" if book.get("author") else "") + ".\n" if new else ""
        moved = f"Moved {book['title']} from To read to Reading.\n" if started else ""
        return (f"{head}{moved}Filed a {note.kind}{where} in {book['title']} (note {count}). "
                "Send \"keep that private\" to keep this one off the site for good.")

    async def save(self, cmd: Command) -> str:
        """A book or link for the To read list: a new private file with status `queued`."""
        title, url = cmd.book, cmd.url
        kind = link_kind(url) if url else "book"
        if url and not title:
            title = await self._title_of_link(url)
        async with self._lock:
            found = await self.find(title, creating=True)
            if found.choices:
                return "More than one book fits that: " + ", ".join(found.choices) + ". Write a little more of the title."
            if found.exists:
                raw, _ = await self._read(found.slug)
                book = yaml.safe_load(raw) or {}
                return f"{book.get('title', found.slug)} is already on your shelf ({book.get('status', 'reading')})."
            if not found.slug:
                return "I couldn't make a file name from that title."
            made = await self._create(found.slug, new_book(found.title, found.author, self._today(), None, status="queued", kind=kind, url=url))
            if made is None:
                return "A file for that appeared just now; it is on your shelf already."
            book, commit = made
            self._journal.write("reading", action="save", book_kind=kind, commit=commit[:10])
        return (f"Saved to your To read list: {book['title']}" + (f" by {book['author']}" if book.get("author") else "")
                + (f" ({kind})" if kind != "book" else "") + ". Private, like every new book.")

    async def problem_new(self, cmd: Command) -> str:
        async with self._lock:
            found = await self.find(cmd.book, creating=True, folder=PROBLEMS)
            if found.choices:
                return "More than one problem fits that: " + ", ".join(found.choices) + ". Write a little more of the title."
            if found.exists:
                raw, _ = await self._read(found.slug, PROBLEMS)
                title = (yaml.safe_load(raw) or {}).get("title", found.slug)
                return f"There is already a problem called {title}. Add to it with: problem {title} idea: …"
            if not found.slug:
                return "I couldn't make a file name from that title."
            made = await self._create(found.slug, new_problem(found.title, cmd.text, self._today()), PROBLEMS)
            if made is None:
                return "A file for that problem appeared just now. Send it again to add to it."
            book, commit = made
            self._journal.write("reading", action="problem", commit=commit[:10])
        return (f"New open problem, private until you publish it: {book['title']}. "
                f"Add to it with: problem {book['title']} idea|question|progress|resource: …")

    async def problem_entry(self, cmd: Command) -> str:
        entry = Entry(at=cmd.at or self._now(), kind=cmd.kind, text=cmd.text, url=cmd.url)
        if not entry.text and not entry.url:
            return "That entry has nothing in it."
        async with self._lock:
            found = await self.find(cmd.book, folder=PROBLEMS)
            if found.choices:
                return "More than one problem fits that: " + ", ".join(found.choices) + ". Write a little more of the title."
            if not found.exists:
                return f"I have no open problem called {cmd.book}. Start one with: problem {cmd.book}: why it matters."
            book, commit = await self._commit(found.slug, lambda raw: append_note(raw, entry), lambda raw: len(_notes(raw)), PROBLEMS)
            self._remember(found.slug, entry.at, PROBLEMS)
            self._journal.write("reading", action="entry", entry_kind=entry.kind, commit=commit[:10])
        return (f"Added {'an' if entry.kind[0] in 'aeiou' else 'a'} {entry.kind} to {book['title']} (entry {len(book.get('notes') or [])}). "
                "Send \"keep that private\" to keep this one off the site for good.")

    async def private_last(self) -> str:
        last = self._last()
        if last is None:
            return ""       # nothing filed lately: the words were about something else
        slug, at, folder = last
        async with self._lock:
            def change(raw: str) -> str:
                notes = _notes(raw)
                if not notes or _at_of(notes[-1].get("at")) != at:
                    raise BookError("A newer note has gone in there since, so I did not guess which one you meant.")
                return mark_last_private(raw)
            book, commit = await self._commit(slug, change, lambda raw: len(_notes(raw)) - 1, folder)
            self._journal.write("reading", action="private", commit=commit[:10])
        return f"Done: that {'entry' if folder == PROBLEMS else 'note'} in {book['title']} stays private, even after it is published."

    async def set_finished(self, written: str, takeaway: str) -> str:
        async with self._lock:
            found = await self.find(written)
            if not found.exists:
                return self._not_found(found)
            today = self._today()
            values: dict[str, Any] = {"status": "finished", "finished": today}
            if takeaway:
                values["takeaway"] = takeaway
            before, _ = await self._read(found.slug)
            old = (yaml.safe_load(before) or {}).get("takeaway")
            book, commit = await self._commit(found.slug, lambda raw: set_fields(raw, values), lambda raw: len(_notes(raw)))
            self._journal.write("reading", action="finished", commit=commit[:10])
        replaced = f"\nThe takeaway before was: {old}" if takeaway and old and old != takeaway else ""
        return f"Marked {book['title']} finished on {today:%d %b %Y}" + (" with your takeaway." if takeaway else ".") + replaced

    async def set_why(self, written: str, why: str) -> str:
        async with self._lock:
            found = await self.find(written)
            if not found.exists:
                return self._not_found(found)
            before, _ = await self._read(found.slug)
            old = (yaml.safe_load(before) or {}).get("why")
            book, commit = await self._commit(found.slug, lambda raw: set_fields(raw, {"why": why}), lambda raw: len(_notes(raw)))
            self._journal.write("reading", action="why", commit=commit[:10])
        replaced = f"\nIt said before: {old}" if old and old != why else ""
        return f"Saved why you picked up {book['title']}." + replaced

    # -- changes that are shown first and wait for the owner's tap --
    async def publish_question(self, written: str) -> tuple[str, dict[str, Any]] | str:
        """(the question for the owner, the inputs to approve), or a line saying why there is nothing to ask."""
        found = await self.find(written)
        if not found.exists:
            return self._not_found(found)
        raw, sha = await self._read(found.slug)
        book = validate(raw)
        if book.get("public") is True:
            return f"{book['title']} is already public."
        return publish_text(book, found.slug), {"book": found.slug, "sha": sha}

    async def publish(self, slug: str, sha: str) -> str:
        async with self._lock:
            raw, now_sha = await self._read(slug)
            if now_sha != sha:
                return "The book changed after I asked you, so I did not publish it. Send publish again to see it as it is now."
            book, commit = await self._commit(slug, lambda r: set_fields(r, {"public": True}), lambda r: len(_notes(r)))
            self._journal.write("reading", action="publish", commit=commit[:10])
        return f"Published {book['title']}. The site rebuilds in a few minutes."

    async def must_question(self, written: str, pitch: str) -> tuple[str, dict[str, Any]] | str:
        found = await self.find(written)
        if found.choices:
            return self._not_found(found)
        if not found.exists:
            return f"I have no book called {written}. Save it first with: save {written}"
        raw, sha = await self._read(found.slug)
        book = validate(raw)
        return must_text(book, found.slug, pitch), {"book": found.slug, "sha": sha, "pitch": pitch}

    async def must_read(self, slug: str, sha: str, pitch: str) -> str:
        async with self._lock:
            raw, now_sha = await self._read(slug)
            if now_sha != sha:
                return "The book changed after I asked you, so I changed nothing. Send must read again to see it as it is now."
            book, commit = await self._commit(slug, lambda r: set_fields(r, {"public": True, "recommended": True, "pitch": pitch}),
                                              lambda r: len(_notes(r)))
            self._journal.write("reading", action="must", commit=commit[:10])
        return f"{book['title']} is on your Must read shelf now, and public. The site rebuilds in a few minutes."

    def _not_found(self, found: Found) -> str:
        if found.choices:
            return "More than one book fits that: " + ", ".join(found.choices) + ". Write a little more of the title."
        return ""

    # -- the last note filed, so that "keep that private" knows which one --
    def _remember(self, slug: str, at: datetime, folder: str = FOLDER) -> None:
        self._last_note = (slug, _at(at), self._now(), folder)
        if self._state is not None:
            try:
                self._state.write_text(json.dumps({"slug": slug, "at": _at(at), "filed": self._now().isoformat(), "folder": folder}), encoding="utf-8")
            except OSError:
                pass

    def _last(self) -> tuple[str, str, str] | None:
        last = getattr(self, "_last_note", None)
        if last is None and self._state is not None and self._state.exists():
            try:
                saved = json.loads(self._state.read_text(encoding="utf-8"))
                last = (saved["slug"], saved["at"], datetime.fromisoformat(saved["filed"]), saved.get("folder", FOLDER))
            except (OSError, ValueError, KeyError):
                last = None
        if last is None or self._now() - last[2] > PRIVATE_FOR:
            return None
        return last[0], last[1], last[3]


def _notes(raw: str) -> list[dict[str, Any]]:
    book = yaml.safe_load(raw) or {}
    notes = book.get("notes") if isinstance(book, dict) else None
    return [n for n in notes if isinstance(n, dict)] if isinstance(notes, list) else []


def _at_of(value: Any) -> str:
    if isinstance(value, datetime):
        return _at(value if value.tzinfo else value.replace(tzinfo=IST))
    return str(value)


def _shown_notes(book: dict[str, Any], each: int = 240, most: int = 40) -> list[str]:
    notes = [n for n in book.get("notes") or [] if isinstance(n, dict)]
    shown = [n for n in notes if not n.get("private")]
    hidden = len(notes) - len(shown)
    lines = [f"{len(shown)} note{'s' if len(shown) != 1 else ''} will be public"
             + (f"; {hidden} private note{'s stay' if hidden != 1 else ' stays'} off the site." if hidden else ".")]
    for n in shown[:most]:
        body = (f"“{n['quote']}” " if n.get("quote") else "") + (n.get("text") or "")
        body = " ".join(body.split())
        body = body if len(body) <= each else body[:each - 1] + "…"
        where = f" {n['where']}" if n.get("where") else ""
        lines.append(f"- {n.get('kind', 'thought')}{where}: {body}")
    if len(shown) > most:
        lines.append(f"…and {len(shown) - most} more, the newest.")
    return lines


def publish_text(book: dict[str, Any], slug: str, each: int = 240, most: int = 40) -> str:
    """What the owner is shown before a book goes public: everything that will appear, and what will not."""
    lines = [f"Publish \"{book['title']}\"" + (f" by {book['author']}" if book.get("author") else "") + " on your website?", ""]
    lines.append(f"Status: {book.get('status', 'reading')}" + (f", finished {book['finished']}" if book.get("finished") else "")
                 + (f", started {book['started']}" if book.get("started") else "") + ".")
    if book.get("why"):
        lines.append(f"Why: {book['why']}")
    if book.get("takeaway"):
        lines.append(f"Takeaway: {book['takeaway']}")
    lines.append("")
    lines += _shown_notes(book, each, most)
    lines.append("")
    lines.append(f"It becomes readable at /reading/{slug} once the site rebuilds (a few minutes after you approve).")
    return "\n".join(lines)


def must_text(book: dict[str, Any], slug: str, pitch: str) -> str:
    """The question before a book goes on the Must read shelf: each field before and after, and, if the book is still
    private, everything that going public will show."""
    def yes(v: Any) -> str:
        return "yes" if v is True else "no"
    public = book.get("public") is True
    lines = [f"Put \"{book['title']}\"" + (f" by {book['author']}" if book.get("author") else "") + " on your Must read shelf?", ""]
    lines.append(f"Must read: {yes(book.get('recommended'))} → yes")
    lines.append(f"Public: {yes(book.get('public'))} → yes")
    lines.append(f"Pitch: {book['pitch'] if book.get('pitch') else '(none)'} → {pitch}")
    if not public:
        lines += ["", "The book is private now, so this also publishes it."] + _shown_notes(book)
    lines += ["", f"It shows at /reading/{slug} and on the Must read shelf once the site rebuilds."]
    return "\n".join(lines)


PUBLISH_TOOL = "reading_publish"
MUST_TOOL = "reading_must_read"


class Desk:
    """Where the conversation hands a message that may be a reading command. Returns True if it took the message."""

    def __init__(self, shelf: Shelf, gate: Any, journal: Any):
        self._shelf, self._gate, self._journal = shelf, gate, journal
        self._jobs: set[asyncio.Task] = set()
        if gate is not None:
            from .gate import ASK
            for tool in (PUBLISH_TOOL, MUST_TOOL):
                gate.rule(tool, ASK, describe=lambda a: a["question"])

    async def take(self, text: str, chat: Any, at: datetime | None = None) -> bool:
        cmd = parse(text, at or datetime.now(IST))
        if cmd is None:
            return False
        s = self._shelf
        try:
            if cmd.action == "note":
                reply = await s.add_note(cmd)
            elif cmd.action == "private":
                reply = await s.private_last()
            elif cmd.action == "finish":
                reply = await s.set_finished(cmd.book, cmd.text)
            elif cmd.action == "why":
                reply = await s.set_why(cmd.book, cmd.text)
            elif cmd.action == "save":
                reply = await s.save(cmd)
            elif cmd.action == "problem":
                reply = await s.problem_new(cmd)
            elif cmd.action == "entry":
                reply = await s.problem_entry(cmd)
            else:
                if cmd.action == "publish":
                    asked, tool = await s.publish_question(cmd.book), PUBLISH_TOOL
                else:
                    asked, tool = await s.must_question(cmd.book, cmd.text), MUST_TOOL
                if isinstance(asked, str):
                    reply = asked
                else:
                    job = asyncio.get_running_loop().create_task(self._approve(tool, *asked, chat))
                    self._jobs.add(job)
                    job.add_done_callback(self._jobs.discard)
                    return True
        except BookError as e:
            self._journal.write("reading", action=cmd.action, ok=False, error=str(e)[:200])
            await chat.send(f"Not filed: {e}")
            return True
        except Exception as e:          # the network, most likely; the owner hears, and the message is not lost on the model
            self._journal.write("error", where=f"reading.{cmd.action}", error=type(e).__name__)
            await chat.send(f"I couldn't reach your site's repository ({type(e).__name__}). Nothing was changed; send it again in a moment.")
            return True
        if not reply:
            return False         # no such book, or nothing filed lately: the message was about something else
        await chat.send(reply)
        return True

    async def _approve(self, tool: str, question: str, args: dict[str, Any], chat: Any) -> None:
        """Put the change to the owner through the gate, and make it only on Approve."""
        inputs = {**args, "question": question}

        async def ask_owner(approval: Any) -> None:
            await chat.ask(approval.text, approval.id)

        key = f"reading-{args['book']}"
        self._gate.begin_task(key, ask_owner, f"The owner said: {tool} {args['book']}")
        try:
            approved, why, _ = await self._gate.ask(tool, inputs)
        finally:
            self._gate.end_task(key)
        if not approved:
            if "declined" in why:
                await chat.send("Left as it was.")
            elif "in time" in why:
                await chat.send("You didn't answer, so nothing changed.")
            return
        if not self._gate.guard(tool, inputs):
            await chat.send("That approval did not match, so nothing was changed.")
            return
        try:
            if tool == PUBLISH_TOOL:
                await chat.send(await self._shelf.publish(args["book"], args["sha"]))
            else:
                await chat.send(await self._shelf.must_read(args["book"], args["sha"], args["pitch"]))
        except BookError as e:
            await chat.send(f"Not changed: {e}")
        except Exception as e:
            self._journal.write("error", where=f"reading.{tool}", error=type(e).__name__)
            await chat.send(f"I couldn't reach your site's repository ({type(e).__name__}). Nothing was changed.")
