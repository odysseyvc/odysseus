"""The day's budget while several things run at once (journal.Ledger.in_flight, agent.AgentRunner.within).

A finished task has a costed record in the log. A running one has none, so two tasks started close together each saw
the day as untouched by the other, and an unattended run (a routine, a brief, a draft for a colleague) was checked
before it started and never while it worked. Nothing here calls a model or a service.
"""
from __future__ import annotations

from types import SimpleNamespace

from odysseus.agent import AgentRunner
from odysseus.journal import Journal, Ledger

from test_slice import call, ends, make, result, say


def test_what_running_tasks_have_spent_so_far_counts_towards_the_day(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    ledger = Ledger(journal)
    journal.write("tool", name="x", cost_usd=0.25)
    assert ledger.spent_today() == 0.25
    running = [0.40]
    ledger.in_flight.append(lambda: running[0])
    ledger.in_flight.append(lambda: 1 / 0)             # an estimate that cannot be read is left out, not fatal
    assert round(ledger.spent_today(), 2) == 0.65 and ledger.recorded_today() == 0.25
    running[0] = 0.0                                    # the task finished: its cost is now a record, counted once
    journal.write("tool", name="x", cost_usd=0.40)
    assert round(ledger.spent_today(), 2) == 0.65


def test_each_agent_sees_what_the_others_are_spending_and_not_its_own_twice(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    ledger = Ledger(journal)
    journal.write("tool", name="x", cost_usd=0.20)
    a, b = SimpleNamespace(in_flight=0.0, day_room=None), SimpleNamespace(in_flight=0.0, day_room=None)
    for agent in (a, b):
        AgentRunner.within(agent, ledger, 1.00)
    assert round(a.day_room(), 2) == 0.80 and round(b.day_room(), 2) == 0.80
    a.in_flight = 0.50
    assert round(a.day_room(), 2) == 0.80, "its own spending is compared with the room, not taken out of it as well"
    assert round(b.day_room(), 2) == 0.30, "the other's room shrinks as this one spends"
    assert round(ledger.spent_today(), 2) == 0.70, "and a check made before starting anything sees it too"


async def test_a_task_is_ended_when_something_else_running_uses_up_the_day(tmp_path):
    big = {"input_tokens": 100_000, "output_tokens": 10_000}
    script = [call(1, "Partial answer", big), call(2, "Never reached", big), result(cost=0.6)]
    x = make(tmp_path, [script], daily=1.00, task_usd=5.00)
    x.ledger.in_flight.append(lambda: 0.80)             # a routine running beside it has spent 80 cents and recorded nothing yet
    await say(x, "expensive")
    assert ends(x.journal)[-1]["status"] == "day_budget" and ends(x.journal)[-1]["turns"] == 1
    y = make(tmp_path / "y", [script], daily=1.00, task_usd=5.00)
    y.ledger.in_flight.append(lambda: 1.00)
    await say(y, "anything")
    assert "is used up, so I have not started this" in y.chat.sent[-1] and not y.clients, "nothing is started into a day already spent"


# --- a task handed to the browser is counted, as a search is (browser.add_rules, taint.spill) --------------------------

def test_a_browser_task_carrying_much_that_the_owner_did_not_type_is_shown_first(tmp_path):
    from odysseus.browser import BROWSE_TOOL, TASK_NEW_WORDS
    from odysseus.tools import make_gate
    gate = make_gate(Journal(tmp_path / "j.jsonl"), with_browser=True, taint_file=tmp_path / "t.bin")
    plain = {"task": "Open the pricing page of the vendor and bring back the monthly plans with their limits."}
    long_one = {"task": "Open the site and fill the form with " + " ".join(f"detail{chr(97 + i % 26)}{chr(97 + i // 26)}" for i in range(TASK_NEW_WORDS + 5))}
    number = {"task": "Open the bank site and look up reference 4188392077 there."}
    assert [gate.decide(BROWSE_TOOL, a) for a in (plain, long_one, number)] == ["allow"] * 3, "nothing has been read yet, so there is nothing to carry out"
    gate.taint.add("A message from the bank about the account of Meenakshi Subramanian, and other things besides.")
    assert gate.decide(BROWSE_TOOL, plain) == "allow", "an ordinary task still starts unasked"
    assert gate.decide(BROWSE_TOOL, long_one) == "ask" and gate.decide(BROWSE_TOOL, number) == "ask"
    asked = gate._rules[BROWSE_TOOL].describe(number)
    assert "4188392077, which you did not type" in asked and number["task"] in asked
    gate.taint.owner_said("look up reference 4188392077 at the bank")
    assert gate.decide(BROWSE_TOOL, number) == "allow", "what the owner typed is theirs to send"
