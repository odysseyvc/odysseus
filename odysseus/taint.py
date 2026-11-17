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

Before text leaves through web search or the browser, it is compared with those
fingerprints. A match means the owner is shown the text and asked. Anything the
owner typed themselves is exempt: if they wrote the booking reference in their
message, searching for it is their request.

What this does not catch: a paraphrase, a detail split across several searches,
or a short fact such as a name or a date. It raises the cost of the crude
version of the attack; it does not close the door.
"""
from __future__ import annotations

import re

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


class Taint:
    def __init__(self):
        self._tokens: set[str] = set()
        self._shingles: set[str] = set()
        self._owner_tokens: set[str] = set()
        self._owner_shingles: set[str] = set()

    def add(self, text: str) -> None:
        """Remember something read from the owner's accounts."""
        if len(self._tokens) + len(self._shingles) > MAX_FINGERPRINTS:
            return      # full: stop learning; what is already known still counts
        self._tokens |= _tokens(text)
        self._shingles |= _shingles(text)

    def owner_said(self, text: str) -> None:
        """Remember the owner's own words, which are theirs to send anywhere."""
        self._owner_tokens |= _tokens(text)
        self._owner_shingles |= _shingles(text)

    def leaked(self, text: str) -> list[str]:
        """Private details found in `text`, most specific first. Empty means nothing was recognised."""
        found = sorted((_tokens(text) & self._tokens) - self._owner_tokens)
        runs = sorted((_shingles(text) & self._shingles) - self._owner_shingles)
        return found + [f'"{r}"' for r in runs[:3]]
