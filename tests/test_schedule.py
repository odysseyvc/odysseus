"""Stage 3 tests: reminders, routines and the loop that runs them.

Time is a variable the tests set by hand, so "tomorrow at nine" takes no time.
The model is a scripted stand-in, as before.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from odysseus.agent import ROUTINE_PROMPT, AgentRunner, make_policy
from odysseus.config import Settings, make_clock
from odysseus.conversation import Conversation
from odysseus.journal import Journal, Ledger
from odysseus.memory import MemoryStore
from odysseus.routines import make_routine_runner
from odysseus.schedule import NO_REPLY, ScheduleError, ScheduleStore, Scheduler, next_occurrence, parse_when
from odysseus.tools import ALLOWED_TOOLS, ROUTINE_TOOLS, SCHEDULE_TOOLS, SEARCH_TOOL, make_schedule_handlers, make_tool_server

from test_slice import OWNER, FakeChat, FakeClient, FakeSpec, result

T0 = datetime(2026, 11, 17, 10, 0)     # a Tuesday


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


class FailingChat(FakeChat):
    async def send(self, text):
        raise ConnectionError("telegram unreachable")


def rig(tmp_path, routine=None, daily=3.0, chat=None):
    clock = Clock()
    journal = Journal(tmp_path / "journal.jsonl")
    ledger = Ledger(journal)
    store = ScheduleStore(tmp_path / "schedule.db", now=clock)
    chat = chat or FakeChat()
    calls, delivered = [], []

    async def run_routine(text):
        calls.append(text)
        return routine(text) if routine else ("done", "Here is your summary.", 0.004)

    sched = Scheduler(store, chat, journal, ledger, daily, run_routine, now=clock, on_delivered=delivered.append)
    return SimpleNamespace(clock=clock, store=store, chat=chat, sched=sched, journal=journal, ledger=ledger,
                           calls=calls, delivered=delivered)


def outcomes(x):
    return [r["outcome"] for r in x.store.runs()]


# --- Times -------------------------------------------------------------------

def test_when_must_be_a_local_time_in_the_near_future():
    assert parse_when("2026-11-18T09:00", T0) == datetime(2026, 11, 18, 9, 0)
    for bad in ("tomorrow at nine", "2026-11-16T09:00", "2026-11-18T09:00+05:30", "2030-01-01T00:00"):
        with pytest.raises(ScheduleError):
            parse_when(bad, T0)


def test_next_occurrence_follows_the_rhythm_and_never_lands_in_the_past():
    fri = datetime(2026, 11, 20, 9, 0)
    assert next_occurrence(fri, "once", fri) is None
    assert next_occurrence(fri, "daily", fri) == datetime(2026, 11, 21, 9, 0)
    assert next_occurrence(fri, "weekdays", fri) == datetime(2026, 11, 23, 9, 0), "Friday's next weekday is Monday"
    assert next_occurrence(fri, "weekly", fri) == datetime(2026, 11, 27, 9, 0)
    assert next_occurrence(fri, "hourly", fri) == datetime(2026, 11, 20, 10, 0)
    # After ten days switched off, a daily job has one next time, in the future, not ten.
    assert next_occurrence(fri, "daily", fri + timedelta(days=10, hours=1)) == datetime(2026, 12, 1, 9, 0)


def test_the_clock_can_be_pinned_to_a_time_zone():
    kolkata, utc = make_clock("Asia/Kolkata")(), make_clock("UTC")()
    assert kolkata.tzinfo is None and abs((kolkata - utc) - timedelta(hours=5, minutes=30)) < timedelta(seconds=5)


# --- The store ---------------------------------------------------------------

def test_jobs_survive_a_restart_and_bad_ones_are_refused(tmp_path):
    x = rig(tmp_path)
    job = x.store.add("reminder", "Call the dentist", "2026-11-18T09:00")
    again = ScheduleStore(tmp_path / "schedule.db", now=x.clock)
    assert [(j.id, j.text, j.next_run) for j in again.jobs()] == [(job.id, "Call the dentist", datetime(2026, 11, 18, 9, 0))]
    for kind, text, when, repeat in (("alarm", "x", "2026-11-18T09:00", "once"), ("reminder", "", "2026-11-18T09:00", "once"),
                                     ("reminder", "x", "2026-11-18T09:00", "fortnightly"), ("reminder", "x" * 601, "2026-11-18T09:00", "once")):
        with pytest.raises(ScheduleError):
            x.store.add(kind, text, when, repeat)
    assert len(x.store.jobs()) == 1


def test_two_jobs_never_get_the_same_id(tmp_path, monkeypatch):
    # Ids are four characters so they are easy to type, which makes a clash possible. Found when a test run hit one.
    import odysseus.schedule as schedule_module
    ids = iter(["aaaa", "aaaa", "aaaa", "bbbb"])
    monkeypatch.setattr(schedule_module.secrets, "token_hex", lambda n: next(ids))
    x = rig(tmp_path)
    first = x.store.add("reminder", "one", "2026-11-18T09:00")
    second = x.store.add("reminder", "two", "2026-11-18T09:00")
    assert (first.id, second.id) == ("aaaa", "bbbb") and len(x.store.jobs()) == 2


def test_there_is_a_ceiling_on_the_number_of_jobs(tmp_path):
    x = rig(tmp_path)
    for i in range(50):
        x.store.add("reminder", f"thing {i}", "2026-11-18T09:00")
    with pytest.raises(ScheduleError):
        x.store.add("reminder", "one too many", "2026-11-18T09:00")


# --- Reminders ---------------------------------------------------------------

async def test_a_reminder_is_sent_by_code_at_its_time_and_not_before(tmp_path):
    x = rig(tmp_path)
    x.store.add("reminder", "Call the dentist", "2026-11-17T10:30")
    assert await x.sched.tick() == 0 and x.chat.sent == []
    x.clock.advance(minutes=30)
    assert await x.sched.tick() == 1
    assert x.chat.sent == ["Reminder: Call the dentist"]
    assert x.calls == [], "no model is involved in a reminder"
    assert outcomes(x) == ["delivered"] and x.store.jobs() == [], "a one-off is gone once sent"
    assert await x.sched.tick() == 0 and len(x.chat.sent) == 1, "and is never sent twice"
    assert x.delivered == ["Reminder: Call the dentist"]


async def test_a_repeating_reminder_moves_to_its_next_time(tmp_path):
    x = rig(tmp_path)
    x.store.add("reminder", "Stand-up", "2026-11-20T09:00", "weekdays")     # a Friday
    x.clock.t = datetime(2026, 11, 20, 9, 0)
    await x.sched.tick()
    assert x.store.jobs()[0].next_run == datetime(2026, 11, 23, 9, 0)
    assert len(x.chat.sent) == 1


async def test_after_downtime_a_missed_reminder_is_sent_once_and_marked_late(tmp_path):
    x = rig(tmp_path)
    x.store.add("reminder", "Take the bins out", "2026-11-17T20:00", "daily")
    x.clock.advance(days=4)                                                  # switched off for four days
    assert await x.sched.tick() == 1
    assert len(x.chat.sent) == 1 and "late because I was not running" in x.chat.sent[0]
    assert x.store.jobs()[0].next_run > x.clock.t, "the next time is in the future: no burst of four"
    assert await x.sched.tick() == 0


async def test_a_reminder_that_cannot_be_sent_is_kept_and_tried_again_before_it_is_called_failed(tmp_path):
    x = rig(tmp_path, chat=FailingChat())
    x.store.add("reminder", "Call the dentist", "2026-11-17T10:00")
    await x.sched.tick()
    assert outcomes(x) == [] and len(x.store.jobs()) == 1, "not delivered, and not deleted either"
    assert x.store.jobs()[0].next_run == T0 + timedelta(minutes=1)
    assert await x.sched.tick() == 0, "not tried again until that minute has passed"
    for wait in (1, 5, 15, 30):
        x.clock.advance(minutes=wait)
        await x.sched.tick()
    assert outcomes(x) == ["failed"] and "ConnectionError" in x.store.runs()[0]["detail"] and x.store.jobs() == []
    kinds = [(r["kind"], r.get("outcome")) for r in x.journal.read()]
    assert kinds.count(("scheduled_run", "retrying")) == 4 and ("error", None) in kinds, "the failed announcement is logged too"


async def test_a_reminder_that_fails_once_is_delivered_on_the_next_try_and_says_it_is_late(tmp_path):
    x = rig(tmp_path)
    real, state = x.chat.send, {"fail": True}

    async def flaky(text):
        if state.pop("fail", False):
            raise ConnectionError("network down")
        await real(text)
    x.chat.send = flaky
    x.store.add("reminder", "Call the dentist", "2026-11-17T10:00", "daily")
    await x.sched.tick()
    x.clock.advance(minutes=6)
    await x.sched.tick()
    assert len(x.chat.sent) == 1 and "Call the dentist" in x.chat.sent[0] and "late because I could not send it then" in x.chat.sent[0]
    assert outcomes(x) == ["delivered"] and x.store.jobs()[0].next_run == T0 + timedelta(days=1), "its daily rhythm is kept"


async def test_a_message_the_first_channel_refuses_goes_out_on_the_second(tmp_path):
    x = rig(tmp_path, chat=FailingChat())
    other = FakeChat()
    x.sched._fallback = other
    x.store.add("reminder", "Call the dentist", "2026-11-17T10:00")
    await x.sched.tick()
    assert other.sent == ["Reminder: Call the dentist"] and outcomes(x) == ["delivered"]


# --- Routines: the three outcomes ----------------------------------------------

async def test_a_routine_that_has_something_to_say_is_delivered_and_costed(tmp_path):
    x = rig(tmp_path)
    job = x.store.add("routine", "Summarise today's top AI news in three lines.", "2026-11-17T10:00", "daily")
    await x.sched.tick()
    assert x.calls == ["Summarise today's top AI news in three lines."]
    assert x.chat.sent == [f"Here is your summary.\n\n(Scheduled routine [{job.id}])"]
    assert outcomes(x) == ["delivered"] and x.ledger.spent_today() == pytest.approx(0.004)
    assert x.delivered == ["Here is your summary."]


@pytest.mark.parametrize("reply", [NO_REPLY, "NO_REPLY.", ' "no_reply" '])
async def test_a_routine_with_nothing_to_say_is_quiet(tmp_path, reply):
    x = rig(tmp_path, routine=lambda text: ("done", reply, 0.002))
    x.store.add("routine", "Tell me only if Python 4 has been released.", "2026-11-17T10:00", "daily")
    await x.sched.tick()
    assert x.chat.sent == [] and outcomes(x) == ["quiet"]
    assert x.ledger.spent_today() == pytest.approx(0.002), "a quiet run still cost something, and it is counted"


@pytest.mark.parametrize("routine, reason", [
    (lambda t: ("error", "", 0.001), "ended as 'error'"),
    (lambda t: ("budget", "partial", 0.5), "ended as 'budget'"),
    (lambda t: ("done", "   ", 0.001), "produced nothing"),
])
async def test_a_routine_that_fails_is_announced(tmp_path, routine, reason):
    x = rig(tmp_path, routine=routine)
    x.store.add("routine", "Summarise the news.", "2026-11-17T10:00")
    await x.sched.tick()
    assert outcomes(x) == ["failed"]
    assert len(x.chat.sent) == 1 and "failed and was not carried out" in x.chat.sent[0] and reason in x.chat.sent[0]


async def test_a_routine_that_crashes_is_announced_and_the_loop_survives(tmp_path):
    def boom(text):
        raise RuntimeError("search is down")

    x = rig(tmp_path, routine=boom)
    x.store.add("routine", "Summarise the news.", "2026-11-17T10:00")
    x.store.add("reminder", "Still here", "2026-11-17T10:00")
    assert await x.sched.tick() == 2
    assert sorted(outcomes(x)) == ["delivered", "failed"]
    assert any("search is down" in s for s in x.chat.sent) and "Reminder: Still here" in x.chat.sent


async def test_a_routine_is_not_run_once_the_daily_budget_is_spent(tmp_path):
    x = rig(tmp_path, daily=0.01)
    x.ledger.add(0.01)
    x.store.add("routine", "Summarise the news.", "2026-11-17T10:00")
    x.store.add("reminder", "Free of charge", "2026-11-17T10:00")
    await x.sched.tick()
    assert x.calls == [], "no model call was made"
    assert any("budget" in s and "failed" in s for s in x.chat.sent)
    assert "Reminder: Free of charge" in x.chat.sent, "reminders cost nothing and still arrive"


async def test_every_run_has_exactly_one_recorded_outcome(tmp_path):
    replies = iter([("done", "News.", 0.001), ("done", NO_REPLY, 0.001), ("error", "", 0.0)])
    x = rig(tmp_path, routine=lambda t: next(replies))
    for i in range(3):
        x.store.add("routine", f"job {i}", "2026-11-17T10:00")
    x.store.add("reminder", "r", "2026-11-17T10:00")
    await x.sched.tick()
    assert sorted(outcomes(x)) == ["delivered", "delivered", "failed", "quiet"]
    logged = [r for r in x.journal.read() if r["kind"] == "scheduled_run"]
    assert len(logged) == 4 and all(r["outcome"] in ("delivered", "quiet", "failed") for r in logged)


# --- The routine's agent can only read -----------------------------------------

async def test_the_routine_agent_is_read_only_cheap_and_starts_fresh_each_time(tmp_path):
    settings = Settings(telegram_token="t", owner_id=OWNER, anthropic_api_key="k", exa_api_key="e", data_dir=tmp_path)
    journal, memory, clients = Journal(tmp_path / "j.jsonl"), MemoryStore(tmp_path / "memory", now=Clock()), []
    memory.remember("Is vegetarian")

    def factory(options=None):
        clients.append(FakeClient([[result(text="Lentil soup.", cost=0.003)]], options=options))
        return clients[-1]

    run = make_routine_runner(settings, journal, object(), memory, Clock(), client_factory=factory)
    assert await run("Suggest a lunch.") == ("done", "Lentil soup.", pytest.approx(0.003))
    assert await run("Suggest a lunch.") == ("done", "Lentil soup.", pytest.approx(0.003)), "cost is not carried between runs"
    o = clients[0].options
    assert o.model == "claude-haiku-5-5" and o.system_prompt == ROUTINE_PROMPT and o.tools == []
    assert set(o.allowed_tools) == ROUTINE_TOOLS == {SEARCH_TOOL, "mcp__odysseus__recall"}
    assert len(clients) == 2 and all(c.options.resume is None for c in clients) and not any(c.connected for c in clients)
    p = clients[0].prompts[0]
    assert "Is vegetarian" in p and "ROUTINE INSTRUCTIONS" in p and p.endswith("[Tuesday 17 November 2026, 10:00]")
    policy = make_policy(ROUTINE_TOOLS)
    assert isinstance(await policy(SEARCH_TOOL, {}, None), PermissionResultAllow)
    for name in ("mcp__odysseus__remember", "mcp__odysseus__schedule", "mcp__odysseus__forget", "Bash"):
        assert isinstance(await policy(name, {}, None), PermissionResultDeny), f"{name} must not run unattended"


def test_servers_build_and_the_gate_lists_the_schedule_tools(tmp_path):
    journal, memory = Journal(tmp_path / "j.jsonl"), MemoryStore(tmp_path / "memory")
    store = ScheduleStore(tmp_path / "s.db")
    assert make_tool_server(FakeSpec(), journal, memory, store)["name"] == "odysseus"
    assert make_tool_server(FakeSpec(), journal, memory, read_only=True)["name"] == "odysseus"
    assert set(SCHEDULE_TOOLS) <= ALLOWED_TOOLS and not (set(SCHEDULE_TOOLS) & ROUTINE_TOOLS)


# --- The tools, and what the owner sees and can do -------------------------------

async def test_schedule_tools_validate_and_log(tmp_path):
    x = rig(tmp_path)
    h = make_schedule_handlers(x.store, x.journal)
    ok = await h["schedule"]({"kind": "reminder", "text": "Call the dentist", "when": "2026-11-18T09:00"})
    assert "Scheduled: [" in ok["content"][0]["text"] and "Wed 18 Nov 2026, 09:00" in ok["content"][0]["text"]
    past = await h["schedule"]({"kind": "reminder", "text": "x", "when": "2026-11-01T09:00"})
    assert past["is_error"] is True and "in the past" in past["content"][0]["text"] and "It is now 2026-11-17 10:00" in past["content"][0]["text"]
    job_id = x.store.jobs()[0].id
    assert "Call the dentist" in (await h["list_schedule"]({}))["content"][0]["text"]
    assert (await h["cancel_schedule"]({"id": "zzzz"}))["is_error"] is True
    assert "Cancelled" in (await h["cancel_schedule"]({"id": job_id}))["content"][0]["text"] and x.store.jobs() == []


def conversation(tmp_path, on_query=None):
    x = rig(tmp_path)
    settings = Settings(telegram_token="t", owner_id=OWNER, anthropic_api_key="k", exa_api_key="e", data_dir=tmp_path)
    memory, clients = MemoryStore(tmp_path / "memory", now=x.clock), []

    class Client(FakeClient):
        async def query(self, prompt):
            await super().query(prompt)
            if on_query:
                on_query(prompt, x.store)

    def factory(options=None):
        clients.append(Client([[result(text="Done.")]], options=options))
        return clients[-1]

    runner = AgentRunner(settings, x.journal, object(), client_factory=factory)
    x.conv = Conversation(settings, x.journal, x.ledger, runner, memory, now=x.clock, schedule=x.store)
    x.clients = clients
    return x


async def say(x, text):
    await x.conv.handle(OWNER, text, x.chat)
    await x.conv.wait()


async def test_the_owner_is_told_of_every_schedule_change_the_model_makes(tmp_path):
    x = conversation(tmp_path, on_query=lambda prompt, store: store.add("reminder", "Call the dentist", "2026-11-18T09:00"))
    await say(x, "remind me tomorrow at nine to call the dentist")
    assert x.chat.sent[-1].startswith("Done.\n\nSchedule: set [") and "reminder at Wed 18 Nov 2026, 09:00: Call the dentist." in x.chat.sent[-1]


async def test_the_owner_can_list_and_cancel_without_the_model(tmp_path):
    x = conversation(tmp_path)
    job = x.store.add("routine", "Morning news", "2026-11-18T08:00", "daily")
    await x.conv.handle(OWNER, "/schedule", x.chat)
    assert f"[{job.id}] routine at Wed 18 Nov 2026, 08:00, repeating daily: Morning news" in x.chat.sent[-1]
    await x.conv.handle(OWNER, f"/cancel {job.id}", x.chat)
    assert x.chat.sent[-1].startswith("Cancelled: [") and x.store.jobs() == []
    await x.conv.handle(OWNER, "/cancel zzzz", x.chat)
    assert "no scheduled job" in x.chat.sent[-1]
    await x.conv.handle(OWNER, "/cancel", x.chat)
    assert x.chat.sent[-1] == "Nothing is running.", "a bare /cancel still means stop"
    assert x.clients == []
    await x.conv.handle(999, "/schedule", x.chat)
    assert len(x.chat.sent) == 4, "a stranger learns nothing"


async def test_the_model_is_told_what_scheduled_jobs_sent_so_follow_ups_make_sense(tmp_path):
    x = conversation(tmp_path)
    x.conv.record_delivery("Top AI story: a new chip was announced.")
    await say(x, "tell me more about that")
    p = x.clients[0].prompts[0]
    assert "SENT TO THE OWNER BY SCHEDULED JOBS" in p and "a new chip was announced" in p
    await say(x, "thanks")
    assert "SENT TO THE OWNER" not in x.clients[0].prompts[1], "told once, not on every message"


# --- A routine that runs every day must not say the same thing every day ---------

async def test_a_repeating_routine_is_shown_what_it_sent_before(tmp_path):
    clock, journal = Clock(), Journal(tmp_path / "journal.jsonl")
    store, chat, seen = ScheduleStore(tmp_path / "schedule.db", now=clock), FakeChat(), []

    async def run_routine(text, previous=()):
        seen.append(previous)
        return "done", f"Digest {len(seen)}", 0.01

    def scheduler():
        return Scheduler(store, chat, journal, Ledger(journal), 3.0, run_routine, now=clock, history_file=tmp_path / "history.json")

    job = store.add("routine", "Send me the sourcing digest.", "2026-11-17T10:00", "daily")
    sched = scheduler()
    for _ in range(4):
        await sched.tick()
        clock.advance(days=1)
    assert seen == [(), ("Digest 1",), ("Digest 1", "Digest 2"), ("Digest 1", "Digest 2", "Digest 3")]
    clock.advance(days=1)
    await scheduler().tick()
    assert seen[-1] == ("Digest 2", "Digest 3", "Digest 4"), "kept across a restart, and only the last three"
    assert job.id in (tmp_path / "history.json").read_text(encoding="utf-8")


async def test_a_quiet_or_failed_run_adds_nothing_to_what_was_sent(tmp_path):
    clock, journal = Clock(), Journal(tmp_path / "journal.jsonl")
    store, chat, seen = ScheduleStore(tmp_path / "schedule.db", now=clock), FakeChat(), []
    replies = iter([("done", NO_REPLY, 0.0), ("error", "", 0.0), ("done", "Two new founders.", 0.01), ("done", "x", 0.0)])

    async def run_routine(text, previous=()):
        seen.append(previous)
        return next(replies)

    store.add("routine", "Digest.", "2026-11-17T10:00", "daily")
    sched = Scheduler(store, chat, journal, Ledger(journal), 3.0, run_routine, now=clock, history_file=tmp_path / "history.json")
    for _ in range(4):
        await sched.tick()
        clock.advance(days=1)
    assert seen == [(), (), (), ("Two new founders.",)]


async def test_the_routine_agent_is_told_what_it_sent_before_and_gets_a_fresh_allowance(tmp_path):
    from odysseus.mcpapp import McpApp
    from odysseus.tools import routine_tools
    from test_mcpapp import FakeService
    settings = Settings(telegram_token="t", owner_id=OWNER, anthropic_api_key="k", exa_api_key="e", data_dir=tmp_path)
    journal, memory, clients = Journal(tmp_path / "j.jsonl"), MemoryStore(tmp_path / "memory", now=Clock()), []
    app = McpApp("granola", FakeService(), journal)

    def factory(options=None):
        clients.append(FakeClient([[result(text="Nothing new today.", cost=0.003)]], options=options))
        return clients[-1]

    run = make_routine_runner(settings, journal, object(), memory, Clock(), client_factory=factory, services=[app])
    app.unattended_left = 0
    await run("Send the digest.", ("Asha Rao is starting a robotics company.",))
    assert app.unattended_left == 12 and app.unattended_seen == []
    o, p = clients[0].options, clients[0].prompts[0]
    assert set(o.allowed_tools) == routine_tools(False, [app]) and "granola_find" in o.system_prompt
    assert "WHAT THIS ROUTINE SENT THE LAST TIMES IT RAN" in p and "Asha Rao is starting a robotics company." in p
    assert p.index("ROUTINE INSTRUCTIONS") < p.index("WHAT THIS ROUTINE SENT") and p.endswith("[Tuesday 17 November 2026, 10:00]")
    await run("Send the digest.")
    assert "WHAT THIS ROUTINE SENT" not in clients[1].prompts[0]


def test_a_job_can_repeat_every_half_hour():
    from odysseus.schedule import REPEATS
    at = datetime(2026, 11, 20, 9, 0)
    assert "half-hourly" in REPEATS and next_occurrence(at, "half-hourly", at) == datetime(2026, 11, 20, 9, 30)
    assert next_occurrence(at, "half-hourly", datetime(2026, 11, 20, 11, 10)) == datetime(2026, 11, 20, 11, 30), "after a gap it resumes on its rhythm, once"
