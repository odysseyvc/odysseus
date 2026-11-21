"""Failures found by reading the log of real use (scripts/review_log.py), each with the fix that now holds.

Six causes lay behind the seventeen kinds the first reading listed:

  the network being down     three loops that run unwatched each wrote an error every time the laptop lost its connection
  a read that was retried    an attempt that was about to be made again was written like the call's failure
  two reads treated as writes  a dropped connection on them was reported as something that "may or may not have taken effect"
  a guessed tool name        search_records for search-records; search_posts for search_posts_all
  a limit the model could not see  routine instructions over 600 characters, refused only after the call
  a failure nobody heard of  a routine timed out during an outage, and the notice of that failed to send in the same outage

Nothing here calls a model or a service.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from odysseus.apps import READS, AppError, Apps, near_tools, same_tool
from odysseus.journal import Journal, Outage, looks_offline

from test_schedule import T0, FailingChat, FakeChat, outcomes, rig


# --- the network being down ------------------------------------------------------------------------------------------

def test_an_outage_is_one_line_when_it_begins_and_one_when_it_ends_and_anything_else_is_still_an_error(tmp_path):
    clock = [1000.0]
    journal = Journal(tmp_path / "j.jsonl")
    outage = Outage(journal, "brief.tick", clock=lambda: clock[0])
    outage.worked()
    for _ in range(40):                                 # forty minutes with no network
        outage.failed(ConnectionError("Connection error."))
        clock[0] += 60
    outage.worked()
    outage.worked()
    outage.failed(KeyError("items"))                    # a real fault, twice
    outage.failed(KeyError("items"))
    rows = [(r["kind"], r.get("after_s")) for r in journal.read()]
    assert rows == [("offline", None), ("online", 2400), ("error", None), ("error", None)]
    for said in ("APIConnectionError: Connection error.", "the mailbox service could not be reached (ConnectError)", "APITimeoutError: Request timed out.",
                 "X did not answer in time. Nothing is known about whether it was done.", "the routine ended as 'error': it timed out waiting for a service"):
        assert looks_offline(said), said
    assert looks_offline(asyncio.TimeoutError()) and looks_offline(type("TelegramNetworkError", (Exception,), {})())
    for said in ("That id does not exist.", "Too long: 972 characters, and the limit is 600.", "KeyError: 'items'"):
        assert not looks_offline(said), said


async def test_the_mailbox_loop_records_an_outage_once_and_its_end(tmp_path):
    from test_mailbox import rig as mailbox_rig
    from odysseus.mailbox import MailboxError
    x = mailbox_rig(tmp_path)
    await x.box.make()
    state = {"down": True}

    async def messages(inbox_id, after="", limit=25):
        if state["down"]:
            raise MailboxError("the mailbox service could not be reached (ConnectError)")
        return []
    x.service.messages = messages
    task = asyncio.create_task(x.box.run_forever(every_s=0.005))
    await asyncio.sleep(0.06)
    state["down"] = False
    await asyncio.sleep(0.04)
    task.cancel()
    kinds = [r["kind"] for r in x.journal.read() if r.get("where") == "mailbox.tick"]
    assert kinds == ["offline", "online"], kinds


# --- reads -------------------------------------------------------------------------------------------------------------

class Flaky:
    """A connector that fails a set number of times and then answers."""

    def __init__(self, failures):
        self.failures, self.calls = failures, 0

    def execute(self, slug, arguments):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("Connection error.")
        return {"messages": [], "emailAddress": "owner@firm.example"}

    def connected(self):
        return {"gmail": True, "googlecalendar": True}


async def test_a_read_that_code_makes_is_tried_again_and_only_its_last_failure_is_the_calls_failure(tmp_path):
    from datetime import datetime
    assert {"mail_for_code", "mail_profile"} <= READS and not {"mail_send", "mail_reply", "calendar_create", "mail_send_by_code"} & READS
    journal = Journal(tmp_path / "j.jsonl")
    apps = Apps(Flaky(2), journal, lambda: datetime(2026, 11, 21, 9, 0), "Asia/Kolkata")
    apps.retry_wait_s = 0
    await apps._call("mail_for_code", "GMAIL_FETCH_EMAILS", {"query": "newer_than:1d"})
    rows = [(r["ok"], r.get("retrying")) for r in journal.read() if r["kind"] == "tool"]
    assert rows == [(False, True), (False, True), (True, None)], "two attempts that were made again, then the answer"
    down = Apps(Flaky(9), Journal(tmp_path / "d.jsonl"), lambda: datetime(2026, 11, 21, 9, 0), "Asia/Kolkata")
    down.retry_wait_s = 0
    with pytest.raises(AppError) as e:
        await down._call("mail_profile", "GMAIL_GET_PROFILE", {})
    assert "may or may not have taken effect" not in str(e.value), "a read changed nothing, and is not described as if it might have"
    last = [r for r in Journal(tmp_path / "d.jsonl").read() if r["kind"] == "tool"][-1]
    assert last["ok"] is False and "retrying" not in last and last["attempt"] == 3


# --- a guessed tool name -------------------------------------------------------------------------------------------------

def test_a_tool_name_with_the_wrong_joiner_is_understood_and_a_wrong_name_is_answered_with_the_nearest():
    attio = ["search-records", "list-records", "create-record", "get-records-by-ids", "create-note", "whoami"]
    assert same_tool("search_records", attio) == "search-records" and same_tool("Search Records", attio) == "search-records"
    assert same_tool("search_everything", attio) == "" and same_tool("search-records", attio) == "search-records"
    x = ["search_posts_all", "search_posts_recent_counts", "get_user_by_username", "get_post_by_id"]
    assert near_tools("search_posts", x) == " The closest names are: search_posts_all, search_posts_recent_counts."
    assert "search_posts_all" in near_tools("search_posts_recent", x)
    assert near_tools("frobnicate", x) == ""


async def test_attio_find_takes_an_underscore_for_a_hyphen_and_names_the_nearest_tools_otherwise(tmp_path):
    from odysseus.attio import Attio

    class Backend:
        async def list_tools(self):
            return [{"name": n, "title": n, "labels": {"read_only_hint": True}, "description": f"About {n}.", "inputs": {"type": "object"}}
                    for n in ("search-records", "list-records", "create-note")]

        async def call(self, name, arguments):
            return "{}"
    journal = Journal(tmp_path / "j.jsonl")
    attio = Attio(Backend(), journal)
    find = attio.handlers()["attio_find"]
    out = await find({"tools": ["search_records"]})
    assert not out.get("is_error") and out["content"][0]["text"].startswith("search-records\n"), "answered under the real name, so the next call uses it"
    wrong = await find({"tools": ["search-people"]})
    assert wrong.get("is_error") and "The closest names are: search-records" in wrong["content"][0]["text"]
    run = await attio.handlers()["attio_run"]({"tool": "search_records", "arguments": {}})
    assert run.get("is_error") and "joined with hyphens, not underscores" in run["content"][0]["text"], "nothing is run under a name that was not looked up"


# --- a limit the model could not see -------------------------------------------------------------------------------------

def test_the_schedule_tool_says_how_long_instructions_may_be(tmp_path):
    import inspect
    from odysseus import tools
    from odysseus.schedule import MAX_TEXT
    source = inspect.getsource(tools.make_tool_server)
    assert '"maxLength": MAX_TEXT' in source and "At most {MAX_TEXT} characters" in source and MAX_TEXT == 600


def test_a_refusal_with_nobody_to_ask_says_not_to_send_it_again():
    import inspect
    from odysseus import mcpapp
    assert "Do not send it again in this run" in inspect.getsource(mcpapp)


# --- a failure nobody heard of -------------------------------------------------------------------------------------------

async def test_a_routine_that_timed_out_is_run_once_more_and_one_that_failed_otherwise_is_not(tmp_path):
    results = [("error", "", 0.001), ("done", "Here is your summary.", 0.004)]
    x = rig(tmp_path, routine=lambda text: results.pop(0))
    x.sched._run_routine.runner = SimpleNamespace(timed_out=True)
    x.store.add("routine", "Summarise the news", "2026-11-17T10:00", "daily")
    await x.sched.tick()
    assert outcomes(x) == [] and x.chat.sent == [], "not called failed yet, and nothing said"
    assert x.store.jobs()[0].next_run == T0 + timedelta(minutes=5)
    x.clock.advance(minutes=5)
    x.sched._run_routine.runner.timed_out = False
    await x.sched.tick()
    assert outcomes(x) == ["delivered"] and "Here is your summary." in x.chat.sent[0] and len(x.calls) == 2
    assert x.store.jobs()[0].next_run == T0 + timedelta(days=1), "its place in its rhythm is counted from when it was due"
    assert abs(x.ledger.spent_today() - 0.005) < 1e-9, "both runs are paid for and counted"

    twice = rig(tmp_path / "twice", routine=lambda text: ("error", "", 0.001))
    twice.sched._run_routine.runner = SimpleNamespace(timed_out=True)
    twice.store.add("routine", "Summarise the news", "2026-11-17T10:00")
    await twice.sched.tick()
    twice.clock.advance(minutes=5)
    await twice.sched.tick()
    assert outcomes(twice) == ["failed"] and len(twice.calls) == 2 and "failed and was not carried out" in twice.chat.sent[0], "once more, not for ever"

    other = rig(tmp_path / "other", routine=lambda text: ("turn_limit", "", 0.002))
    other.store.add("routine", "Summarise the news", "2026-11-17T10:00")
    await other.sched.tick()
    assert outcomes(other) == ["failed"] and len(other.calls) == 1, "a routine that failed for its own reasons is not paid for twice"


async def test_a_failure_notice_that_could_not_be_sent_is_said_when_a_message_next_gets_out(tmp_path):
    chat = FailingChat()
    x = rig(tmp_path, routine=lambda text: ("turn_limit", "", 0.0), chat=chat)
    x.store.add("routine", "Summarise the news", "2026-11-17T10:00")
    await x.sched.tick()
    assert outcomes(x) == ["failed"] and len(x.sched._unsaid) == 1
    x.clock.advance(minutes=1)
    await x.sched.tick()
    assert [r["kind"] for r in x.journal.read() if r.get("where") == "scheduler.announce"] == ["offline"], "logged once, however long it lasts"
    working = FakeChat()
    x.sched._chat = working
    x.clock.advance(minutes=1)
    await x.sched.tick()
    assert x.sched._unsaid == [] and "failed and was not carried out" in working.sent[0], "silence never means failure"
