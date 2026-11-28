"""Five faults that were open in the safety layer, and what now holds for each.

  questions that were waiting when the program restarted (offers.py)
  the day's budget during a task, not only before one (agent.py)
  a connection for events that opened and then died (watch.py)
  a log read whole at start-up however old it was (journal.py)
  private details leaving in other words than they were read in (taint.py)

Nothing here calls a model or a service.
"""
from __future__ import annotations

import gzip
import json
from datetime import timedelta

from odysseus.journal import KEEP_DAYS, Journal, Ledger
from odysseus.offers import KEEP_DAYS as OFFER_DAYS
from odysseus.offers import MOST, Offers
from odysseus.taint import NEW_WORDS_A_SEARCH, NEW_WORDS_AN_HOUR, Taint
from odysseus.tools import SEARCH_TOOL, describe_search, make_gate

from test_slice import OWNER, FakeChat, call, ends, make, result, say
from test_watch import NOW
from test_watch import rig as watch_rig


# --- questions that were waiting ---------------------------------------------------------------

def test_what_was_put_to_the_owner_is_still_there_after_a_restart(tmp_path):
    clock = [1_000_000.0]
    first = Offers(tmp_path / "offers.json", clock=lambda: clock[0])
    first["w1"] = ("read message id m2", "mail to my own address", object())      # a piece of work: the chat itself is not kept
    first["m1"] = "The owner prefers morning meetings."
    first["v1"] = {"filed": {"company": "Corvid Freight"}, "view": "call", "reasons": ["team", "market"], "field": ""}
    first["gone"] = "answered already"
    assert first.pop("gone") == "answered already" and first.pop("never", None) is None
    again = Offers(tmp_path / "offers.json", clock=lambda: clock[0])
    assert again["w1"] == ("read message id m2", "mail to my own address", None) and again["m1"] == "The owner prefers morning meetings."
    assert again["v1"]["reasons"] == ["team", "market"] and "gone" not in again
    del again["m1"]
    clock[0] += (OFFER_DAYS + 1) * 86400
    again["new"] = "a fact offered today"
    late = Offers(tmp_path / "offers.json", clock=lambda: clock[0])
    assert list(late) == ["new"], "a question nobody answered in two weeks is dropped"
    for i in range(MOST + 5):
        late[f"k{i}"] = "x"
    assert len(late) == MOST and "new" not in late and f"k{MOST + 4}" in late, "the oldest go when there are too many"
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert dict(Offers(tmp_path / "bad.json")) == {}, "an unreadable file must not stop the program"


async def test_work_offered_before_a_restart_runs_on_the_tap_after_it(tmp_path):
    x = make(tmp_path)
    await x.conv.offer_task(x.chat, "A message arrived. Read it?", "read message id m2", "mail to my own address")
    offer = x.chat.asks[-1][1]
    y = make(tmp_path)          # the program again, on the same folder
    assert await y.conv.handle_decision(OWNER, offer, True) == "I can no longer run this. Please ask me again.", "with no chat to report in, it says so"
    await x.conv.offer_task(x.chat, "Another. Read it?", "read message id m3", "mail to my own address")
    z = make(tmp_path)
    z.conv.own_chat = z.chat
    assert await z.conv.handle_decision(OWNER, x.chat.asks[-1][1], True) == "Approved. Working on it now."
    import asyncio
    for _ in range(100):
        if z.clients and z.clients[0].prompts:
            break
        await asyncio.sleep(0.02)
    await z.conv.wait()
    assert "read message id m3" in z.clients[0].prompts[0] and z.chat.sent, "it ran as a task and reported in the owner's own chat"
    assert await make(tmp_path).conv.handle_decision(OWNER, x.chat.asks[-1][1], True) != "Approved. Working on it now.", "one tap, one run"


# --- the day's budget, during a task -----------------------------------------------------------

async def test_a_task_is_ended_when_it_uses_up_what_was_left_of_the_day(tmp_path):
    big = {"input_tokens": 100_000, "output_tokens": 10_000}        # about $0.30 a call on this model
    script = [call(1, "Partial answer", big), call(2, "Never reached", big), result(cost=0.6)]
    x = make(tmp_path, [script], daily=1.00, task_usd=5.00)
    x.ledger.add(0.80)          # 20 cents of the day left: enough to start, not enough for one of these calls
    await say(x, "expensive")
    assert ends(x.journal)[-1]["status"] == "day_budget" and ends(x.journal)[-1]["turns"] == 1
    assert "$1.00 is where I stop and check with you" in x.chat.sent[-1] and "Partial answer" in x.chat.sent[-1]
    assert x.clients[0].interrupted.is_set()
    roomy = make(tmp_path / "roomy", [script], daily=5.00, task_usd=5.00)
    await say(roomy, "expensive")
    assert ends(roomy.journal)[-1]["status"] == "done", "with room in the day the same task finishes"


# --- a connection that died after opening ------------------------------------------------------

async def test_a_dead_connection_is_noticed_reopened_and_said(tmp_path):
    clock = [NOW]
    x = watch_rig(tmp_path, clock=lambda: clock[0])
    x.backend.up, x.backend.opened = True, 0
    x.backend.alive = lambda: x.backend.up
    listen = x.backend.listen

    def counted(on_event):
        x.backend.opened += 1
        x.backend.up = True
        return listen(on_event)
    x.backend.listen = counted
    x.store.add("Priya replies about lunch", "mail")
    import asyncio
    loop = asyncio.get_running_loop()
    await x.watcher.keep_listening(loop, rounds=1)
    await x.watcher.keep_listening(loop, rounds=1)
    assert x.backend.opened == 1 and x.chat.sent == [], "a live connection is left alone"
    x.backend.up = False        # the library gave up on it; nothing would ever arrive again
    await x.watcher.keep_listening(loop, rounds=1)
    assert x.backend.opened == 2 and x.watcher.listening and x.backend.listening
    assert len(x.chat.sent) == 1 and "dropped" in x.chat.sent[0] and "was not checked" in x.chat.sent[0]
    assert [r for r in x.journal.read() if r.get("where") == "watch.stream"]
    x.backend.up = False
    await x.watcher.keep_listening(loop, rounds=1)
    assert x.backend.opened == 3 and len(x.chat.sent) == 1, "reopened every time, said once in a while"
    clock[0] = NOW + timedelta(hours=7)
    x.backend.up = False
    await x.watcher.keep_listening(loop, rounds=1)
    assert len(x.chat.sent) == 2


# --- the log ----------------------------------------------------------------------------------

def test_old_lines_move_to_a_file_per_month_and_nothing_is_lost(tmp_path):
    day = 86400.0
    now = [1_800_000_000.0]
    path = tmp_path / "journal.jsonl"
    j = Journal(path, clock=lambda: now[0])
    stamps = [now[0] - 100 * day, now[0] - 99 * day, now[0] - 50 * day, now[0] - (KEEP_DAYS + 1) * day, now[0] - 3 * day, now[0]]
    for n, ts in enumerate(stamps):
        now_was, now[0] = now[0], ts
        j.write("task_end", cost_usd=1.0, n=n)
        now[0] = now_was
    with path.open("a", encoding="utf-8") as f:
        f.write('{"ts": 17999999')        # a half-written last line, as after a crash
    again = Journal(path, clock=lambda: now[0])
    kept = [r["n"] for r in again.read()]
    assert kept == [4, 5], "what is read at start-up is the last few weeks"
    archives = sorted(tmp_path.glob("journal-*.jsonl.gz"))
    moved = [json.loads(line)["n"] for a in archives for line in gzip.open(a, "rt", encoding="utf-8")]
    assert sorted(moved) == [0, 1, 2, 3] and len(archives) >= 2, "one file per month, every old line in one of them"
    assert path.read_text(encoding="utf-8").endswith('{"ts": 17999999'), "a half-written line stays where it was"
    assert Journal(path, clock=lambda: now[0]).archive() == 0 and Ledger(again, today=None).spent_today() >= 0
    untouched = Journal(tmp_path / "b.jsonl", clock=lambda: now[0], keep_days=0)
    now_was, now[0] = now[0], now[0] - 400 * day
    untouched.write("old")
    now[0] = now_was
    assert len(Journal(tmp_path / "b.jsonl", clock=lambda: now[0], keep_days=0).read()) == 1


# --- the same private fact in other words --------------------------------------------------------

MAIL = "From Tara Menon: the board has agreed to sell Corvid Freight to Polar Haul for forty million, announcement on Friday."


def test_nothing_is_counted_until_something_private_has_been_read():
    t = Taint()
    long_query = " ".join(f"word{i}" for i in range(NEW_WORDS_A_SEARCH + 20))
    assert t.spill(long_query) == "" and t.spill("account 9988776655") == ""
    t.add(MAIL)
    assert "words that are not yours" in t.spill(long_query)


def test_a_search_is_measured_by_the_words_that_are_not_the_owners():
    t = Taint()
    t.add(MAIL)
    t.owner_said("What is new with Corvid Freight and who are its competitors?")
    assert t.new_words("Corvid Freight competitors latest news 2026") == [], "the owner's words, ordinary search words and a year"
    assert t.new_words("cold chain logistics India") == ["cold", "chain", "logistics", "india"]
    assert t.new_words("Корвид продан") == ["корвид", "продан"], "another script is counted like any other"
    assert t.leaked("Corvid being bought by Polar Haul, deal worth 40m") == [], "the old check sees nothing in a paraphrase"
    assert t.spill("cold chain logistics India") == "", "an ordinary search passes"
    assert "8844221100" in t.spill("reference 8844221100"), "a long number the owner did not type"
    assert "did not type" in t.spill("x" * 30) and t.spill("revenue 2025 12000") != ""
    t.owner_said("my booking is 8844221100")
    assert t.spill("reference 8844221100") == "", "typed by the owner, it is theirs to send"


def test_an_hours_allowance_bounds_what_can_leave_in_other_words():
    t = Taint()
    now = [0.0]
    t.clock = lambda: now[0]
    t.add(MAIL)
    sent, n = 0, 0
    while True:          # a misled model paraphrasing the mailbox, twenty new words at a time
        text = " ".join(f"p{n}w{i}" for i in range(20))
        if t.spill(text):
            break
        t.spent(text)
        sent, n = sent + 20, n + 1
    assert sent == NEW_WORDS_AN_HOUR - NEW_WORDS_AN_HOUR % 20 and "in the last hour" in t.spill(text)
    now[0] = 3601.0
    assert t.spill(text) == "", "the allowance is by the hour"


def test_the_search_rule_asks_when_too_much_would_leave_and_says_why(tmp_path):
    j = Journal(tmp_path / "j.jsonl")
    gate = make_gate(j, taint_file=tmp_path / "taint.bin")
    rule = gate._rules[SEARCH_TOOL] if hasattr(gate, "_rules") else None
    assert rule is not None
    plain = {"query": "cold chain logistics India", "objective": "which companies lead this market"}
    wordy = {"query": " ".join(f"w{i}" for i in range(NEW_WORDS_A_SEARCH + 1)), "objective": "o"}
    assert rule.decide(plain) == "allow" and rule.decide(wordy) == "allow", "nothing private read yet"
    gate.taint.add(MAIL)
    assert rule.decide(plain) == "allow" and rule.decide(wordy) == "ask"
    q = describe_search(wordy, gate.taint)
    assert "words that are not yours" in q and "Query: w0 w1" in q and "Details recognised" not in q
    known = describe_search({"query": "tara menon the board has agreed to sell corvid freight", "objective": ""}, gate.taint)
    assert "Details recognised" in known
