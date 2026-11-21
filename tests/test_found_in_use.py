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


# --- found by using it as a person would, in the chat ------------------------------------------------------------------

async def test_the_group_command_typed_in_a_private_chat_is_answered_by_code_and_reaches_no_model(tmp_path):
    from test_people import HOST, Chat, invite
    from test_people import rig as house_rig
    x = house_rig(tmp_path)
    chat = Chat()
    await x.house.handle(HOST, "/firm on", chat)
    assert "for a group chat, not for this one" in chat.sent[-1] and "Nothing was switched on here" in chat.sent[-1]
    assert x.host.conversation.heard == [], "a model shown this command answered that a mode was on; there is no such mode"
    code = await invite(x)
    await x.house.handle(7, f"/start {code}", Chat(who="Tara Menon"))
    member = Chat()
    await x.house.handle(7, "/firm on", member)
    assert "person who runs this assistant" in member.sent[-1] and x.built[7].conversation.heard == []


def test_a_reminder_is_stored_without_the_word_that_is_added_when_it_is_sent(tmp_path):
    from odysseus.schedule import ScheduleStore
    from test_schedule import Clock
    store = ScheduleStore(tmp_path / "s.db", now=Clock())
    assert store.add("reminder", "Reminder: check the term sheet", "2026-11-17T10:00").text == "check the term sheet"
    assert store.add("reminder", "reminder - Reminder: call the bank", "2026-11-17T10:05").text == "call the bank"
    assert store.add("reminder", "Call about the reminder: it lapsed", "2026-11-17T10:10").text == "Call about the reminder: it lapsed"
    assert store.add("routine", "Reminder: summarise the news", "2026-11-17T10:15").text == "Reminder: summarise the news", "a routine's instructions are left alone"
    import inspect
    from odysseus import tools
    assert "without the word Reminder" in inspect.getsource(tools.make_tool_server) and "nothing added that they did not say" in inspect.getsource(tools.make_tool_server)


async def test_a_mailbox_key_that_may_not_send_is_explained_in_words_the_owner_can_act_on():
    from odysseus.mailbox import AgentMail, MailboxError

    class Refusing:
        async def request(self, method, url, **kw):
            return SimpleNamespace(status_code=403, json=lambda: {"name": "ForbiddenError", "code": "missing_permission", "message": "Forbidden"})

    class Odd:
        async def request(self, method, url, **kw):
            return SimpleNamespace(status_code=409, json=lambda: {"name": "Conflict", "code": "already_exists", "message": "Inbox exists"})
    with pytest.raises(MailboxError) as e:
        await AgentMail("k", Refusing()).send("inbox-1", ["a@b.example"], [], "s", "t")
    assert "can read, not send" in str(e.value) and "message_send" in str(e.value)
    with pytest.raises(MailboxError) as e:
        await AgentMail("k", Odd()).send("inbox-1", ["a@b.example"], [], "s", "t")
    assert str(e.value) == "the mailbox service answered 409: Inbox exists (already_exists)"


def test_an_ordinary_search_with_its_purpose_is_not_put_to_the_owner_and_a_paragraph_or_a_long_number_still_is(tmp_path):
    from odysseus.taint import NEW_WORDS_A_SEARCH
    from odysseus.tools import SEARCH_TOOL, make_gate
    gate = make_gate(Journal(tmp_path / "j.jsonl"), taint_file=tmp_path / "t.bin")
    gate.taint.add("A message about the weekly sync and other things besides, from a colleague.")
    research = {"query": "climate hardware funds in northern Europe that lead first rounds, partners, typical cheque, recent investments",
                "objective": "Find venture funds known for leading first rounds in climate hardware companies in Scandinavia and the Baltics "
                             "(for example Corvid Ventures, Nimbus Capital, Helio Partners, Zentrix Fund, Palmgrove Seed). Need partner names, "
                             "typical first cheque sizes, and three recent deals each. Prefer fund sites, registries, and the business press."}
    assert 30 < len(gate.taint.new_words(f"{research['query']} {research['objective']}")) <= NEW_WORDS_A_SEARCH
    assert gate.decide(SEARCH_TOOL, research) == "allow", "this was put to the owner, who had typed nothing private"
    paragraph = {"query": " ".join(f"word{chr(97 + i % 26)}{chr(97 + i // 26)}" for i in range(NEW_WORDS_A_SEARCH + 5)), "objective": ""}
    assert gate.decide(SEARCH_TOOL, paragraph) == "ask"
    assert gate.decide(SEARCH_TOOL, {"query": "company with registration 4188392077", "objective": ""}) == "ask"


async def test_a_read_that_asks_for_more_than_a_tool_allows_is_given_the_most_it_allows(tmp_path):
    from odysseus.apps import within_bounds
    from odysseus.attio import Attio
    schema = {"type": "object", "properties": {"limit": {"type": "number", "minimum": 1, "maximum": 50}, "object": {"type": "string"}, "deep": {"type": "boolean"}}}
    assert within_bounds({"object": "deals", "limit": 100, "deep": True}, schema) == ({"object": "deals", "limit": 50, "deep": True}, ["limit 100 to 50"])
    assert within_bounds({"limit": 0}, schema) == ({"limit": 1}, ["limit 0 to 1"]) and within_bounds({"limit": 20}, schema) == ({"limit": 20}, [])
    assert within_bounds("not a dict", schema) == ("not a dict", []) and within_bounds({"limit": 100}, None) == ({"limit": 100}, [])
    calls = []

    class Backend:
        async def list_tools(self):
            return [{"name": "list-records", "title": "List records", "labels": {"read_only_hint": True}, "description": "List.", "inputs": schema},
                    {"name": "create-record", "title": "Create a record", "labels": {}, "description": "Create.", "inputs": schema}]

        async def call(self, name, arguments):
            calls.append((name, arguments))
            return "{}"
    attio = Attio(Backend(), Journal(tmp_path / "j.jsonl"))
    run = attio.handlers()["attio_run"]
    await attio.handlers()["attio_find"]({})
    out = await run({"tool": "list-records", "arguments": {"object": "deals", "limit": 100}})
    assert calls[-1] == ("list-records", {"object": "deals", "limit": 50}) and "limit 100 to 50" in out["content"][0]["text"]
    await run({"tool": "create-record", "arguments": {"object": "deals", "limit": 100}})
    assert calls[-1] == ("create-record", {"object": "deals", "limit": 100}), "a change runs with exactly what the owner was shown"


async def test_with_nobody_else_on_the_copy_the_model_is_told_not_to_offer_a_colleagues_assistant(tmp_path):
    from test_fresh import rig as fresh_rig, say
    x = fresh_rig(tmp_path)
    x.conv.alone = lambda: True
    await say(x, "Who knows the founder of Corvid Freight?")
    assert "Nobody else uses this assistant yet" in x.clients[0].prompts[0]
    y = fresh_rig(tmp_path / "y")
    y.conv.alone = lambda: False
    await say(y, "Who knows the founder of Corvid Freight?")
    assert "Nobody else uses this assistant" not in y.clients[0].prompts[0]


async def test_messages_sent_quickly_are_taken_in_the_order_they_were_received(tmp_path):
    """The library starts on several messages at once. Each handler pauses for a different time before it reaches the
    conversation, as a download or a slow send does; the conversation must still hear them in order."""
    from datetime import datetime, timezone
    from odysseus.telegram import build_dispatcher
    heard = []

    class Conv:
        def is_owner(self, user_id):
            return True

        async def handle(self, user_id, text, chat, **more):
            await asyncio.sleep({"one": 0.03, "two": 0.0, "three": 0.01}[text])
            heard.append(text)
    dp = build_dispatcher(SimpleNamespace(), Conv())
    on_text = next(h.callback for h in dp.message.handlers if h.callback.__name__ == "on_text")

    def message(i, text):
        return SimpleNamespace(from_user=SimpleNamespace(id=1, full_name="Owner"), date=datetime.now(timezone.utc), text=text, message_id=i,
                               chat=SimpleNamespace(id=1))
    await asyncio.gather(*(on_text(message(i, t)) for i, t in enumerate(("one", "two", "three"))))
    assert heard == ["one", "two", "three"]


async def test_a_burst_of_waiting_messages_is_acknowledged_once_and_each_answer_replies_to_its_message(tmp_path):
    from test_slice import OWNER, call, make, result

    class Quoting(FakeChat):
        def __init__(self):
            super().__init__()
            self.quote = False
    x = make(tmp_path, [[result(text="first")], [result(text="second")], [result(text="third")]])
    x.clients_gate = asyncio.Event()
    chats = [Quoting(), Quoting(), Quoting()]
    x.conv.busy_for_test = True
    task = asyncio.create_task(x.conv.handle(OWNER, "one", chats[0]))
    await asyncio.sleep(0)
    await x.conv.handle(OWNER, "two", chats[1])
    await x.conv.handle(OWNER, "three", chats[2])
    await task
    await x.conv.wait()
    said = [m for c in chats for m in c.sent if m.startswith("Noted.")]
    assert len(said) == 1 and "in order" in said[0], "one line for the run of waiting messages"
    assert [c.quote for c in chats] == [False, True, True], "an answer to a message that waited is sent as a reply to it"
    assert any("second" in m for m in chats[1].sent) and any("third" in m for m in chats[2].sent)


async def test_a_fact_the_owner_forgets_is_withdrawn_from_the_conversation_in_progress(tmp_path):
    from test_fresh import OWNER, rig as fresh_rig, say
    x = fresh_rig(tmp_path)
    await say(x, "Hello")
    entry = x.conv._memory.profile()[0]
    await x.conv.handle(OWNER, f"/forget {entry.id}", x.chat)
    await say(x, "When should I schedule the call?")
    prompt = x.clients[0].prompts[-1]
    assert "THE OWNER HAS JUST REMOVED THIS FROM MEMORY" in prompt and entry.text in prompt and "Do not rely on it" in prompt
    await say(x, "And the next one?")
    assert "JUST REMOVED" not in x.clients[0].prompts[-1], "said once"
