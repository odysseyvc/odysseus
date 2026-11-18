"""Changing and deleting events, drafts and archiving, and the four holes in the gate that were closed.

The connector is the seeded test world from the errand suite; no model and no network are involved.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from evals.world import NOW, World
from odysseus.anyapp import APP_RUN, AnyApp
from odysseus.apps import CALENDAR_CHANGE, CALENDAR_DELETE, HIDDEN, Apps
from odysseus.gate import MAX_QUESTION, Gate
from odysseus.journal import Journal
from odysseus.taint import Taint
from odysseus.tools import SEARCH_TOOL, make_gate


def rig(tmp_path, answer: bool | None = True):
    journal = Journal(tmp_path / "journal.jsonl")
    world = World()
    apps = Apps(world, journal, lambda: NOW, tz="Asia/Kolkata")
    gate = make_gate(journal, lambda: NOW, timeout_s=2, apps=apps)
    asked: list[str] = []

    async def asker(approval):
        asked.append(approval.text)
        if answer is not None:
            gate.resolve(approval.id, answer)
    gate.set_asker(asker)
    h = apps.handlers()
    for name in (CALENDAR_CHANGE, CALENDAR_DELETE):
        short = name.rsplit("__", 1)[1]
        h[short] = gate.guarded(name, h[short])
    return SimpleNamespace(world=world, apps=apps, gate=gate, asked=asked, h=h, journal=journal)


async def listed(x):
    await x.h["calendar_list"]({"start": "2026-11-18T00:00", "end": "2026-11-28T00:00"})


def event(x, event_id):
    return next(e for e in x.world.events if e["id"] == event_id)


# --- the calendar ------------------------------------------------------------------

async def test_an_event_nobody_else_is_on_is_moved_without_a_question_and_reported(tmp_path):
    x = rig(tmp_path)
    await listed(x)
    args = {"event_id": "e01", "start": "2026-11-18T13:00"}
    assert x.gate.decide(CALENDAR_CHANGE, args) == "allow"
    out = await x.h["calendar_change"](args)
    assert "is_error" not in out and event(x, "e01")["start"] == datetime(2026, 11, 18, 13) and event(x, "e01")["minutes"] == 60
    assert x.asked == [] and x.world.notified == []
    assert "changed" in x.apps.pop_changes()[0]


async def test_moving_an_event_with_a_guest_asks_first_and_shows_who_is_told(tmp_path):
    x = rig(tmp_path)
    await listed(x)
    args = {"event_id": "e02", "start": "2026-11-18T16:00"}
    assert (await x.h["calendar_change"](dict(args)))["is_error"] is True and event(x, "e02")["start"].hour == 15, "not approved, not moved"
    assert isinstance(await x.gate.permission(CALENDAR_CHANGE, dict(args)), PermissionResultAllow)
    assert "sam@example.org" in x.asked[0] and "15:00 to 16:00" in x.asked[0] and "16:00 to 17:00" in x.asked[0]
    await x.h["calendar_change"](dict(args))
    assert event(x, "e02")["start"].hour == 16 and x.world.notified == ["sam@example.org"]
    assert (await x.h["calendar_change"]({"event_id": "e02", "start": "2026-11-18T17:00"}))["is_error"] is True, "one approval, one change"


async def test_an_event_the_program_has_not_seen_cannot_be_changed_or_deleted(tmp_path):
    x = rig(tmp_path)        # the calendar was never listed, so no id is known
    for tool, args in ((CALENDAR_CHANGE, {"event_id": "e01", "start": "2026-11-18T13:00"}), (CALENDAR_DELETE, {"event_id": "e01"})):
        out = await x.gate.permission(tool, args)
        assert isinstance(out, PermissionResultDeny) and "List the calendar" in out.message
    assert x.asked == [] and x.world.changed == [] and x.world.deleted == []


async def test_deleting_always_asks_and_a_decline_leaves_the_event(tmp_path):
    x = rig(tmp_path, answer=False)
    await listed(x)
    assert x.gate.decide(CALENDAR_DELETE, {"event_id": "e01"}) == "ask", "even an event with no guests"
    assert isinstance(await x.gate.permission(CALENDAR_DELETE, {"event_id": "e05"}), PermissionResultDeny)
    assert "Dentist" in x.asked[0] and (await x.h["calendar_delete"]({"event_id": "e05"}))["is_error"] is True
    assert x.world.deleted == []


async def test_an_approved_delete_removes_the_event_and_tells_its_guests(tmp_path):
    x = rig(tmp_path)
    await listed(x)
    await x.gate.permission(CALENDAR_DELETE, {"event_id": "e02"})
    assert "sam@example.org" in x.asked[0]
    assert "is_error" not in await x.h["calendar_delete"]({"event_id": "e02"})
    assert [e["id"] for e in x.world.deleted] == ["e02"] and x.world.notified == ["sam@example.org"]


async def test_a_change_that_changes_nothing_or_starts_in_the_past_is_refused(tmp_path):
    x = rig(tmp_path)
    await listed(x)
    assert "Nothing would change" in (await x.h["calendar_change"]({"event_id": "e01"}))["content"][0]["text"]
    assert "past" in (await x.h["calendar_change"]({"event_id": "e01", "start": "2026-11-17T10:00"}))["content"][0]["text"]


# --- drafts and archiving ------------------------------------------------------------

async def test_a_draft_is_saved_and_nothing_is_sent(tmp_path):
    x = rig(tmp_path)
    out = await x.h["mail_draft"]({"to": ["sam@example.org"], "subject": "Comments", "body": "Will send by Thursday."})
    assert "NOT been sent" in out["content"][0]["text"]
    assert x.world.sent == [] and x.world.drafts[0]["to"] == ["sam@example.org"]
    assert "not sent" in x.apps.pop_changes()[0]
    assert (await x.h["mail_draft"]({"subject": "x", "body": "y"}))["is_error"] is True, "a draft to nobody, in no thread"


async def test_archiving_takes_a_message_out_of_the_inbox_and_keeps_it(tmp_path):
    x = rig(tmp_path)
    assert "is_error" not in await x.h["mail_archive"]({"message_id": "m06"})
    assert x.world.archived == ["m06"] and any(m["id"] == "m06" for m in x.world.mail)
    assert "m06" not in (await x.h["mail_search"]({"query": "in:inbox deals"}))["content"][0]["text"]


# --- hole 1: private details leaving through a search ----------------------------------

def test_fingerprints_catch_addresses_numbers_codes_and_runs_of_words_but_not_ordinary_words():
    t = Taint()
    t.add("Booking reference: QX7R2L. Account 5520-118, amount due Rs 3,180. Write to ravi.shah@example.org. "
          "The team offsite is at Lakeview Hall on Friday 4 December from ten.")
    assert t.leaked("skyjet manage booking QX7R2L") == ["qx7r2l"]
    assert t.leaked("who is ravi.shah@example.org") == ["ravi.shah@example.org"]
    assert t.leaked("city power account 5520 118") == ["5520118"]
    assert '"team offsite is at lakeview hall"' in t.leaked("the team offsite is at lakeview hall on friday"), "six words in a row"
    assert t.leaked("Lakeview Hall parking and directions") == [], "a place name alone is not a fingerprint"
    assert t.leaked("SkyJet domestic baggage allowance 2026") == []


def test_what_the_owner_typed_themselves_is_theirs_to_search_for():
    t = Taint()
    t.add("Booking reference: QX7R2L")
    t.owner_said("Look up check-in for booking QX7R2L")
    assert t.leaked("skyjet check-in QX7R2L") == []


async def test_a_search_carrying_details_from_mail_is_put_to_the_owner(tmp_path):
    x = rig(tmp_path, answer=False)
    plain = {"query": "SkyJet baggage allowance", "objective": "the airline's own page"}
    assert x.gate.decide(SEARCH_TOOL, plain) == "allow"
    await x.h["mail_read"]({"message_id": "m04"})
    assert x.gate.decide(SEARCH_TOOL, plain) == "allow", "reading mail does not make every search suspect"
    leaky = {"query": "SkyJet booking QX7R2L seat 14C", "objective": "find the booking"}
    out = await x.gate.permission(SEARCH_TOOL, leaky)
    assert isinstance(out, PermissionResultDeny) and "qx7r2l" in x.asked[0] and "sent to a search company" in x.asked[0]
    assert SEARCH_TOOL not in x.gate.always_allowed()


# --- hole 2: approving text that was not shown -------------------------------------------

async def test_a_question_too_long_to_show_in_full_is_refused_and_never_shown(tmp_path):
    gate = Gate(Journal(tmp_path / "j.jsonl"), timeout_s=1)
    shown = []

    async def asker(approval):
        shown.append(approval.text)
    gate.set_asker(asker)
    gate.rule("big", "ask", lambda a: "x" * (MAX_QUESTION + 1))
    ok, why, _ = await gate.ask("big", {})
    assert ok is False and "too long to show" in why and shown == []


def test_app_inputs_are_shown_whole():
    app = AnyApp(SimpleNamespace(), Journal.__new__(Journal))
    text = app.describe({"tool": "GOOGLESHEETS_VALUES_UPDATE", "arguments": {"values": [["row %d" % i] for i in range(400)]}})
    assert "row 399" in text and "cut" not in text


# --- hole 3: one approval covering destructive actions -------------------------------------

def test_only_an_action_recognised_as_reading_runs_in_an_app_without_asking():
    app = AnyApp(SimpleNamespace(), Journal.__new__(Journal))
    assert app.decide({"tool": "GOOGLESHEETS_BATCH_GET"}) == "allow"
    for slug in ("GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND", "GOOGLESHEETS_CLEAR_VALUES", "GOOGLESHEETS_SEARCH_AND_REPLACE",
                 "GOOGLESHEETS_DELETE_SHEET", "GOOGLESHEETS_EXECUTE_SQL", "GOOGLESHEETS_MOVE_SHEET", "GOOGLESHEETS_VOID_SHEET", "STRIPE_WIPE"):
        assert app.decide({"tool": slug}) == "ask", slug


# --- hole 4: one suspicious line hiding a whole result ----------------------------------------

async def test_a_line_that_looks_like_a_code_hides_that_field_and_not_the_whole_result(tmp_path):
    backend = SimpleNamespace(execute=lambda slug, args: {"rows": [["Rent", 42000], ["Power", 3180]], "note": "security code 4821 for the gate"})
    app = AnyApp(backend, Journal(tmp_path / "j.jsonl"))
    out = (await app.handlers()["app_run"]({"tool": "GOOGLESHEETS_BATCH_GET", "arguments": {}}))["content"][0]["text"]
    assert HIDDEN in out and "4821" not in out and "Rent" in out and "3180" in out


# --- failures: what the model is told, and what is retried --------------------------------------

def failing(tmp_path, *faults):
    from evals.sim import Sim
    journal = Journal(tmp_path / "journal.jsonl")
    sim = Sim(planned=list(faults))
    apps = Apps(sim, journal, lambda: NOW, tz="Asia/Kolkata")
    apps.retry_wait_s = 0
    return sim, apps.handlers(), journal


async def test_a_failed_read_is_tried_again_before_the_model_hears_of_it(tmp_path):
    from evals.sim import Fault
    sim, h, journal = failing(tmp_path, Fault("GMAIL_FETCH_EMAILS", 1, "rate"), Fault("GMAIL_FETCH_EMAILS", 2, "error"))
    out = await h["mail_search"]({"query": "from:priya"})
    assert "is_error" not in out and "Priya" in out["content"][0]["text"]
    assert [r["ok"] for r in journal.read() if r["kind"] == "tool"] == [False, False, True]


async def test_when_a_read_keeps_failing_the_model_is_given_the_services_own_words(tmp_path):
    from evals.sim import Fault
    sim, h, _ = failing(tmp_path, *(Fault("GOOGLECALENDAR_EVENTS_LIST", n, "rate") for n in (1, 2, 3)))
    out = await h["calendar_list"]({"start": "2026-11-19T00:00", "end": "2026-11-20T00:00"})
    assert out["is_error"] is True and "Rate limit exceeded" in out["content"][0]["text"], "not just the error's class name"


async def test_a_failed_write_is_never_repeated_by_code_and_warns_that_it_may_have_happened(tmp_path):
    from evals.sim import Fault
    sim, h, _ = failing(tmp_path, Fault("GMAIL_SEND_EMAIL", 1, "lost_ack"))
    out = await h["mail_send"]({"to": ["ravi.shah@example.org"], "subject": "Tap", "body": "The tap is leaking."})
    text = out["content"][0]["text"]
    assert out["is_error"] is True and "timed out" in text and "may or may not have taken effect" in text and "check" in text.lower()
    assert len(sim.sent) == 1, "it was sent once, by the call that reported a timeout, and not again"
