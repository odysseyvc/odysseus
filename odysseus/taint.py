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

What this does not catch: a paraphrase, a detail split across several searches,
or a short fact such as a name or a date. It raises the cost of the crude
version of the attack; it does not close the door. Treat it as a hint.
"""
from __future__ import annotations

import hashlib
import re
import struct
from itertools import islice
from pathlib import Path
from typing import Callable

SHINGLE = 6
MAX_FINGERPRINTS = 300_000

_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}")
_NUMBER = re.compile(r"\d[\d ,-]{4,}\d")
_CODE = re.compile(r"\b(?=[a-z0-9-]*\d)(?=[a-z0-9-]*[a-z])[a-z0-9]{6,}\b")
_WORD = re.compile(r"[a-z0-9]+")


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

    def leaked(self, text: str) -> list[str]:
        """Private details found in `text`, most specific first. Empty means nothing was recognised."""
        def hit(kind: str, value: str) -> bool:
            h = _h(kind, value)
            return h in self._known and h not in self._owner
        found = sorted(t for t in _tokens(text) if hit("t", t))
        runs = sorted(s for s in _shingles(text) if hit("s", s))
        return found + [f'"{r}"' for r in runs[:3]]
