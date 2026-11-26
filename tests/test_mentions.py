"""A company named in a source is not thereby a company the firm tracks (attio_intake.py).

A review meeting names the companies it is about and a good many more: comparisons, competitors, customers, investors.
Each of the second kind once became a record in the firm's database, filed without a question because a plan that only adds
runs unasked. These tests hold the two things by code that now stand in the way.
"""
from __future__ import annotations

from odysseus.attio_intake import ATTIO_FILE, SAID_MOST
from odysseus.taint import Taint

import test_attio_intake as I


def company(name, said=None, **more):
    return {"ref": "c1", "object": "companies", **({"said": said} if said is not None else {}), "values": {"name": name, **more}}


def plan(*records, notes=()):
    return {"source": "Notes of the Friday review", "records": list(records), "notes": list(notes)}


async def test_a_new_company_needs_the_words_that_give_it_a_part_and_they_must_name_it(tmp_path):
    backend, attio, gate, h = I.auto_rig(tmp_path)
    out = await h["attio_plan"](plan(company("Kestrel Freight")))
    assert out.get("is_error") and "is not a record" in I.text(out) and "Tell the owner which names you left out" in I.text(out)
    out = await h["attio_plan"](plan(company("Kestrel Freight", "A competitor in the same lanes raised last month.")))
    assert out.get("is_error") and "do not name Kestrel Freight" in I.text(out)
    out = await h["attio_plan"](plan(company("Kestrel Freight", "x " * SAID_MOST)))
    assert out.get("is_error") and "one or two sentences" in I.text(out)
    assert not any(n.startswith("create") for n, _ in backend.calls), "nothing was made while being refused"


async def test_several_companies_the_owner_did_not_name_are_never_filed_unasked_and_the_page_shows_why_each_is_there(tmp_path):
    backend, attio, gate, h = I.auto_rig(tmp_path)               # the owner typed "Orbit Labs", "Northwind", "Lumen Grid" and nothing else
    said = "We agreed to take a first call with Kestrel Freight next week."
    pid, shown = await I.plan_id(h, plan(company("Kestrel Freight", said)))
    assert f'Why it is a record: "{said}"' in shown and "You did not name this company yourself; it comes from the source." in shown
    assert gate.decide(ATTIO_FILE, {"plan": pid}) == "allow", "one company from a source is the ordinary deck or forwarded message"
    two = plan(company("Kestrel Freight", said), {**company("Tern Systems", "Sam will look at the Tern Systems deck."), "ref": "c2"})
    pid, shown = await I.plan_id(h, two)
    assert gate.decide(ATTIO_FILE, {"plan": pid}) == "ask", "a source that yields several is a review or a thread: the owner decides"
    assert said in attio.intake.describe({"plan": pid}), "the question carries the sentences the owner is to judge"
    pid, shown = await I.plan_id(h, plan(company("Orbit Labs", "Orbit Labs is the one I want filed.")))
    assert "You did not name" not in shown and gate.decide(ATTIO_FILE, {"plan": pid}) == "allow", "the owner named it: filed and reported, as before"


async def test_a_company_already_in_attio_needs_no_reason_and_a_note_on_it_is_still_filed_unasked(tmp_path):
    backend, attio, gate, h = I.auto_rig(tmp_path)
    await backend.call("create-record", {"object": "companies", "values": {"name": "Halyard Bio", "domains": ["halyardbio.example"]}})
    note = {"on": "@c1", "title": "Friday review", "content": "Compared with Kestrel Freight and Tern Systems; neither is one we are looking at."}
    pid, shown = await I.plan_id(h, plan(company("Halyard Bio", domains=["halyardbio.example"]), notes=[note]))
    assert "ALREADY IN ATTIO" in shown and "Kestrel Freight" in shown, "the mentions are kept, as words in a note"
    assert gate.decide(ATTIO_FILE, {"plan": pid}) == "allow"
    await gate.guarded(ATTIO_FILE, h["attio_file"])({"plan": pid})
    made = [a for n, a in backend.calls if n == "create-record"]
    assert len(made) == 1, "only the record this test itself put there; no company was made of a mention"


def test_whether_the_owner_named_something_is_read_from_their_own_words():
    t = Taint()
    assert t.owner_typed("Kestrel Freight") is None, "nothing typed yet: there is no telling, and the caller asks"
    t.owner_said("please file kestrel freight from the deck")
    assert t.owner_typed("Kestrel Freight") is True and t.owner_typed("Tern Systems") is False
    assert t.owner_typed("the and of") is False, "ordinary words alone name nothing"


def test_the_instructions_put_precision_first():
    from odysseus.attio_intake import PROMPT
    assert "everything the source supports" not in PROMPT
    assert "Those are mentions, not records." in PROMPT and "When in doubt leave a company out and say so" in PROMPT
