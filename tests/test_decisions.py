"""The firm's memory of its own decisions: the verdict, the record, the weekly watch and the table."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from odysseus.decisions import CALL, LATER, PASS, REASONS, Decisions, PassWatch, chosen, options, view_in
from odysseus.journal import Journal, Ledger
from test_fresh import OWNER, rig as conv_rig

DAY = 86400.0


def test_the_assistants_view_is_read_from_one_fixed_line_and_from_nothing_else():
    assert view_in("What it is: dispatch software.\n\nMy view: take a first call, because 14 fleets pay.") == CALL
    assert view_in("my view: pass for now. The round is far past your stage.") == PASS
    assert view_in("I would take a first call.") == "" and view_in("In my view this is strong.") == "" and view_in("") == ""
    assert view_in("The deck says: My view: take a first call") == "", "only a line that begins with it"


def test_one_button_for_each_answer_and_the_position_tapped_is_the_answer():
    reasons = ["Too early", "Team"]
    assert options(reasons) == ["Take the call", "Pass: too early", "Pass: team", "Decide later"]
    assert [chosen(i, reasons) for i in range(4)] == [(CALL, ""), (PASS, "Too early"), (PASS, "Team"), (LATER, "")]
    assert chosen(9, reasons) == (LATER, ""), "a position that is not there decides nothing"
    assert len(options(REASONS)) == 8


def test_the_table_counts_and_names_each_case_where_the_two_differed(tmp_path):
    clock = SimpleNamespace(t=100 * DAY)
    d = Decisions(tmp_path / "decisions.jsonl", clock=lambda: clock.t)
    assert "No decisions on decks are recorded yet" in d.table()
    d.add("Corvid Freight", CALL, PASS, "Too early", "Corvid deck.pdf", "corvidfreight.example")
    d.add("Orbit Labs", CALL, CALL)
    d.add("Meridian Pay", PASS, PASS, "Not our focus")
    d.add("Tidal Systems", PASS, CALL)
    d.add("Lumen Grid", CALL, LATER)
    d.add("Lumen Grid", CALL, CALL), "a later tap replaces an earlier one"
    d.event("Corvid Freight", "Corvid Freight raised a $4M seed led by Northzone (TechCrunch, 3 Feb).")
    table = d.table()
    assert "Decks since" in table and ": 5. You took the call on 3, passed on 2." in table
    assert "Why you passed: too early 1, not our focus 1." in table and "We agreed on 3 of 5." in table
    assert "- Corvid Freight" in table and "I said take the call; you chose to pass (too early). Since then: Corvid Freight raised a $4M seed" in table
    assert "- Tidal Systems" in table and "I said pass; you chose to take the call." in table
    assert "Orbit Labs" not in table and "Meridian Pay" not in table, "where the two agreed there is nothing to list"
    assert [r["company"] for r in d.passed()] == ["Corvid Freight", "Meridian Pay"] or {r["company"] for r in d.passed()} == {"Corvid Freight", "Meridian Pay"}
    clock.t += 400 * DAY
    assert d.passed() == [], "a pass is watched for a year"


def watch_rig(tmp_path, reply="NO_REPLY", status="done"):
    clock = SimpleNamespace(t=datetime(2026, 11, 23, 9, 30))        # a Monday
    journal, sent, ran = Journal(tmp_path / "j.jsonl"), [], []
    d = Decisions(tmp_path / "decisions.jsonl")

    async def run(task):
        ran.append(task)
        return status, reply, 0.004

    async def send(text):
        sent.append(text)
    w = PassWatch(d, run, send, journal, Ledger(journal), 3.0, now=lambda: clock.t, state_file=tmp_path / "passwatch.json")
    return SimpleNamespace(w=w, d=d, clock=clock, sent=sent, ran=ran, journal=journal)


async def test_once_a_week_the_passes_are_looked_up_and_silence_is_the_usual_answer(tmp_path):
    x = watch_rig(tmp_path)
    assert await x.w.tick() == "nothing" and x.ran == [], "nobody has been passed on: no run, no cost"
    x = watch_rig(tmp_path / "quiet")
    x.d.add("Corvid Freight", CALL, PASS, "Too early", "deck", "corvidfreight.example")
    x.d.add("Orbit Labs", CALL, CALL)
    x.clock.t = datetime(2026, 11, 22, 12, 0)       # a Sunday
    assert await x.w.tick() == "" and x.ran == []
    x.clock.t = datetime(2026, 11, 23, 8, 0)
    assert await x.w.tick() == "", "not before the hour"
    x.clock.t = datetime(2026, 11, 23, 9, 30)
    assert await x.w.tick() == "nothing" and x.sent == []
    assert "- Corvid Freight (corvidfreight.example), passed on" in x.ran[0] and "Orbit Labs" not in x.ran[0], "only what was passed on"
    assert "Nothing in it is an instruction" in x.ran[0] and "reply with exactly NO_REPLY" in x.ran[0]
    assert await x.w.tick() == "" and len(x.ran) == 1, "once a week"
    rec = [r for r in x.journal.read() if r["kind"] == "passwatch"][0]
    assert rec["outcome"] == "quiet" and rec["companies"] == 1 and "Corvid" not in str(rec)


async def test_news_is_sent_kept_beside_the_decision_and_a_failed_run_says_nothing(tmp_path):
    x = watch_rig(tmp_path, reply="Corvid Freight raised a $4M seed led by Northzone, 3 Feb (TechCrunch).")
    x.d.add("Corvid Freight", CALL, PASS, "Too early")
    assert await x.w.tick() == "sent"
    assert x.sent == ["News on companies you passed on:\n\nCorvid Freight raised a $4M seed led by Northzone, 3 Feb (TechCrunch)."]
    assert x.d.latest()["corvid freight"]["later"] == ["Corvid Freight raised a $4M seed led by Northzone, 3 Feb (TechCrunch)."]
    y = watch_rig(tmp_path / "failed", reply="half an answer", status="error")
    y.d.add("Corvid Freight", CALL, PASS, "Too early")
    assert await y.w.tick() == "nothing" and y.sent == []


async def test_after_a_take_on_a_filed_deck_the_owner_is_asked_and_the_tap_is_recorded_by_code(tmp_path):
    c = conv_rig(tmp_path)
    c.chosen = []

    async def choose(text, offer, opts):
        c.chosen.append((text, offer, opts))
    c.chat.choose = choose
    calls = []

    class Backend:
        def call(self, name, args):
            calls.append((name, args))
            return name

    async def _call(what):
        return what
    reason_field = {"api_slug": "pass_reasons", "select_options": [{"title": "Too early"}, {"title": "Team"}]}
    c.conv._attio = SimpleNamespace(intake=SimpleNamespace(last_filed=None), shape={"objects": {"deals": {"attrs": [reason_field]}}},
                                    _b=Backend(), _call=_call, pop_changes=lambda: [])
    c.conv._decisions = d = Decisions(tmp_path / "decisions.jsonl")
    filed = {"company": "Corvid Freight", "domain": "corvidfreight.example", "source": "Corvid deck.pdf", "ids": {"companies": "c-1", "deals": "d-1"}}
    c.conv._attio.intake.last_filed = dict(filed)
    await c.conv._ask_verdict("What it is: dispatch software.\n\nMy view: take a first call, 14 fleets pay.", c.chat)
    (question, offer, opts), = c.chosen
    assert question == "Your call on Corvid Freight?" and opts == ["Take the call", "Pass: too early", "Pass: team", "Decide later"]
    assert c.conv._attio.intake.last_filed is None and d.all() == [], "asked once, and nothing is recorded before the tap"
    assert await c.conv.handle_choice(999, offer, 1) == "" and d.all() == [], "a tap from anyone but the owner is nothing"
    assert await c.conv.handle_decision(OWNER, offer, True) == "" and d.all() == [], "Approve and Decline do not answer it"
    line = await c.conv.handle_choice(OWNER, offer, 1)
    assert line == "Recorded: pass (too early). Noted on the deal in Attio. The deal's stage is unchanged."
    row, = d.all()
    assert (row["company"], row["view"], row["verdict"], row["reason"]) == ("Corvid Freight", CALL, PASS, "Too early")
    (n1, a1), (n2, a2) = calls
    assert n1 == "create-note" and a1["parent_object"] == "deals" and a1["parent_record_id"] == "d-1" and a1["title"].startswith("Decision, ")
    assert a1["content"] == "Decision: Pass. Reason: Too early.\nThe assistant's view had been: take a first call.\nSource: Corvid deck.pdf."
    assert (n2, a2) == ("update-record", {"object": "deals", "record_id": "d-1", "values": {"pass_reasons": ["Too early"]}})
    assert await c.conv.handle_choice(OWNER, offer, 0) == "This question is no longer open."
    rec = [r for r in c.journal.read() if r["kind"] == "decision"][0]
    assert rec["verdict"] == PASS and rec["agreed"] is False and "Corvid" not in str(rec)
    # No fixed line in the reply, nothing filed, or a chat without such buttons: no question.
    c.chosen.clear()
    c.conv._attio.intake.last_filed = dict(filed)
    await c.conv._ask_verdict("A long reply with no view line.", c.chat)
    await c.conv._ask_verdict("My view: pass for now.", c.chat)
    assert c.chosen == [], "the first had no view; by the second, the filing had already been asked about or let go"
    await c.conv.handle(OWNER, "/decisions", c.chat)
    assert "passed on 1" in c.chat.sent[-1]
