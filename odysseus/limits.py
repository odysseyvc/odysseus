"""A length the owner set is counted by code, because the model does not count.

Asked for "at most 150 words" the assistant wrote 176 to 182, three times in three; asked for "under 80" it wrote 84 to
92. The answers were right and a fifth too long, every time, so the fault is not carelessness about the request: a model
does not count the words it writes, it writes what feels like the length asked for, and that feeling runs long.

Two things by code. Before the task, a limit found in the owner's own message is restated to the model as a target a
fifth lower, which is the size of the overshoot measured: asked for 150, it is told to aim for about 120. After the
task, the reply is counted (`over`), so that an overshoot is on record and can be told to the owner or sent back.

Only a limit the owner states as a number of words, lines or sentences is read: "at most 150 words", "under 80 words",
"in 60 words or fewer", "two lines at most", "no more than three sentences". "Briefly" and "keep it short" are not
numbers and are left to the model.
"""
from __future__ import annotations

import re

_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15,
        "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "eighty": 80, "hundred": 100}
_N = r"(\d{1,4}|" + "|".join(_NUM) + r")"
_UNIT = r"(words?|lines?|sentences?)"
_BEFORE = re.compile(r"\b(?:at most|no more than|not more than|up to|max(?:imum)?(?: of)?|under|fewer than|less than|within|in)\s+" + _N + r"\s+" + _UNIT + r"\b", re.I)
_AFTER = re.compile(r"\b" + _N + r"\s+" + _UNIT + r"\s+(?:at most|or fewer|or less|max(?:imum)?|tops)\b", re.I)
_STRICT = re.compile(r"\b(?:under|fewer than|less than)\b", re.I)
_WORD = re.compile(r"[^\W_]+(?:['’.,\-/][^\W_]+)*")
SLACK = 0.7          # the target given for words, as a share of the limit: replies overran a target of four fifths once they could carry lists and labels
HEAD = 700           # characters of a message in which a limit is looked for


def stated(message: str) -> tuple[int, str] | None:
    """The limit in the owner's message as (most allowed, unit), or None. "Under 80 words" allows 79."""
    message = (message or "")[:HEAD]        # the owner's own request, not a long thing pasted under it
    for pattern in (_BEFORE, _AFTER):
        m = pattern.search(message)
        if m:
            raw = m.group(1).lower()
            n = int(raw) if raw.isdigit() else _NUM[raw]
            unit = m.group(2).lower().rstrip("s")
            if _STRICT.match(m.group(0)):
                n -= 1
            return (n, unit) if n > 0 else None
    return None


def count(text: str, unit: str) -> int:
    if unit == "word":
        return len(_WORD.findall(text))
    if unit == "line":
        return sum(1 for line in text.splitlines() if line.strip())
    return len([s for s in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if s.strip()])


def note(message: str) -> str:
    """What code adds to the owner's message for the model, or "". The limit itself is unchanged; the target is lower."""
    found = stated(message)
    if not found:
        return ""
    n, unit = found
    aim = max(1, int(n * SLACK)) if unit == "word" else n
    return (f"\n\n[Added by the program, not by the owner: the owner set a limit of {n} {unit}{'s' if n != 1 else ''} for the whole reply. "
            f"Replies to such requests have run well over their limit, so write no more than {aim} {unit}{'s' if aim != 1 else ''}. Lists, labels, headings and "
            "every line count, and a labelled list spends words a sentence does not: under a limit, write tight sentences and keep marks "
            "to a few words in bold. Cut detail before you cut anything the owner asked for by name.]")


def over(message: str, reply: str) -> str:
    """"" if the reply keeps the owner's limit or none was set; otherwise how far over it is, in words for the journal."""
    found = stated(message)
    if not found:
        return ""
    n, unit = found
    got = count(reply, unit)
    return f"{got} {unit}s against a limit of {n}" if got > n else ""
