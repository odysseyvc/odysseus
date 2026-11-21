"""Noticing when private details are about to leave through a tool that needs no approval.

Reading mail is allowed without asking, and so is searching the web. Together
they are a way out: a model misled by something it read could put what it found
in the owner's mailbox into a search query, and the query goes to a search
company and from there to whoever is watching.

This is a plain text check, not an understanding of meaning. Everything read
from the owner's accounts is remembered here as fingerprints:

  - email addresses
  - long numbers (six digits or more, ignoring spaces, commas and hyphens)
  - codes that mix letters and digits (booking references, account numbers)
  - every run of six consecutive words

Before text leaves through web search, the browser or an app, it is compared with
those fingerprints. A match means the owner is shown the text and asked. Anything
the owner typed themselves is exempt: if they wrote the booking reference in their
message, searching for it is their request.

Only a short hash of each fingerprint is kept, never the words. The hashes are
written to a file, because the conversation now survives a restart: if they did
not, the model would still have the mail in front of it after a restart and this
check would know nothing. When the store is full the oldest are dropped, and that
is recorded once.

It also keeps a second, cruder record for the gate: which kinds of outside
material have been read during the current task (sources). Memory and reminders
are written without a question only while that record is empty (tools.py).

Recognising text cannot catch a paraphrase: the same private fact in other words
has no fingerprint. So there is a second check that recognises nothing and
counts instead (spill, below). Once anything has been read from the owner's
accounts, each search is measured by its new words: the words that are not the
owner's own and not ordinary search vocabulary. A search may carry a limited
number of them, and so may each hour. Past either limit the owner is asked.
A long number or a long unbroken string that the owner did not type is asked
about whatever the count. The model's own knowledge is made of new words too,
which is why this is a limit on how much can leave unasked and not a ban.

What the two checks together do not catch: a short private fact, such as a name
or a date, sent once in other words. They bound how much can leave without the
owner seeing it; they do not close the door.
"""
from __future__ import annotations

import hashlib
import re
import struct
import time
from collections import deque
from itertools import islice
from pathlib import Path
from typing import Callable

SHINGLE = 6
MAX_FINGERPRINTS = 300_000

_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}")
_NUMBER = re.compile(r"\d[\d ,-]{4,}\d")
_CODE = re.compile(r"\b(?=[a-z0-9-]*\d)(?=[a-z0-9-]*[a-z])[a-z0-9]{6,}\b")
_WORD = re.compile(r"[a-z0-9]+")
_ANY_WORD = re.compile(r"[^\W_]+")      # a word in any script: a check that saw only Latin letters could be walked round in another

# How much a search may carry that the owner did not say (spill). Set from the real searches on record, with room to
# spare: of 253, the middle one carried 14 new words and one carried more than 30. An hour's allowance is about twenty
# ordinary searches.
NEW_WORDS_A_SEARCH = 30
NEW_WORDS_AN_HOUR = 300
LONG_NUMBER = 5          # digits; a year or a round count is shorter
LONG_WORD = 24           # characters; longer than any ordinary word, as an encoded string is
# Words a search uses whatever it is about. They carry almost nothing about what was read, so they are not counted.
ORDINARY = frozenset("""
a about after all also an and any are as at be been before best between both but by can company companies could did do does
each for from had has have how if in into is it its latest like list may more most much new news no not of on one only or
other our out over recent same should since so some such than that the their them then there these they this those to top
under up use used was were what when where which who whose why will with would
official site website page profile linkedin twitter founder founders cofounder ceo cto team employees staff investor investors
funding funded raise raised raising round seed series valuation revenue customers competitors competitor market industry
sector product products launch launched announced announcement acquisition acquired startup startups business overview
background history headquarters location based review reviews pricing price report reports interview article press release
january february march april may june july august september october november december today year years month week
""".split())


def _tokens(text: str) -> set[str]:
    low = text.lower()
    out = set(_EMAIL.findall(low))
    for n in _NUMBER.findall(low):
        digits = re.sub(r"\D", "", n)
        if len(digits) >= 6:
            out.add(digits)
    out.update(_CODE.findall(low))
    return out


def _shingles(text: str) -> set[str]:
    words = _WORD.findall(text.lower())
    return {" ".join(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


def _h(kind: str, value: str) -> int:
    return int.from_bytes(hashlib.blake2b(f"{kind}:{value}".encode(), digest_size=8).digest(), "big")


class Taint:
    def __init__(self, path: Path | None = None, on_full: Callable[[], None] | None = None, limit: int = MAX_FINGERPRINTS):
        self._known: dict[int, None] = {}       # hashes of what was read, oldest first
        self._owner: set[int] = set()
        self._path, self._on_full, self._limit, self._told_full = path, on_full, limit, False
        self.listeners: list = []      # others who need to know what was read from the accounts (tracing.py)
        self.sources: set[str] = set()     # kinds of outside material read during the current task
        self._said: set[int] = set()       # each word the owner has typed since the program started
        self._spilt: deque[tuple[float, int]] = deque()     # new words that left unasked: when, and how many
        self.clock: Callable[[], float] = time.monotonic
        if path is not None and path.exists():
            raw = path.read_bytes()
            saved = struct.unpack(f">{len(raw) // 8}Q", raw[:len(raw) // 8 * 8])
            self._known = dict.fromkeys(saved[-limit:])
            if len(saved) > limit:      # the file only ever grows while running; trim it here
                self._rewrite()

    def _rewrite(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_bytes(struct.pack(f">{len(self._known)}Q", *self._known))

    def __len__(self) -> int:
        return len(self._known)

    # --- the current task ---------------------------------------------------------------
    def start_task(self) -> None:
        self.sources = set()

    def saw(self, source: str) -> None:
        """Record that something other people wrote was read in this task, without fingerprinting it."""
        self.sources.add(source)

    # --- what was read ------------------------------------------------------------------
    def add(self, text: str, source: str = "your mail, calendar or apps") -> None:
        """Remember something read from the owner's accounts."""
        self.sources.add(source)
        for listener in self.listeners:
            try:
                listener(text)
            except Exception:
                pass
        new = [h for h in ([_h("t", t) for t in _tokens(text)] + [_h("s", s) for s in _shingles(text)]) if h not in self._known]
        if not new:
            return
        self._known.update(dict.fromkeys(new))
        over = len(self._known) - self._limit
        if over > 0:
            for h in list(islice(self._known, over)):      # full: the oldest go, so that what was read lately still counts
                del self._known[h]
            if not self._told_full:
                self._told_full = True
                if self._on_full is not None:
                    self._on_full()
        if self._path is not None:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("ab") as f:
                    f.write(struct.pack(f">{len(new)}Q", *new))
            except OSError:
                pass

    def owner_said(self, text: str) -> None:
        """Remember the owner's own words, which are theirs to send anywhere."""
        self._owner |= {_h("t", t) for t in _tokens(text)} | {_h("s", s) for s in _shingles(text)}
        self._said |= {_h("w", w) for w in _ANY_WORD.findall(text.lower())}

    # --- what would leave, counted ------------------------------------------------------
    def new_words(self, text: str) -> list[str]:
        """The words in `text` that the owner did not type and that are not ordinary search vocabulary, each once."""
        seen: dict[str, None] = {}
        for w in _ANY_WORD.findall(text.lower()):
            if w in ORDINARY or (w.isdigit() and len(w) < LONG_NUMBER) or _h("w", w) in self._said:
                continue
            seen[w] = None
        return list(seen)

    def spill(self, text: str, most: int | None = None) -> str:
        """Why `text` should not leave without the owner seeing it, judged by counting and not by recognising. Empty if
        it may. Nothing is counted until something has been read from the owner's accounts: before that there is
        nothing to carry out.

        `most` is for a task handed to the browser, which is instructions in sentences and not a query of a few words:
        it is allowed that many words that are not the owner's, a long word in it is ordinary (an address, a site's
        name), and it is not added to the hour's count of searches. A long number the owner did not type still asks."""
        if not self._known:
            return ""
        new = self.new_words(text)
        dense = [w for w in new if (w.isdigit() and len(w) >= LONG_NUMBER) or (most is None and len(w) >= LONG_WORD)]
        if dense:
            return f"it carries {dense[0][:40]}, which you did not type"
        if most is not None:
            return f"it carries {len(new)} words that are not yours, and {most} is the most I hand over unasked" if len(new) > most else ""
        if len(new) > NEW_WORDS_A_SEARCH:
            return f"it carries {len(new)} words that are not yours, and {NEW_WORDS_A_SEARCH} is the most I send unasked"
        now = self.clock()
        while self._spilt and now - self._spilt[0][0] >= 3600:
            self._spilt.popleft()
        if sum(n for _, n in self._spilt) + len(new) > NEW_WORDS_AN_HOUR:
            return (f"my searches in the last hour have carried {sum(n for _, n in self._spilt)} words that are not yours, "
                    f"and {NEW_WORDS_AN_HOUR} an hour is the most I send unasked")
        return ""

    def spent(self, text: str) -> None:
        """Count a search that left without the owner being asked. One they approved is not counted: they saw it."""
        if self._known:
            self._spilt.append((self.clock(), len(self.new_words(text))))

    def known(self, token: str) -> bool:
        """Whether an address or a code is in the owner's own words or in anything read from their accounts."""
        h = _h("t", token.strip().lower())
        return h in self._known or h in self._owner

    def leaked(self, text: str) -> list[str]:
        """Private details found in `text`, most specific first. Empty means nothing was recognised."""
        def hit(kind: str, value: str) -> bool:
            h = _h(kind, value)
            return h in self._known and h not in self._owner
        found = sorted(t for t in _tokens(text) if hit("t", t))
        runs = sorted(s for s in _shingles(text) if hit("s", s))
        return found + [f'"{r}"' for r in runs[:3]]
