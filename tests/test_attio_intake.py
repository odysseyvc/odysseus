"""Filing a whole source into Attio with one approval. No network: a stand-in for Attio's server."""
from __future__ import annotations

from odysseus.attio import Attio
from odysseus.attio_intake import ATTIO_FILE, ATTIO_PLAN
from odysseus.journal import Journal
from odysseus.tools import make_gate


def attr(slug, title, type_="text", writable=True, **more):
    return {"api_slug": slug, "title": title, "type": type_, "is_writable": writable, "is_archived": False, **more}


SHAPE = {
    "me": {"name": "Pat Owner", "workspace_member_id": "M1", "workspace_name": "Acme Capital"},
    "objects": {
        "companies": {"plural": "Companies", "attrs": [attr("name", "Name"), attr("domains", "Domains", "domain", is_multiselect=True),
                                                         attr("record_id", "Record ID", writable=False)]},
        "people": {"plural": "People", "attrs": [attr("name", "Name", "personal-name"), attr("email_addresses", "Email addresses", "email-address"),
                                                  attr("linkedin", "LinkedIn"), attr("company", "Company", "record-reference"),
                                                  attr("role", "Role", "select", is_multiselect=True, select_options=[{"title": "Founder"}, {"title": "Investor"}])]},
        "deals": {"plural": "Deals", "attrs": [attr("name", "Deal name"), attr("associated_company", "Associated company", "record-reference"),
                                                attr("associated_people", "Associated people", "record-reference", is_multiselect=True),
                                                attr("stage", "Deal stage", "status", statuses=[{"title": "Sourced"}, {"title": "Term sheet"}])]},
    },
    "lists": {"portfolio": {"name": "Portfolio", "parents": ["companies"], "attrs": [attr("ownership", "Ownership", "number")]}},
}

PLAN = {
    "source": "Forwarded email from Sam, 3 Nov",
    "records": [
        {"ref": "c1", "object": "companies", "values": {"name": "Orbit Labs", "domains": ["orbitlabs.example"]}},
        {"ref": "p1", "object": "people", "values": {"name": {"first_name": "Jo", "last_name": "Rao", "full_name": "Jo Rao"},
                                                     "email_addresses": ["jo@orbitlabs.example"], "company": "@c1", "role": ["founder"]}},
        {"ref": "d1", "object": "deals", "values": {"name": "Orbit Labs", "stage": "sourced", "associated_company": "@c1", "associated_people": ["@p1"]}},
    ],
    "list_entries": [{"list": "portfolio", "record": "@c1", "values": {"ownership": 5}}],
    "notes": [{"on": "@d1", "title": "From Sam", "content": "Sam says they are raising in March."}],
}


class FakeAttio:
    """Holds records as Attio would: search by text, create with an id, lists and notes."""

    def __init__(self, existing=(), fail_on=None):
        self.records = {f"r{i}": (obj, values) for i, (obj, values) in enumerate(existing)}
        self.calls, self.fail_on, self.n = [], fail_on, 100

    async def list_tools(self):
        return []

    async def call(self, name, a):
        self.calls.append((name, a))
        if name == self.fail_on:
            from odysseus.apps import AppError
            raise AppError("Attio refused it.")
        if name == "search-records":
            hits = [(rid, v) for rid, (obj, v) in self.records.items() if obj == a["object"] and a["query"].lower() in str(v).lower()]
            if not hits:
                return "results[0]:\nhas_more_results: false"
            body = "".join(f"\n  - record_id: {rid}\n    attributes:\n      name: {v.get('name') if not isinstance(v.get('name'), dict) else v['name']['full_name']}"
                           + (f"\n      domains[1]: {v['domains'][0]}" if v.get("domains") else "")
                           + (f"\n      email_addresses[1]: {v['email_addresses'][0]}" if v.get("email_addresses") else "") for rid, v in hits)
            return f"results[{len(hits)}]:{body}\nhas_more_results: false"
        if name == "create-record":
            self.n += 1
            self.records[f"new{self.n}"] = (a["object"], a["values"])
            return f"record:\n  record_id: new{self.n}\n  object: {a['object']}"
        return "ok: true"


def rig(tmp_path, **kw):
    backend = FakeAttio(**kw)
    attio = Attio(backend, Journal(tmp_path / "j.jsonl"))
    attio.shape = SHAPE
    gate = make_gate(Journal(tmp_path / "g.jsonl"), attio=attio)
    return backend, attio, gate, attio.handlers()


def text(out):
    return out["content"][0]["text"]


async def plan_id(h, plan=PLAN):
    out = await h["attio_plan"](plan)
    assert "is_error" not in out, text(out)
    return text(out).split("Plan ")[1].split(".")[0], text(out)


async def test_a_plan_changes_nothing_and_says_in_plain_lines_what_is_new(tmp_path):
    backend, attio, gate, h = rig(tmp_path)
    pid, shown = await plan_id(h)
    assert gate.decide(ATTIO_PLAN, PLAN) == "allow" and {n for n, _ in backend.calls} == {"search-records"}, "only looked things up"
    assert "From: Forwarded email from Sam, 3 Nov" in shown
    assert "- Company Orbit Labs: NEW. Name: Orbit Labs; Domains: orbitlabs.example" in shown
    assert "Company: the company Orbit Labs" in shown and "Associated people: the person Jo Rao" in shown, "links are named, never shown as refs or ids"
    assert "- Person Jo Rao: NEW." in shown and "Role: Founder" in shown, "a choice is written as Attio spells it"
    assert "Deal stage: Sourced" in shown and '- Add the company Orbit Labs to the list "Portfolio". Ownership: 5' in shown
    assert '- A note on the deal Orbit Labs, titled "From Sam":\n    Sam says they are raising in March.' in shown
    question = attio.intake.describe({"plan": pid})
    assert question.startswith("File this in Attio?") and shown.split("word for word:\n\n")[1].split("\n\nIf this is")[0] in question


async def test_filing_always_asks_and_then_creates_links_and_notes_in_order(tmp_path):
    backend, attio, gate, h = rig(tmp_path)
    pid, _ = await plan_id(h)
    assert gate.decide(ATTIO_FILE, {"plan": pid}) == "ask"
    out = text(await h["attio_file"]({"plan": pid}))
    made = [(n, a) for n, a in backend.calls if n != "search-records"]
    assert [n for n, _ in made] == ["create-record", "create-record", "create-record", "add-record-to-list", "create-note"]
    company, person, deal = (a["values"] for _, a in made[:3])
    cid = {"target_object": "companies", "target_record_id": "new101"}
    assert person["company"] == cid and person["role"] == ["Founder"]
    assert deal["associated_company"] == cid and deal["associated_people"] == [{"target_object": "people", "target_record_id": "new102"}]
    assert deal["stage"] == "Sourced"
    assert made[3][1] == {"list": "portfolio", "parent_object": "companies", "parent_record_id": "new101", "entry_values": {"ownership": 5}}
    assert made[4][1]["parent_record_id"] == "new103" and made[4][1]["parent_object"] == "deals"
    assert "Company Orbit Labs: created" in out and attio.pop_changes() == ["filed 5 things in Attio from one approved plan"]
    again = await h["attio_file"]({"plan": pid})
    assert again.get("is_error") and "no such plan" in text(again), "an approved plan is carried out once"


async def test_a_record_already_in_attio_is_linked_to_and_never_changed(tmp_path):
    backend, _, _, h = rig(tmp_path, existing=[("companies", {"name": "Orbit Labs Inc", "domains": ["orbitlabs.example"]})])
    pid, shown = await plan_id(h)
    assert "- Company Orbit Labs: ALREADY IN ATTIO (found by domains orbitlabs.example). It will be linked to and left exactly as it is." in shown
    await h["attio_file"]({"plan": pid})
    made = [(n, a) for n, a in backend.calls if n != "search-records"]
    assert [a["object"] for n, a in made if n == "create-record"] == ["people", "deals"], "no second company"
    assert made[0][1]["values"]["company"] == {"target_object": "companies", "target_record_id": "r0"}
    assert not any(n == "update-record" for n, _ in made)


async def test_a_record_that_appeared_between_plan_and_approval_is_not_created_twice(tmp_path):
    backend, _, _, h = rig(tmp_path)
    pid, _ = await plan_id(h)
    backend.records["late"] = ("companies", {"name": "Orbit Labs", "domains": ["orbitlabs.example"]})      # a colleague adds it meanwhile
    out = text(await h["attio_file"]({"plan": pid}))
    assert "appeared in Attio since the plan was made; linked, not created" in out
    assert [a["object"] for n, a in backend.calls if n == "create-record"] == ["people", "deals"]


async def test_a_plan_that_does_not_fit_the_workspace_is_refused_with_the_reason(tmp_path):
    _, _, _, h = rig(tmp_path)
    bad = [
        ({**PLAN, "source": ""}, "Say in `source`"),
        ({**PLAN, "records": [{"ref": "c1", "object": "investors", "values": {"name": "X"}}]}, "no object called 'investors'"),
        ({**PLAN, "records": [{"ref": "c1", "object": "companies", "values": {"name": "X", "sector": "AI"}}], "list_entries": [], "notes": []}, "no field 'sector'"),
        ({**PLAN, "records": [{"ref": "c1", "object": "companies", "values": {"name": "X", "record_id": "1"}}], "list_entries": [], "notes": []}, "no field 'record_id'"),
        ({**PLAN, "records": [{"ref": "d1", "object": "deals", "values": {"name": "X", "stage": "Won"}}], "list_entries": [], "notes": []}, "'Won' is not a choice for stage"),
        ({**PLAN, "records": [{"ref": "d1", "object": "deals", "values": {"name": "X", "associated_company": "@zz"}}], "list_entries": [], "notes": []}, "points at @zz"),
        ({**PLAN, "records": [{"ref": "p1", "object": "people", "values": {"role": ["Founder"]}}], "list_entries": [], "notes": []}, "give at least one of"),
        ({**PLAN, "records": [{"ref": "d1", "object": "deals", "values": {"name": "X", "associated_company": "0b614df8-bfd7-44ff-84ce-686c99a45901"}}],
          "list_entries": [], "notes": []}, 'must point at a record in this plan, as "@ref"'),
        ({**PLAN, "list_entries": [{"list": "watchlist", "record": "@c1"}]}, "no list called 'watchlist'"),
        ({**PLAN, "notes": [{"on": "@c1", "title": "", "content": "x"}]}, "note: give"),
        ({**PLAN, "records": PLAN["records"] * 9}, "more than 24 steps"),
    ]
    for plan, why in bad:
        out = await h["attio_plan"](plan)
        assert out.get("is_error") and why in text(out), (why, text(out))


async def test_when_a_step_fails_filing_stops_and_says_exactly_how_far_it_got(tmp_path):
    backend, attio, _, h = rig(tmp_path, fail_on="add-record-to-list")
    pid, _ = await plan_id(h)
    out = await h["attio_file"]({"plan": pid})
    said = text(out)
    assert out.get("is_error") and "Filing stopped at a step that failed" in said
    assert "Company Orbit Labs: created; Person Jo Rao: created; Deal Orbit Labs: created" in said and "Not done: that step and the 1 after it" in said
    assert not any(n == "create-note" for n, _ in backend.calls), "nothing after the failure was attempted"
    assert attio.pop_changes() == ["filed part of a plan in Attio (3 of 5 steps) before it failed"]


async def test_an_old_or_unknown_plan_cannot_be_filed(tmp_path):
    _, attio, gate, h = rig(tmp_path)
    now = [0.0]
    attio.intake.clock = lambda: now[0]
    pid, _ = await plan_id(h)
    now[0] = 19 * 60
    assert attio.intake.describe({"plan": pid}).startswith("File this in Attio?"), "still good at nineteen minutes"
    now[0] = 21 * 60
    assert (await h["attio_file"]({"plan": pid})).get("is_error") and (await h["attio_file"]({"plan": "nope"})).get("is_error")


async def test_the_owner_and_money_are_written_in_words(tmp_path):
    backend, attio, _, h = rig(tmp_path)
    deals = attio.shape["objects"]["deals"]["attrs"]
    if not any(a["api_slug"] == "owner" for a in deals):
        deals += [attr("owner", "Deal owner", "actor-reference"), attr("value", "Deal value", "currency", currency_code="USD")]
    plan = {"source": "A call with Jo", "records": [{"ref": "d1", "object": "deals", "values": {"name": "Orbit Labs", "owner": "me", "value": 2000000}}]}
    pid, shown = await plan_id(h, plan)
    assert "Deal owner: you (Pat Owner)" in shown and "Deal value: USD 2,000,000" in shown and "M1" not in shown
    await h["attio_file"]({"plan": pid})
    assert [a["values"]["owner"] for n, a in backend.calls if n == "create-record"] == ["M1"], "Attio is given the member id"
