"""The shape of the owner's Attio workspace, read from Attio and written out for the model.

Without this the model begins every conversation knowing nothing of the workspace. In the first live runs it
spent about twenty reads per request finding out which objects, lists and fields exist, and once filled Attio's
built-in "job title" where the firm's own "Role" field was meant, because it had not seen that there was one.

Attio answers in a compact text format of its own (keys, indented blocks, and small tables with a header row),
not in JSON. `parse` reads it. `describe` turns the parsed answers into a page of plain lines: each object and
list, each field with its slug, type and choices. It is read from Attio itself and not written by hand, so it is
right whatever the firm does to its layout.
"""
from __future__ import annotations

import csv
import json
import re
from typing import Any, Awaitable, Callable

_KEY = re.compile(r"^([\w.\-]*)(?:\[(\d+)\])?(?:\{([^}]*)\})?:(?:\s(.*))?$")      # the name may be empty: a list with no name, "[2]:"


def _scalar(v: str) -> Any:
    v = v.strip()
    if v.startswith('"'):
        try:
            return json.loads(v)
        except ValueError:
            return v.strip('"')
    return {"null": None, "true": True, "false": False}.get(v, v)


def _row(line: str) -> list[Any]:
    return [_scalar(c) if not c.startswith('"') else c for c in next(csv.reader([line.strip()]))]


def parse(text: str) -> dict[str, Any]:
    """Attio's text format, as nested dicts and lists. Lines it cannot read are skipped, never guessed at."""
    lines = [(len(l) - len(l.lstrip(" ")), l.strip()) for l in text.splitlines() if l.strip()]

    def block(i: int, indent: int) -> tuple[dict[str, Any], int]:
        out: dict[str, Any] = {}
        while i < len(lines) and lines[i][0] >= indent:
            ind, content = lines[i]
            if ind > indent or content.startswith("- "):
                i += 1
                continue
            m = _KEY.match(content)
            if not m:
                i += 1
                continue
            key, n, cols, rest = m.group(1), m.group(2), m.group(3), m.group(4)
            i += 1
            if cols is not None:                                   # a table: header, then one row a line
                names, rows = [c.strip() for c in cols.split(",")], []
                while i < len(lines) and lines[i][0] > indent and len(rows) < int(n or 0):
                    rows.append(dict(zip(names, _row(lines[i][1]))))
                    i += 1
                out[key] = rows
            elif n is not None and rest:                           # a short list on one line
                out[key] = _row(rest)
            elif n is not None:                                    # a list of items, each begun by "- "
                items: list[Any] = []
                while i < len(lines) and lines[i][0] > indent and lines[i][1].startswith("- "):
                    dash_indent, first = lines[i]
                    if not _KEY.match(first[2:]):
                        items.append(_scalar(first[2:]))
                        i += 1
                        continue
                    lines[i] = (dash_indent + 2, first[2:])
                    item, i = block(i, dash_indent + 2)
                    items.append(item)
                out[key] = items
            elif rest is not None and rest != "":
                out[key] = _scalar(rest)
            elif i < len(lines) and lines[i][0] > indent and not lines[i][1].startswith("- "):
                out[key], i = block(i, lines[i][0])
            else:
                out[key] = None
        return out, i

    return block(0, lines[0][0])[0] if lines else {}


def _field(a: dict[str, Any]) -> str:
    bits = [str(a.get("type") or "?")]
    if a.get("is_multiselect") is True:
        bits.append("many")
    if a.get("is_required") is True:
        bits.append("required")
    if a.get("currency_code"):
        bits.append(str(a["currency_code"]))
    line = f"  {a.get('api_slug')} ({', '.join(bits)}): {a.get('title')}"
    if a.get("description"):
        line += f". {' '.join(str(a['description']).split())[:120]}"
    choices = [str(o.get("title")) for o in (a.get("statuses") or a.get("select_options") or []) if isinstance(o, dict) and o.get("title")]
    if choices:
        line += f" Choices, in order: {' | '.join(choices[:40])}"
    return line


def _fields(attrs: list[dict[str, Any]]) -> list[str]:
    live = [a for a in attrs if isinstance(a, dict) and a.get("api_slug") and a.get("is_archived") is not True]
    out = [_field(a) for a in live if a.get("is_writable") is True]
    fixed = [str(a["api_slug"]) for a in live if a.get("is_writable") is not True]
    if fixed:
        out.append(f"  Filled in by Attio, not by you: {', '.join(fixed)}")
    return out


async def read(call: Callable[[str, dict[str, Any]], Awaitable[str]]) -> dict[str, Any]:
    """Read the workspace's layout through `call(tool, inputs)`. Raises whatever `call` raises."""
    shape: dict[str, Any] = {"me": parse(await call("whoami", {})), "objects": {}, "lists": {}}
    for obj in parse(await call("list-objects", {})).get("objects") or []:
        slug, attrs, offset = obj.get("api_slug"), [], 0
        while slug and offset < 300:
            page = parse(await call("list-attribute-definitions", {"object": slug, "limit": 30, "offset": offset}))
            attrs += page.get("attribute_definitions") or []
            offset += 30
            if page.get("has_more") is not True:
                break
        if slug:
            shape["objects"][slug] = {"plural": obj.get("plural_noun"), "attrs": [a for a in attrs if isinstance(a, dict)]}
    for lst in parse(await call("list-lists", {})).get("lists") or []:
        slug = lst.get("api_slug")
        parents = lst.get("parent_objects") or lst.get("parent_object") or []
        page = parse(await call("list-list-attribute-definitions", {"list": slug}))
        if slug:
            shape["lists"][slug] = {"name": lst.get("name"), "parents": [parents] if isinstance(parents, str) else list(parents),
                                    "attrs": [a for a in page.get("attribute_definitions") or [] if isinstance(a, dict)]}
    return shape


def render(shape: dict[str, Any], limit: int = 12000) -> str:
    """The layout as a page of plain lines for the model."""
    me = shape.get("me") or {}
    lines = [f"The workspace is \"{me.get('workspace_name')}\". The owner is signed in as {me.get('name')}, workspace member "
             f"{me.get('workspace_member_id')}; use that id wherever an owner or member is wanted and the owner means themselves."]
    for slug, obj in (shape.get("objects") or {}).items():
        lines.append(f"\nObject `{slug}` ({obj.get('plural')}). Fields you can set:")
        lines += _fields(obj.get("attrs") or [])
    for slug, lst in (shape.get("lists") or {}).items():
        lines.append(f"\nList `{slug}` (\"{lst.get('name')}\"), a list of {', '.join(map(str, lst.get('parents') or ['?']))}. "
                     "Fields that belong to an entry in this list, not to the record:")
        lines += _fields(lst.get("attrs") or [])
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit] + "\n[Cut. List the attributes of an object yourself if a field you need is not shown.]"


async def describe(call: Callable[[str, dict[str, Any]], Awaitable[str]], limit: int = 12000) -> str:
    return render(await read(call), limit)
