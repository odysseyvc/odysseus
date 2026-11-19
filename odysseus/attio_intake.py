"""Filing what a document names into Attio: one plan, one approval.

A forwarded email or a note of a meeting usually names several things at once: a company, its founder, a deal,
something worth writing down. Entered one at a time, that is four or five approval questions in a row, and the book's chapter on the gate
warned what a run of questions does: the owner stops reading them.

So filing is done in two steps.

  attio_plan   The model says everything it would file, as one plan. The program checks the plan against the
               workspace's layout (do these fields exist, is this a real choice), looks each record up in Attio,
               and writes out in plain lines what would be created and what is already there. Nothing is changed.
  attio_file   The owner is shown exactly that page and approves it once. The program then carries the plan out
               step by step, looking each record up again at the moment of creating it.

The plan is kept by the program between the two steps and cannot be altered: the approval is for the plan the
owner saw. A record that already exists is linked to and left as it is, unless the plan names a change to it, and then the owner
is shown that change as before and after, and it is made only if the record still says what the owner was shown.
Nothing can be undone through Attio's server, so if a step fails the program stops and says exactly how far it got.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from typing import Any

from .apps import AppError
from .attio_layout import parse
from .gate import ALLOW, ASK, Gate

ATTIO_PLAN, ATTIO_FILE = "mcp__odysseus__attio_plan", "mcp__odysseus__attio_file"
PLAN_KEEPS_S = 20 * 60
MATCH_BY = {"companies": ("domains", "name"), "people": ("email_addresses", "linkedin", "name"), "deals": ("name",)}
MAX_STEPS = 24


def _norm(v: Any) -> str:
    s = str(v or "").strip().lower()
    s = re.sub(r"^https?://(www\.)?", "", s).rstrip("/")
    return re.sub(r"\s+", " ", s)


def _texts(v: Any) -> list[str]:
    """Every piece of text inside a value Attio returned, whatever its nesting."""
    if isinstance(v, dict):
        return [t for x in v.values() for t in _texts(x)]
    if isinstance(v, list):
        return [t for x in v for t in _texts(x)]
    return [] if v is None or isinstance(v, bool) else [str(v)]


def _find_id(v: Any, key: str = "record_id") -> str | None:
    if isinstance(v, dict):
        if isinstance(v.get(key), str):
            return v[key]
        for x in v.values():
            got = _find_id(x, key)
            if got:
                return got
    if isinstance(v, list):
        for x in v:
            got = _find_id(x, key)
            if got:
                return got
    return None


def _tally(done: list[str]) -> str:
    """What an approved plan actually did, counted from what was done and not from what was planned."""
    n = lambda *marks: sum(1 for d in done if any(m in d for m in marks))
    parts = [f"{k} {w}" for k, w in ((n(": created"), "created"), (n(": changed ("), "changed"), (n("added to the list"), "added to a list"),
                                     (n("note \""), "noted")) if k]
    left = n("already there; linked", ": not changed", "linked, not created")
    return ("in Attio, from one approved plan: " + (", ".join(parts) or "nothing changed")
            + (f"; {left} already there and left as {'it was' if left == 1 else 'they were'}" if left else ""))


_LINK = re.compile(r"@[\w-]+")


def _links(v: Any) -> list[str]:
    """The "@ref" links inside a value: strings that are a link and nothing else."""
    if isinstance(v, str):
        return [v] if _LINK.fullmatch(v) else []
    if isinstance(v, list):
        return [r for x in v for r in _links(x)]
    if isinstance(v, dict):
        return [r for x in v.values() for r in _links(x)]
    return []


def _show(v: Any) -> str:
    if isinstance(v, dict) and "full_name" in v:
        return str(v["full_name"])
    if isinstance(v, list):
        return ", ".join(_show(x) for x in v)
    return str(v) if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False)


class Intake:
    def __init__(self, attio: Any, journal: Any):
        self._attio, self._journal = attio, journal
        self._plans: dict[str, dict[str, Any]] = {}
        self.last_filed: dict[str, Any] | None = None       # the company in the plan most recently filed; taken by the conversation
        self.clock = time.monotonic

    # --- checking a plan against the layout ------------------------------------------------

    def _attrs(self, kind: str, slug: str) -> dict[str, dict[str, Any]]:
        group = (self._attio.shape.get(kind) or {}).get(slug)
        if group is None:
            known = ", ".join((self._attio.shape.get(kind) or {}).keys())
            raise AppError(f"Attio has no {'object' if kind == 'objects' else 'list'} called '{slug}'. It has: {known}.")
        return {str(a.get("api_slug")): a for a in group.get("attrs") or [] if a.get("is_archived") is not True}

    def _check_values(self, where: str, attrs: dict[str, dict[str, Any]], values: Any, refs: set[str]) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise AppError(f"{where}: `values` must be an object of field slug to value.")
        out: dict[str, Any] = {}
        for slug, value in values.items():
            a = attrs.get(slug)
            if a is None or a.get("is_writable") is not True:
                raise AppError(f"{where}: there is no field '{slug}' you can set. Settable fields: "
                               f"{', '.join(s for s, x in attrs.items() if x.get('is_writable') is True)}.")
            choices = [str(o.get("title")) for o in (a.get("statuses") or a.get("select_options") or []) if isinstance(o, dict)]
            if choices:
                wanted = value if isinstance(value, list) else [value]
                canon = []
                for w in wanted:
                    hit = next((c for c in choices if _norm(c) == _norm(w)), None)
                    if hit is None:
                        raise AppError(f"{where}: '{w}' is not a choice for {slug}. Choices: {' | '.join(choices)}.")
                    canon.append(hit)
                value = canon if (isinstance(value, list) or a.get("is_multiselect") is True) else canon[0]
            for r in _links(value):           # only a whole value "@ref" is a link; an email address is not
                if r[1:] not in refs:
                    raise AppError(f"{where}: {slug} points at {r}, and no record in this plan has that ref.")
            kind = str(a.get("type"))
            if kind == "record-reference" and len(_links(value)) != len(value if isinstance(value, list) else [value]):
                # An id cannot be shown to the owner as anything they could check. Every link is to a record named in the plan.
                raise AppError(f"{where}: {slug} must point at a record in this plan, as \"@ref\". If the record is already in Attio, "
                               "add it to `records` with its name (and website or email address): it will be found, linked to and left unchanged.")
            if kind == "actor-reference":
                mine = str((self._attio.shape.get("me") or {}).get("workspace_member_id") or "")
                if _norm(value) in ("me", "@me", "owner", _norm(mine)) and mine:
                    value = mine
            out[slug] = value
        return out

    def _said(self, attrs: dict[str, dict[str, Any]], values: dict[str, Any], labels: dict[str, str]) -> str:
        """A record's values as the owner reads them: names for links, "you" for the owner, money with its currency."""
        me = self._attio.shape.get("me") or {}
        parts = []
        for slug, value in values.items():
            a = attrs[slug]
            if _links(value):
                shown = ", ".join(labels.get(r[1:], r) for r in _links(value))
            elif str(a.get("type")) == "actor-reference" and me.get("workspace_member_id") and value == me["workspace_member_id"]:
                shown = f"you ({me.get('name')})"
            elif str(a.get("type")) == "currency" and isinstance(value, (int, float)) and not isinstance(value, bool):
                shown = f"{a.get('currency_code') or ''} {value:,.2f}".strip().removesuffix(".00")
            else:
                shown = _show(value)
            parts.append(f"{a.get('title')}: {shown}")
        return "; ".join(parts)

    async def _existing(self, obj: str, match: dict[str, Any]) -> tuple[str | None, str]:
        """Is there already a record in this object whose identifying detail equals the one given? (id, what matched)"""
        for field in MATCH_BY.get(obj, ("name",)):
            wanted = _norm(_show(match.get(field))) if match.get(field) else ""
            if not wanted:
                continue
            found = parse(await self._attio._call(self._attio._b.call("search-records", {"object": obj, "query": _show(match[field]), "limit": 5})))
            for hit in found.get("results") or found.get("records") or []:
                if isinstance(hit, dict) and wanted in {_norm(t) for t in _texts(hit.get("attributes") or hit)} and hit.get("record_id"):
                    return str(hit["record_id"]), f"{field} {_show(match[field])}"
        return None, ""

    async def _similar(self, obj: str, name: str) -> list[str]:
        """Names already in Attio that are close to this one without being it: "Northwind Robotics" for "Northwind".

        An exact lookup misses these, and they are how a database fills with doubles: an email says "Asha at Northwind"
        and a second company called Northwind appears beside the first. Only the program can stop that reliably.
        """
        want = _norm(name)
        words = [w for w in re.split(r"[^a-z0-9]+", want) if len(w) >= 4]
        if not want or not words:
            return []
        found = parse(await self._attio._call(self._attio._b.call("search-records", {"object": obj, "query": words[0], "limit": 8})))
        near = []
        for hit in found.get("results") or found.get("records") or []:
            got = _show((hit.get("attributes") or {}).get("name")) if isinstance(hit, dict) else ""
            have = _norm(got)
            if have and have != want and (want in have or have in want or have.split(" ")[0] == want.split(" ")[0]):
                near.append(got)
        return near

    async def _now(self, obj: str, rid: str) -> dict[str, Any]:
        """A record's values as they are in Attio at this moment."""
        got = parse(await self._attio._call(self._attio._b.call("get-records-by-ids", {"object": obj, "record_ids": [rid]})))
        rows = got.get("") or got.get("records") or []
        row = rows[0] if rows and isinstance(rows[0], dict) else {}
        return row.get("attributes") if isinstance(row.get("attributes"), dict) else row

    @staticmethod
    def _was(current: Any) -> str:
        """A present value as the owner reads it."""
        if current in (None, "", [], {}):
            return "(empty)"
        if isinstance(current, dict):
            return "(set)" if not current.get("full_name") else str(current["full_name"])
        if isinstance(current, list):
            return ", ".join(Intake._was(x) for x in current)
        return str(current)

    def _differs(self, a: dict[str, Any], current: Any, wanted: Any) -> str | None:
        """How setting `wanted` would change `current`, in words; None if it would change nothing."""
        was = self._was(current)
        if a.get("is_multiselect") is True and isinstance(wanted, list) and not _links(wanted):
            have = {_norm(x) for x in (current if isinstance(current, list) else [current] if current else [])}
            new = [w for w in wanted if _norm(w) not in have]
            return f"add {', '.join(map(_show, new))} (now: {was})" if new else None     # Attio adds to a list of choices; it does not replace it
        if str(a.get("type")) in ("currency", "number") and isinstance(wanted, (int, float)) and not isinstance(wanted, bool):
            digits = re.sub(r"\D", "", str(current or ""))
            if digits and digits == re.sub(r"\D", "", f"{wanted:.2f}" if "." in str(current) else str(int(wanted))):
                return None
        elif _norm(was) == _norm(_show(wanted)):
            return None
        return was

    async def plan(self, a: dict[str, Any]) -> str:
        if not self._attio.shape:
            await self._attio.layout()
        if not self._attio.shape:
            raise AppError("The workspace's layout could not be read from Attio, so a plan cannot be checked. Try again shortly.")
        source = " ".join(str(a.get("source", "")).split())
        if not source:
            raise AppError("Say in `source` what this is being filed from, in one line, as the owner would recognise it.")
        records = a.get("records") or []
        entries, notes = a.get("list_entries") or [], a.get("notes") or []
        if not isinstance(records, list) or not records:
            raise AppError("A plan needs at least one record.")
        if len(records) + len(entries) + len(notes) > MAX_STEPS:
            raise AppError(f"That is more than {MAX_STEPS} steps. File it as two or three plans.")
        refs = {str(r.get("ref")) for r in records if isinstance(r, dict) and r.get("ref")}
        if len(refs) != len(records):
            raise AppError("Give every record a different `ref`, like c1, p1, d1.")
        steps, lines, checked, labels = [], [f"From: {source}", ""], [], {}
        doubt = False       # a record added beside a similarly named one: the owner should see both names before it exists
        for r in records:
            obj, ref = str(r.get("object", "")), str(r["ref"])
            attrs = self._attrs("objects", obj)
            values = self._check_values(f"record {ref}", attrs, r.get("values") or {}, refs)
            changes = self._check_values(f"record {ref} changes", attrs, r.get("changes") or {}, refs)
            match = {k: values.get(k) for k in MATCH_BY.get(obj, ("name",)) if values.get(k)}
            if not match:
                raise AppError(f"record {ref}: give at least one of {', '.join(MATCH_BY.get(obj, ('name',)))}, so it can be looked up first.")
            noun = {"companies": "the company", "people": "the person", "deals": "the deal"}.get(obj, obj)
            labels[ref] = f"{noun} {_show(values.get('name') or next(iter(match.values())))}"
            checked.append((ref, obj, attrs, values, match, changes, r.get("new") is True))
        for ref, obj, attrs, values, match, changes, said_new in checked:
            found, by = await self._existing(obj, match)
            near = await self._similar(obj, _show(values.get("name"))) if not found and obj in ("companies", "people", "deals") and values.get("name") else []
            if near and not said_new:
                raise AppError(f"record {ref}: Attio has nothing called exactly '{_show(values.get('name'))}', but it has {', '.join(repr(n) for n in near)}. "
                               "If one of those is the same one, give its exact name (and its website or email address if you know it) in `values`, so "
                               "that it is linked to and not added twice. If it really is a different one, put \"new\": true on this record; the owner "
                               "will be shown both names.")
            doubt = doubt or bool(near)
            label = labels[ref][4].upper() + labels[ref][5:] if labels[ref].startswith("the ") else labels[ref]      # "Company Orbit Labs"
            updates, before = {}, {}
            if found and changes:
                now = await self._now(obj, found)
                said = []
                for slug, wanted in changes.items():
                    was = self._differs(attrs[slug], now.get(slug), wanted)
                    if was is None:
                        continue                       # it already says this: nothing to change, and nothing to show
                    updates[slug], before[slug] = wanted, self._was(now.get(slug))
                    after = self._said(attrs, {slug: wanted}, labels).split(": ", 1)[1]
                    said.append(f"{attrs[slug].get('title')}: {was}" if was.startswith("add ") else f"{attrs[slug].get('title')}: {was} → {after}")
                if said:
                    lines.append(f"- {label}: ALREADY IN ATTIO (found by {by}). CHANGES: {'; '.join(said)}. Everything else on it stays as it is.")
            if found and not updates:
                lines.append(f"- {label}: ALREADY IN ATTIO (found by {by}). It will be linked to and left exactly as it is.")
            if not found:
                values = {**values, **changes}         # a record that does not exist yet is simply created with everything said about it
                also = f" (Attio already has the similarly named {', '.join(near)}; this is a separate record.)" if near else ""
                lines.append(f"- {label}: NEW. {self._said(attrs, values, labels)}{also}")
            steps.append({"do": "record", "ref": ref, "object": obj, "values": values, "match": match, "found": found, "label": label,
                          "updates": updates, "before": before})
        for e in entries:
            lst, ref = str(e.get("list", "")), str(e.get("record", "")).lstrip("@")
            attrs = self._attrs("lists", lst)
            if ref not in refs:
                raise AppError(f"list entry: `record` must be the ref of a record in this plan, like @c1.")
            values = self._check_values(f"entry in {lst}", attrs, e.get("values") or {}, refs)
            shown = self._said(attrs, values, labels) or "no fields"
            lines.append(f"- Add {labels[ref]} to the list \"{self._attio.shape['lists'][lst].get('name')}\". {shown}")
            steps.append({"do": "entry", "list": lst, "ref": ref, "values": values})
        for n in notes:
            ref, title, content = str(n.get("on", "")).lstrip("@"), " ".join(str(n.get("title", "")).split()), str(n.get("content", "")).strip()
            if ref not in refs or not title or not content:
                raise AppError("note: give `on` (the ref of a record in this plan), a `title` and the `content`.")
            lines.append(f"- A note on {labels[ref]}, titled \"{title}\":\n    {content[:1200]}")
            steps.append({"do": "note", "ref": ref, "title": title, "content": content[:8000]})
        plan_id = secrets.token_hex(3)
        summary = "\n".join(lines)
        self._plans = {k: v for k, v in self._plans.items() if self.clock() - v["at"] < PLAN_KEEPS_S}
        self._plans[plan_id] = {"at": self.clock(), "steps": steps, "summary": summary, "doubt": doubt, "source": source}
        self._journal.write("tool", name="attio_plan", ok=True, plan=plan_id, steps=len(steps), new=sum(1 for s in steps if s["do"] == "record" and not s["found"]))
        return (f"Plan {plan_id}. Nothing has been changed. This is the plan as the owner would be shown it, word for word:\n\n{summary}\n\n"
                f"If this is what the owner wants filed, call attio_file with plan \"{plan_id}\". To change anything, make a new plan.")

    # --- the gate and carrying it out --------------------------------------------------------

    def _get(self, a: dict[str, Any]) -> dict[str, Any]:
        plan = self._plans.get(str(a.get("plan", "")).strip())
        if plan is None or self.clock() - plan["at"] >= PLAN_KEEPS_S:
            raise AppError("There is no such plan, or it is more than twenty minutes old. Make it again with attio_plan.")
        return plan

    def describe(self, a: dict[str, Any]) -> str:
        return ("File this in Attio?\n\n" + self._get(a)["summary"] + "\n\nA record already in Attio is changed only where a line above says "
                "CHANGES. This cannot be undone from here.")

    def decide(self, a: dict[str, Any]) -> str:
        """Whether the owner is asked before a plan is filed.

        A plan that only adds (new records, notes, list entries, links to what is there) runs unasked when the owner
        has switched that on, and is reported line by line afterwards. A plan that would overwrite a value already in
        Attio is always the owner's decision, shown before and after: what was there is lost from view once changed.
        So is a plan that adds a record beside a similarly named one, and a plan the program does not have.
        """
        plan = self._plans.get(str(a.get("plan", "")).strip())
        if not getattr(self._attio, "auto", False) or plan is None or plan.get("doubt"):
            return ASK
        return ASK if any(s.get("updates") for s in plan["steps"]) else ALLOW

    def add_rules(self, gate: Gate) -> None:
        gate.rule(ATTIO_PLAN, ALLOW)                                # reads and checks; changes nothing
        gate.rule(ATTIO_FILE, self.decide, self.describe)           # the owner's decision whenever it changes what is there

    def _fill(self, value: Any, ids: dict[str, tuple[str, str]]) -> Any:
        if isinstance(value, str) and _LINK.fullmatch(value) and value[1:] in ids:
            obj, rid = ids[value[1:]]
            return {"target_object": obj, "target_record_id": rid}
        if isinstance(value, list):
            return [self._fill(v, ids) for v in value]
        if isinstance(value, dict):
            return {k: self._fill(v, ids) for k, v in value.items()}
        return value

    async def file(self, a: dict[str, Any]) -> str:
        plan_id = str(a.get("plan", "")).strip()
        plan = self._get(a)
        unasked = self.decide(a) == ALLOW
        del self._plans[plan_id]                    # an approval is used once
        run, ids, done = self._attio._b.call, {}, []
        step = None
        try:
            for step in plan["steps"]:
                if step["do"] == "record":
                    rid = step["found"]
                    if not rid:                     # look again at the moment of acting: someone may have added it since the plan was made
                        rid, _ = await self._existing(step["object"], step["match"])
                        if rid:
                            done.append(f"{step['label']}: appeared in Attio since the plan was made; linked, not created")
                    elif step.get("updates"):
                        # Look again at the moment of acting. The owner approved "Met → Diligence"; if it no longer says Met,
                        # somebody has changed it since, and what was approved is no longer what would happen.
                        now, attrs, apply, moved = await self._now(step["object"], rid), self._attrs("objects", step["object"]), {}, []
                        for slug, wanted in step["updates"].items():
                            if _norm(self._was(now.get(slug))) == _norm(step["before"][slug]):
                                apply[slug] = wanted
                            else:
                                moved.append(f"{attrs[slug].get('title')} was {step['before'][slug]} when you approved and is now "
                                             f"{self._was(now.get(slug))}, so it was left alone")
                        if apply:
                            await self._attio._call(run("update-record", {"object": step["object"], "record_id": rid, "values": self._fill(apply, ids)}))
                        titles = ", ".join(str(attrs[s].get("title")) for s in apply)
                        done.append(f"{step['label']}: " + (f"changed ({titles})" if apply else "not changed") + ("; " + "; ".join(moved) if moved else ""))
                    elif step["found"]:
                        done.append(f"{step['label']}: already there; linked")
                    if not rid:
                        out = await self._attio._call(run("create-record", {"object": step["object"], "values": self._fill(step["values"], ids)}))
                        rid = _find_id(parse(out))
                        if not rid:
                            raise AppError(f"Attio created {step['label']} but did not say which record it is, so nothing can be linked to it.")
                        done.append(f"{step['label']}: created")
                    ids[step["ref"]] = (step["object"], rid)
                elif step["do"] == "entry":
                    obj, rid = ids[step["ref"]]
                    await self._attio._call(run("add-record-to-list", {"list": step["list"], "parent_object": obj, "parent_record_id": rid,
                                                                        "entry_values": self._fill(step["values"], ids)}))
                    done.append(f"added to the list {step['list']}")
                else:
                    obj, rid = ids[step["ref"]]
                    await self._attio._call(run("create-note", {"parent_object": obj, "parent_record_id": rid, "title": step["title"], "content": step["content"]}))
                    done.append(f"note \"{step['title']}\" added")
        except AppError as e:
            left = len(plan["steps"]) - plan["steps"].index(step) if step in plan["steps"] else 0
            self._journal.write("tool", name="attio_file", ok=False, plan=plan_id, done=len(done), left=left, error=str(e)[:200])
            if done:
                self._attio._changes.append(f"filed part of a plan in Attio ({len(done)} of {len(plan['steps'])} steps) before it failed")
            raise AppError(f"Filing stopped at a step that failed: {e}\nDone before that, and still in Attio: {'; '.join(done) or 'nothing'}.\n"
                           f"Not done: that step and the {max(left - 1, 0)} after it. Tell the owner exactly this. Do not file the plan again as it was; "
                           "make a new plan for what is missing.") from None
        self._journal.write("tool", name="attio_file", ok=True, plan=plan_id, steps=len(plan["steps"]))
        # What was just filed, for the question that follows a take on a deck (decisions.py): which company, and where its records are.
        company = next((s for s in plan["steps"] if s["do"] == "record" and s["object"] == "companies"), None)
        if company is not None:
            domains = company["values"].get("domains") or company["match"].get("domains") or []
            self.last_filed = {"company": company["label"].split(" ", 1)[-1], "domain": str((domains if isinstance(domains, list) else [domains])[0]) if domains else "",
                               "source": plan.get("source", ""), "ids": {obj: rid for obj, rid in ids.values()}}
        if unasked:
            # Nobody saw this plan before it ran, so the owner is told each thing that was done, not a count of them.
            self._attio._changes.append("filed in Attio without asking, because it only adds: " + "; ".join(done))
            return ("Filed. The owner was not asked first, because this plan only adds and overwrites nothing. The exact list below is shown "
                    "to them under your reply, so do not repeat it item by item: say in a sentence what was filed, then only what the list "
                    "cannot tell them (something they would expect that you left out, anything they should check or decide):\n- " + "\n- ".join(done))
        self._attio._changes.append(_tally(done))
        return "Filed, as the owner approved:\n- " + "\n- ".join(done)

    def handlers(self) -> dict[str, Any]:
        def wrap(fn, name):
            async def handler(args: dict[str, Any]) -> dict[str, Any]:
                try:
                    return {"content": [{"type": "text", "text": await fn(args)}]}
                except AppError as e:
                    return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
            return handler
        return {"attio_plan": wrap(self.plan, "attio_plan"), "attio_file": wrap(self.file, "attio_file")}


SCHEMAS = {
    "attio_plan": ("Draw up, without changing anything, everything you would file in Attio from one source (a forwarded email, a deck, a note of "
                   "a meeting): the records, their links to each other, list entries and notes. The program checks every field against the "
                   "workspace's layout, looks each record up, and returns the page the owner will be shown. Use the field slugs and choices "
                   "from attio_find. In `values`, point at another record of the same plan as \"@ref\".",
                   {"type": "object", "required": ["source", "records"], "properties": {
                       "source": {"type": "string", "description": "What this is filed from, in one line the owner would recognise."},
                       "records": {"type": "array", "items": {"type": "object", "required": ["ref", "object", "values"], "properties": {
                           "ref": {"type": "string", "description": "A short name for this record within the plan: c1, p1, d1."},
                           "object": {"type": "string", "description": "companies, people or deals."},
                           "new": {"type": "boolean", "description": "Only when attio_plan has told you a similarly named record exists and "
                                   "this one is nevertheless a different company or person."},
                           "changes": {"type": "object", "description": "Only for something the source says that should be set on this record "
                                       "even if it is already in Attio: a new stage, a next step, a date. Field slug to new value. The owner is "
                                       "shown each as before → after. A choice from several is added to those already there, never removed."},
                           "values": {"type": "object", "description": "What identifies the record, and what to create it with if it is new. An "
                                      "existing record is never changed by `values`, only by `changes`. Field slug to value. A person's name is {first_name, last_name, full_name}. "
                                      "A link to another record is always \"@ref\" (several: a list of them), never an id: a record already in "
                                      "Attio that you want to link to goes in `records` too, with its name and website or email address, and is "
                                      "found and left unchanged. For a field that takes a member of the firm, \"me\" is the owner."}}}},
                       "list_entries": {"type": "array", "items": {"type": "object", "required": ["list", "record"], "properties": {
                           "list": {"type": "string"}, "record": {"type": "string", "description": "\"@ref\""}, "values": {"type": "object"}}}},
                       "notes": {"type": "array", "items": {"type": "object", "required": ["on", "title", "content"], "properties": {
                           "on": {"type": "string", "description": "\"@ref\""}, "title": {"type": "string"}, "content": {"type": "string"}}}}}}),
    "attio_file": ("Carry out a plan made with attio_plan, by its id. The owner is shown the whole plan and approves or declines it once.",
                   {"type": "object", "required": ["plan"], "properties": {"plan": {"type": "string"}}}),
}

PROMPT = """

Filing from a source. When the owner sends or forwards something that names companies, founders or deals and wants it in Attio (an email, a deck, a note of a meeting, a voice note, "file this", "add these"), do not create the records one at a time. Call attio_find once, then attio_plan with everything the source supports, then attio_file with the plan's id. The program decides whether the owner is asked: a plan that only adds is usually filed at once and the owner is told each thing that was added; a plan that changes a value already in Attio, or adds a record beside a similarly named one, is put to the owner whole, for one tap. Put in a record only what the source says about that record, in the firm's own fields. Where the source gives a view or a fact worth keeping that has no field (why a colleague liked a founder, the terms as described that day), put it in a note in the source's own words, with who said it and when. A source is evidence of what someone wrote on a day, not of what is true now: if it is old, or says something is still to be decided, say so to the owner before filing and do not record the undecided thing as settled. Leave out anything you would have to guess. The plan tells you which records are already in Attio. To change one of those, put only what the source newly says in that record's `changes` (a stage the deal has moved to, the next step, a date); the owner is shown each change as before and after. Never use `changes` to tidy, to fill a gap from a guess, or to empty a field. Use a plan for any change to a record that is already in Attio, including a single correction the owner dictates ("our cheque was 750,000, not 500,000"): give the owner's own words as the source, name the record in `records`, and put the new value in `changes`. That way the owner approves a line that names the record and shows before and after; run through attio_run they would be shown an id and no before. After a meeting about a company already in Attio, the usual plan is: the company and the deal as they are, the deal's stage and next step in `changes` if the meeting moved them, and a note of what was said. After filing, tell the owner what was created, what was already there, and what in the source you left out and why."""
