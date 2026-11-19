"""A stand-in for a firm's customer database and its meeting notes, for the partner errands (partner.py).

`FakeAttio` answers the same calls as Attio's own server, in the same compact text format, and keeps what is written
to it, so that a check can look at the database after an errand and say what is in it. `FakeGranola` holds a few
meetings. Everything in them is invented. The program under test reaches them through the same classes it uses for
the real services (odysseus/attio.py, odysseus/mcpapp.py), so the layout reading, the plan checking and the gate are
the real ones.
"""
from __future__ import annotations

import json
import re
from typing import Any

from odysseus.apps import AppError

ME = {"workspace_member_id": "00000000-0000-4000-8000-00000000aaaa", "name": "Pat Owner", "email": "pat@acme-capital.example",
      "access_level": "admin", "workspace_slug": "acme-capital", "workspace_name": "Acme Capital"}


def _fmt(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    s = str(v)
    return json.dumps(s, ensure_ascii=False) if (s == "" or any(c in s for c in ',"\n') or s != s.strip()) else s


def dump(obj: Any, indent: int = 0, key: str = "") -> list[str]:
    """Attio's text format: keys, indented blocks, and lists whose items begin with a dash."""
    pad = " " * indent
    if isinstance(obj, dict):
        lines = [f"{pad}{key}:"] if key else []
        inner = indent + 2 if key else indent
        for k, v in obj.items():
            lines += dump(v, inner, k)
        return lines
    if isinstance(obj, list):
        if all(not isinstance(x, (dict, list)) for x in obj):
            return [f"{pad}{key}[{len(obj)}]:" + (" " + ",".join(_fmt(x) for x in obj) if obj else "")]
        lines = [f"{pad}{key}[{len(obj)}]:"]
        for item in obj:
            body = dump(item, indent + 4)
            lines += [" " * (indent + 2) + "- " + body[0].strip()] + body[1:] if body else []
        return lines
    return [f"{pad}{key}: {_fmt(obj)}"]


def text(obj: Any) -> str:
    return "\n".join(dump(obj))


def A(slug: str, title: str, kind: str = "text", many: bool = False, required: bool = False, writable: bool = True,
      options: tuple[str, ...] = (), desc: str | None = None, currency: bool = False) -> dict[str, Any]:
    a: dict[str, Any] = {"attribute_id": f"attr-{slug}", "title": title, "description": desc, "api_slug": slug, "is_required": required,
                         "is_multiselect": many, "is_writable": writable, "is_archived": False, "is_unique": slug == "record_id", "type": kind}
    if kind == "status":
        a["statuses"] = [{"order": i + 1, "title": t, "id": f"st-{i}"} for i, t in enumerate(options)]
    elif options:
        a["select_options"] = [{"select_option_id": f"opt-{i}", "title": t} for i, t in enumerate(options)]
    if kind == "currency":
        a["currency_code"] = "USD"
    return a


STAGES = ("Sourced", "Contacted", "Met", "Diligence", "IC", "Term sheet", "Invested", "Passed")
ATTRS: dict[str, list[dict[str, Any]]] = {
    "companies": [A("record_id", "Record ID", writable=False), A("name", "Name"), A("domains", "Domains", "domain", many=True),
                  A("description", "Description"),
                  A("relationship", "Relationship", "select", many=True, options=("Prospect", "Portfolio", "Co-investor", "LP", "Advisor")),
                  A("company_stage", "Company stage", "select", options=("Pre-seed", "Seed", "Series A", "Series B+")),
                  A("associated_deals", "Associated deals", "record-reference", many=True, writable=False),
                  A("last_interaction", "Last interaction", "interaction", writable=False)],
    "people": [A("record_id", "Record ID", writable=False), A("name", "Name", "personal-name"),
               A("email_addresses", "Email addresses", "email-address", many=True), A("linkedin", "LinkedIn"), A("job_title", "Job title"),
               A("company", "Company", "record-reference"), A("associated_deals", "Associated deals", "record-reference", many=True, writable=False),
               A("role", "Role", "select", many=True, options=("Founder", "Investor", "LP", "Operator", "Advisor", "Scout"))],
    "deals": [A("record_id", "Record ID", writable=False), A("name", "Deal name", required=True),
              A("stage", "Deal stage", "status", required=True, options=STAGES), A("owner", "Deal owner", "actor-reference", required=True),
              A("value", "Deal value", "currency", desc="Our cheque."), A("associated_company", "Associated company", "record-reference"),
              A("associated_people", "Associated people", "record-reference", many=True),
              A("round", "Round", "select", options=("Pre-seed", "Seed", "Series A", "Series B+", "Bridge", "Other")),
              A("round_size", "Round size", "currency"),
              A("allocation_offered", "Allocation offered", "currency", desc="What we were offered or asked for, which may differ from our cheque (Deal value)."),
              A("source", "Source", "select", options=("Inbound", "Founder referral", "Investor referral", "Network", "Outbound", "Event", "Scout")),
              A("introduced_by", "Introduced by", "record-reference"), A("next_step", "Next step"), A("next_step_due", "Next step due", "date"),
              A("pass_reasons", "Pass reasons", "select", many=True, options=("Too early", "Team", "Market", "Traction", "Valuation"))],
}
LISTS = {"portfolio": {"name": "Portfolio", "parents": ["companies"],
                       "attrs": [A("entry_id", "Entry ID", writable=False), A("ownership", "Ownership", "number", desc="Percent."),
                                 A("initial_cheque", "Initial cheque", "currency"), A("runway", "Runway", "number", desc="Months.")]}}

READ = {"read_only_hint": True, "destructive_hint": False, "open_world_hint": False}
WRITE = {"read_only_hint": False, "destructive_hint": True, "open_world_hint": False}
OBJ = {"type": "string", "description": "companies, people or deals."}
TOOLS = [
    ("whoami", "Who am I", READ, "Who is signed in, and to which workspace.", {}),
    ("list-objects", "List objects", READ, "List the workspace's objects.", {}),
    ("list-attribute-definitions", "List attribute definitions", READ, "List attribute definitions for a given object.",
     {"object": OBJ, "limit": {"type": "number", "default": 20, "maximum": 30}, "offset": {"type": "number"}, "query": {"type": "string"}}),
    ("list-lists", "List lists", READ, "List the workspace's lists.", {}),
    ("list-list-attribute-definitions", "List list attribute definitions", READ, "List attribute definitions for a given list.", {"list": {"type": "string"}}),
    ("search-records", "Search records", READ, "Fuzzy search for records of one object by name, domain or email address.",
     {"object": OBJ, "query": {"type": "string"}, "limit": {"type": "number"}}),
    ("list-records", "List records", READ, "List records of an object, optionally filtered: {attribute, op (eq|contains), value}, or {and|or: [...]}.",
     {"object": OBJ, "filter": {"type": "object"}, "limit": {"type": "number"}, "offset": {"type": "number"}}),
    ("get-records-by-ids", "Get records by IDs", READ, "Retrieve records by their IDs.", {"object": OBJ, "record_ids": {"type": "array", "items": {"type": "string"}}}),
    ("list-records-in-list", "List records in list", READ, "List the entries of a list, with each entry's own values.", {"list": {"type": "string"}}),
    ("search-notes-by-metadata", "Search notes by metadata", READ, "List notes, optionally only those on one record. Returns titles and the start of each note.",
     {"parent_object": OBJ, "parent_record_id": {"type": "string"}}),
    ("get-note-body", "Get note body", READ, "The full text of one note.", {"note_id": {"type": "string"}}),
    ("create-record", "Create record", WRITE, "Create a record. values maps attribute slugs to values. A link to another record is "
     "{target_object, target_record_id}. A person's name is {first_name, last_name, full_name}.", {"object": OBJ, "values": {"type": "object"}}),
    ("update-record", "Update record", WRITE, "Update a record by record_id. Values for a multi-select attribute are added to those already there.",
     {"object": OBJ, "record_id": {"type": "string"}, "values": {"type": "object"}}),
    ("add-record-to-list", "Add record to list", WRITE, "Add a record to a list, with the entry's own values.",
     {"list": {"type": "string"}, "parent_object": OBJ, "parent_record_id": {"type": "string"}, "entry_values": {"type": "object"}}),
    ("create-note", "Create note", WRITE, "Create a note on a record.",
     {"parent_object": OBJ, "parent_record_id": {"type": "string"}, "title": {"type": "string"}, "content": {"type": "string"}}),
]


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


class FakeAttio:
    def __init__(self):
        self.records: dict[str, dict[str, Any]] = {}       # id -> {"object", "values"}
        self.entries: list[dict[str, Any]] = []
        self.notes: list[dict[str, Any]] = []
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.calls: list[str] = []
        self._n = 0

    # --- for seeding and for checks ---------------------------------------------------------------------------
    def _id(self) -> str:
        self._n += 1
        return f"00000000-0000-4000-8000-{self._n:012d}"

    def add(self, obj: str, **values: Any) -> str:
        rid = self._id()
        self.records[rid] = {"object": obj, "values": self._clean(obj, values)}
        return rid

    def note(self, rid: str, title: str, content: str) -> None:
        self.notes.append({"note_id": self._id(), "parent_object": self.records[rid]["object"], "parent_record_id": rid, "title": title, "content": content})

    def of(self, obj: str) -> list[tuple[str, dict[str, Any]]]:
        return [(rid, r["values"]) for rid, r in self.records.items() if r["object"] == obj]

    def named(self, obj: str, name: str) -> list[tuple[str, dict[str, Any]]]:
        return [(rid, v) for rid, v in self.of(obj) if _norm(name) in _norm(v.get("name"))]

    def notes_on(self, rid: str) -> list[dict[str, Any]]:
        return [n for n in self.notes if n["parent_record_id"] == rid]

    # --- values ---------------------------------------------------------------------------------------------
    def _link(self, v: Any) -> Any:
        if isinstance(v, dict) and v.get("target_record_id"):
            if v["target_record_id"] not in self.records:
                raise AppError(f"No record with id {v['target_record_id']}.")
            return {"object_slug": self.records[v["target_record_id"]]["object"], "record_id": v["target_record_id"]}
        if isinstance(v, dict) and v.get("record_id"):
            return v
        raise AppError("A record reference must be {target_object, target_record_id}.")

    def _clean(self, obj: str, values: dict[str, Any], attrs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        defs = {a["api_slug"]: a for a in (attrs or ATTRS[obj])}
        out: dict[str, Any] = {}
        for slug, v in values.items():
            a = defs.get(slug)
            if a is None or not a["is_writable"]:
                raise AppError(f"Unknown or read-only attribute '{slug}' on {obj}.")
            kind = a["type"]
            if kind == "personal-name":
                if isinstance(v, list) and v:
                    v = v[0]
                v = v.get("full_name") if isinstance(v, dict) else (" ".join(reversed([p.strip() for p in v.split(",", 1)])) if "," in str(v) else str(v))
            elif kind in ("select", "status"):
                titles = [o["title"] for o in a.get("statuses") or a.get("select_options") or []]
                wanted = v if isinstance(v, list) else [v]
                canon = []
                for w in wanted:
                    hit = next((t for t in titles if _norm(t) == _norm(w)), None)
                    if hit is None:
                        raise AppError(f"'{w}' is not an option of {slug}. Options: {', '.join(titles)}.")
                    canon.append(hit)
                v = canon if a["is_multiselect"] else canon[0]
            elif kind == "record-reference":
                v = [self._link(x) for x in v] if isinstance(v, list) else self._link(v)
                if a["is_multiselect"] and not isinstance(v, list):
                    v = [v]
            elif kind in ("currency", "number"):
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise AppError(f"{slug} must be a number.")
            elif kind == "actor-reference":
                v = {"workspace_membership_id": ME["workspace_member_id"]} if _norm(v) in (_norm(ME["email"]), _norm(ME["workspace_member_id"])) else {"actor": str(v)}
            elif a["is_multiselect"] and not isinstance(v, list):
                v = [v]
            out[slug] = v
        return out

    def _view(self, obj: str, values: dict[str, Any], attrs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        defs = {a["api_slug"]: a for a in (attrs or ATTRS[obj])}
        return {k: (f"US${v:,.2f}" if defs.get(k, {}).get("type") == "currency" and isinstance(v, (int, float)) else v) for k, v in values.items()}

    def _row(self, rid: str) -> dict[str, Any]:
        r = self.records[rid]
        shown = self._view(r["object"], r["values"])
        if r["object"] in ("companies", "people"):      # as the real one does: the deals that point at this record
            back = [d for d, v in self.of("deals") if rid in {x.get("record_id") for x in _refs(v.get("associated_company")) + _refs(v.get("associated_people"))}]
            if back:
                shown = {**shown, "associated_deals": [{"object_slug": "deals", "record_id": d} for d in back]}
        return {"record_id": rid, "attributes": shown}

    def _matches(self, values: dict[str, Any], f: dict[str, Any] | None) -> bool:
        if not f:
            return True
        if "and" in f:
            return all(self._matches(values, x) for x in f["and"])
        if "or" in f:
            return any(self._matches(values, x) for x in f["or"])
        have = values.get(str(f.get("attribute")))
        want_raw = f.get("value")
        if isinstance(want_raw, dict):                  # a filter on a link: by the record it points at
            want_raw = want_raw.get("target_record_id") or want_raw.get("record_id")
            have = [x.get("record_id") for x in _refs(have)]
        have_all = [_norm(x) for x in (have if isinstance(have, list) else [have])] if have is not None else []
        want, op = _norm(want_raw), f.get("op", "eq")
        if op in ("eq",):
            return want in have_all
        if op in ("neq", "not_eq"):
            return want not in have_all
        if op == "contains":
            return any(want in h for h in have_all)
        if op in ("empty",):
            return not have_all
        if op in ("not_empty",):
            return bool(have_all)
        raise AppError(f"Unsupported operator '{op}'. Use eq, neq, contains, empty or not_empty.")

    # --- the server's interface -------------------------------------------------------------------------------
    async def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": n, "title": t, "labels": {"title": t, **labels}, "description": d,
                 "inputs": {"type": "object", "properties": props}} for n, t, labels, d, props in TOOLS]

    async def call(self, name: str, a: dict[str, Any]) -> str:
        self.calls.append(name)
        if name == "whoami":
            return text({**ME, "teams": []})
        if name == "list-objects":
            return text({"objects": [{"object_id": f"obj-{o}", "api_slug": o, "singular_noun": o[:-1].capitalize() if o != "people" else "Person",
                                      "plural_noun": o.capitalize(), "is_standard_object": True} for o in ATTRS], "has_more": False})
        if name == "list-attribute-definitions":
            if a.get("object") not in ATTRS:
                raise AppError(f"No object '{a.get('object')}'.")
            defs = [d for d in ATTRS[a["object"]] if not a.get("query") or _norm(a["query"]) in _norm(d["title"])]
            off, lim = int(a.get("offset") or 0), int(a.get("limit") or 20)
            return text({"attribute_definitions": defs[off:off + lim], "has_more": off + lim < len(defs)})
        if name == "list-lists":
            return text({"lists": [{"list_id": f"list-{s}", "name": l["name"], "api_slug": s, "parent_objects": l["parents"]} for s, l in LISTS.items()], "has_more": False})
        if name == "list-list-attribute-definitions":
            if a.get("list") not in LISTS:
                raise AppError(f"No list '{a.get('list')}'.")
            return text({"attribute_definitions": LISTS[a["list"]]["attrs"], "has_more": False})
        if name in ("search-records", "list-records"):
            obj = a.get("object")
            if obj not in ATTRS:
                raise AppError("`object` is required: companies, people or deals.")
            rows = self.of(obj)
            if name == "search-records":
                q = _norm(a.get("query"))
                q = re.sub(r"^https?://(www\.)?", "", q).rstrip("/")
                rows = [(rid, v) for rid, v in rows if q and any(q in _norm(x) for x in _flat(v))]
                return text({"results": [self._row(rid) for rid, _ in rows[:int(a.get("limit") or 10)]], "has_more_results": False})
            rows = [(rid, v) for rid, v in rows if self._matches(v, a.get("filter"))]
            return text({"records": [self._row(rid) for rid, _ in rows[:int(a.get("limit") or 50)]], "has_more": False})
        if name == "get-records-by-ids":
            rows = [self._row(rid) for rid in a.get("record_ids") or [] if rid in self.records]
            return "\n".join(dump(rows, 0, "")).replace(f"[{len(rows)}]:", f"[{len(rows)}]:", 1) if rows else "[0]:"
        if name == "list-records-in-list":
            return text({"entries": [{"entry_id": e["entry_id"], "parent_record": {"object_slug": e["object"], "record_id": e["record_id"]},
                                      **self._view("", e["values"], LISTS[e["list"]]["attrs"])} for e in self.entries if e["list"] == a.get("list")], "has_more": False})
        if name == "search-notes-by-metadata":
            hits = [n for n in self.notes if not a.get("parent_record_id") or n["parent_record_id"] == a["parent_record_id"]]
            return text({"notes": [{"note_id": n["note_id"], "title": n["title"], "parent_object": n["parent_object"], "parent_record_id": n["parent_record_id"],
                                    "snippet": n["content"][:160]} for n in hits], "has_more": False})
        if name == "get-note-body":
            n = next((n for n in self.notes if n["note_id"] == a.get("note_id")), None)
            if n is None:
                raise AppError("No such note.")
            return text({"note_id": n["note_id"], "title": n["title"], "content": n["content"]})
        # --- writes ---
        if name == "create-record":
            obj = a.get("object")
            if obj not in ATTRS:
                raise AppError("`object` is required: companies, people or deals.")
            values = self._clean(obj, a.get("values") or {})
            missing = [d["api_slug"] for d in ATTRS[obj] if d["is_required"] and d["api_slug"] not in values]
            if missing:
                raise AppError(f"Missing required attribute(s): {', '.join(missing)}.")
            rid = self._id()
            self.records[rid] = {"object": obj, "values": values}
            self.writes.append((name, a))
            return text({"record_id": rid, "object": obj, "attributes": self._view(obj, values)})
        if name == "update-record":
            r = self.records.get(a.get("record_id"))
            if r is None:
                raise AppError("No such record.")
            new = self._clean(r["object"], a.get("values") or {})
            for k, v in new.items():
                old = r["values"].get(k)
                r["values"][k] = (v + [x for x in old if x not in v]) if isinstance(v, list) and isinstance(old, list) else v
            self.writes.append((name, a))
            return text(self._row(a["record_id"]))
        if name == "add-record-to-list":
            if a.get("list") not in LISTS or a.get("parent_record_id") not in self.records:
                raise AppError("Unknown list or record.")
            values = self._clean("", a.get("entry_values") or {}, LISTS[a["list"]]["attrs"])
            self.entries.append({"entry_id": self._id(), "list": a["list"], "object": self.records[a["parent_record_id"]]["object"],
                                 "record_id": a["parent_record_id"], "values": values})
            self.writes.append((name, a))
            return text({"entry_id": self.entries[-1]["entry_id"]})
        if name == "create-note":
            if a.get("parent_record_id") not in self.records or not a.get("title") or not a.get("content"):
                raise AppError("A note needs parent_record_id, title and content.")
            self.notes.append({"note_id": self._id(), "parent_object": self.records[a["parent_record_id"]]["object"], "parent_record_id": a["parent_record_id"],
                               "title": str(a["title"]), "content": str(a["content"])})
            self.writes.append((name, a))
            return text({"note_id": self.notes[-1]["note_id"]})
        raise AppError(f"No tool called {name}.")


def _refs(v: Any) -> list[dict[str, Any]]:
    return [x for x in (v if isinstance(v, list) else [v]) if isinstance(x, dict) and x.get("record_id")]


def _flat(v: Any) -> list[str]:
    if isinstance(v, dict):
        return [t for x in v.values() for t in _flat(x)]
    if isinstance(v, list):
        return [t for x in v for t in _flat(x)]
    return [] if v is None or isinstance(v, bool) else [str(v)]


class FakeGranola:
    """A few meetings: title, date, who attended, the notes, and a transcript."""

    def __init__(self):
        self.meetings: list[dict[str, Any]] = []
        self.calls: list[str] = []
        self.writes: list[tuple[str, dict[str, Any]]] = []

    def add(self, mid: str, title: str, when: str, attendees: list[str], notes: str, transcript: str = "") -> None:
        self.meetings.append({"id": mid, "title": title, "meeting_at": when, "attendees": attendees, "notes": notes, "transcript": transcript})

    async def list_tools(self) -> list[dict[str, Any]]:
        read = {"read_only_hint": True, "destructive_hint": False}
        ids = {"meeting_ids": {"type": "array", "items": {"type": "string"}}}
        rows = [("list_meetings", "List the owner's meeting notes, newest first: title, date, attendees and id.", {"time_range": {"type": "string"}}, read),
                ("search_meetings", "Find meetings by a word in the title or notes, or by a participant's name or email.", {"query": {"type": "string"}}, read),
                ("get_meetings", "Read meetings by id: the notes, the summary and who attended.", ids, read),
                ("get_meeting_transcript", "The word-for-word transcript of a meeting by id.", ids, read),
                ("update_summary", "Edit a meeting's summary.", {"meeting_id": {"type": "string"}, "text": {"type": "string"}},
                 {"read_only_hint": False, "destructive_hint": True})]
        return [{"name": n, "title": n.replace("_", " ").capitalize(), "labels": labels, "description": d, "inputs": {"type": "object", "properties": p}}
                for n, d, p, labels in rows]

    async def call(self, name: str, a: dict[str, Any]) -> str:
        self.calls.append(name)
        brief = lambda m: f"- id: {m['id']}\n  title: {m['title']}\n  meeting_at: {m['meeting_at']}\n  attendees: {', '.join(m['attendees'])}"
        if name == "list_meetings":
            return "Meetings, newest first:\n" + "\n".join(brief(m) for m in sorted(self.meetings, key=lambda m: m["meeting_at"], reverse=True))
        if name == "search_meetings":
            q = _norm(a.get("query"))
            hits = [m for m in self.meetings if any(w in _norm(m["title"] + " " + m["notes"] + " " + " ".join(m["attendees"])) for w in q.split())] if q else self.meetings
            return ("Matching meetings:\n" + "\n".join(brief(m) for m in hits)) if hits else "No meetings match."
        if name in ("get_meetings", "get_meeting_transcript"):
            out = []
            for mid in a.get("meeting_ids") or []:
                m = next((m for m in self.meetings if m["id"] == mid), None)
                if m is None:
                    out.append(f"{mid}: no such meeting")
                elif name == "get_meetings":
                    out.append(f"{brief(m)}\n  notes: {m['notes']}")
                else:
                    out.append(f"Transcript of {m['title']} ({m['meeting_at']}):\n{m['transcript'] or '(no transcript was kept)'}")
            return "\n\n".join(out) or "Give meeting_ids."
        if name == "update_summary":
            self.writes.append((name, a))
            return "updated"
        raise AppError(f"No tool called {name}.")
