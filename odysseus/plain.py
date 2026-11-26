"""What an action would do, written for a person and not for a machine.

A question put to the owner used to show the action as the service spells it and its inputs as the program holds them:

    Action: GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND
    With:
    { "spreadsheetId": "1ONA...", "values": [ [ "23 Oct", "Asha", ...

Every character of it was true and almost none of it could be read on a phone. The owner approved fourteen rows of a
spreadsheet by scrolling through sixty lines of brackets, cut across three messages wherever the limit fell. A question
nobody can read is approved without being read, which is the failure the question exists to prevent.

This module turns the same action and the same inputs into lines a person reads: what will be done, in words; rows as rows;
fields by name; and the identifiers a person cannot use (a sheet's id, a range) last, on one line. Nothing is left out and
nothing is summarised: the rule that the owner sees everything they approve is kept, and the gate still refuses a question
too long to show whole. It knows no service. It works from the shape of the inputs alone: a list of lists is a table, a
list of things with fields is a list, a long opaque string is an identifier.
"""
from __future__ import annotations

import re
from typing import Any

_OPAQUE = re.compile(r"[A-Za-z0-9_\-]{20,}")            # an id: long, and with no spaces
_SMALL = {"a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "by", "with"}


def words(name: str) -> str:
    """spreadsheetId -> spreadsheet id; value_input_option -> value input option."""
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(name)).replace("_", " ").replace("-", " ")
    return " ".join(name.split()).lower()


def action(slug: str, description: str = "") -> str:
    """What the action does, in a few words: the service's own first sentence if it gave one, else its name read as words."""
    first = re.split(r"(?<=[.!?])\s", " ".join(str(description).split()), maxsplit=1)[0].rstrip(".")
    if 8 <= len(first) <= 120:
        return first[0].upper() + first[1:]
    parts = [p for p in str(slug).split("_")[1:] if p] or [str(slug)]
    return " ".join(parts).capitalize()


def app(name: str) -> str:
    """googlesheets -> Google Sheets: the name a person knows the app by, where the service gave only its own short name."""
    low = str(name).replace("_", " ").strip()
    for maker in ("google", "microsoft"):
        if low.lower().startswith(maker) and len(low) > len(maker) and " " not in low:
            low = maker + " " + low[len(maker):]
    return " ".join(w if w.lower() in _SMALL else w[:1].upper() + w[1:] for w in low.split())


def _cell(v: Any) -> str:
    if v is None or v == "":
        return "(empty)"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return " ".join(str(v).split()) if not isinstance(v, (dict, list)) else _inline(v)


def _inline(v: Any) -> str:
    if isinstance(v, dict):
        return "; ".join(f"{words(k)}: {_cell(x)}" for k, x in v.items())
    if isinstance(v, list):
        return ", ".join(_cell(x) for x in v)
    return _cell(v)


def _is_table(v: Any) -> bool:
    return isinstance(v, list) and len(v) > 0 and all(isinstance(r, list) for r in v)


def _is_records(v: Any) -> bool:
    return isinstance(v, list) and len(v) > 0 and all(isinstance(r, dict) for r in v)


def inputs(args: dict[str, Any]) -> str:
    """The inputs of an action as lines to read. What a person can judge comes first; identifiers come last, on one line."""
    lines, ids = [], []
    for key, value in (args or {}).items():
        label = words(key)
        label = label[:1].upper() + label[1:]
        if isinstance(value, str) and _OPAQUE.fullmatch(value.strip()):
            ids.append(f"{words(key)} {value.strip()}")
        elif _is_table(value):
            lines.append(f"{label} ({len(value)} {'row' if len(value) == 1 else 'rows'}):")
            lines += [f"{n}. " + " | ".join(_cell(c) for c in row) for n, row in enumerate(value, 1)]
        elif _is_records(value):
            lines.append(f"{label} ({len(value)}):")
            lines += [f"{n}. {_inline(item)}" for n, item in enumerate(value, 1)]
        elif isinstance(value, dict) and value:
            lines.append(f"{label}:")
            lines += [f"   {words(k)}: {_cell(x)}" for k, x in value.items()]
        else:
            lines.append(f"{label}: {_cell(value)}")
    if ids:
        lines.append("(" + "; ".join(ids) + ")")
    return "\n".join(lines) if lines else "(no inputs)"
