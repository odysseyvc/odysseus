"""Two tasks at once: the owner's conversation, and one job in the background. Neither may weaken the other's safety."""
from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

from odysseus.agent import AgentRunner
from odysseus.apps import MAIL_REPLY
from odysseus.config import Settings
from odysseus.conversation import Conversation
from odysseus.gate import ASK
from odysseus.journal import Journal, Ledger
from odysseus.memory import MemoryStore
from odysseus.tools import make_gate
from test_slice import OWNER, FakeChat, FakeClient, result

DECK = "FILE: Corvid deck.pdf (2 pages)\nnote\n\n[page 1]\nCorvid Freight\nEND OF FILE\n\n(The owner sent this file with no message.)"


class Held(FakeClient):
    """A model whose answer arrives only when the test lets it: the job that is still going on."""

    def __init__(self, text, options=None):
        super().__init__([[result(text=text)]], options=options)
        self.go = asyncio.Event()

    async def receive_response(self):
        await asyncio.wait({asyncio.ensure_future(self.go.wait()), asyncio.ensure_future(self.interrupted.wait())}, return_when=asyncio.FIRST_COMPLETED)
        if self.interrupted.is_set():
            yield self._cut()
            return
        async for m in super().receive_response():
            yield m


def rig(tmp_path, with_gate=True, worker=True):
    s = Settings(telegram_token="t", owner_id=OWNER, anthropic_api_key="k", exa_api_key="e", data_dir=tmp_path, stop_grace_s=0.3)
    journal = Journal(tmp_path / "j.jsonl")
    x = SimpleNamespace(journal=journal, chat=FakeChat(), main=[], back=[])

    def main_factory(options=None):
        x.main.append(FakeClient([[result(text=f"Reply {len(x.main) + 1}.")] for _ in range(6)], options=options))
        return x.main[-1]

    def back_factory(options=None):
        x.back.append(Held("Take: 14 fleets pay.\n\nMy view: take a first call.", options=options))
        return x.back[-1]
    gate = make_gate(journal, lambda: datetime(2026, 11, 20, 10, 0), with_apps=True) if with_gate else None
    x.gate = gate
    x.memory = MemoryStore(tmp_path / "memory")
    runner = AgentRunner(s, journal, object(), client_factory=main_factory, gate=gate, persist=True)
    x.worker = AgentRunner(s, journal, object(), client_factory=back_factory, gate=gate) if worker else None
    x.conv = Conversation(s, journal, Ledger(journal), runner, x.memory, gate=gate, worker=x.worker)
    return x


async def started(x):
    for _ in range(200):
        if x.back and x.back[0].prompts:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the job in the background never began")


async def test_a_deck_sent_by_itself_is_dealt_with_in_the_background_while_the_conversation_goes_on(tmp_path):
    x = rig(tmp_path)
    await x.conv.handle(OWNER, DECK, x.chat, material="a file you sent", background=True)
    await started(x)
    assert not x.conv.busy, "the conversation is free"
    await x.conv.handle(OWNER, "What time is my call?", x.chat)
    for _ in range(200):
        if any(t.startswith("Reply 1.") for t in x.chat.sent):
            break
        await asyncio.sleep(0.01)
    assert any(t.startswith("Reply 1.") for t in x.chat.sent), "answered while the deck is still being worked on"
    assert "Corvid Freight" not in str(x.main[0].prompts), "the conversation's model was not shown the deck"
    x.back[0].go.set()
    await x.conv.wait()
    assert any(t.startswith("Take: 14 fleets pay.") for t in x.chat.sent)
    kinds = [(r["kind"], bool(r.get("background"))) for r in x.journal.read() if r["kind"] in ("task_start", "task_end")]
    assert kinds == [("task_start", True), ("task_start", False), ("task_end", False), ("task_end", True)]
    await x.conv.handle(OWNER, "Tell me more about that deck.", x.chat)
    await x.conv.wait()
    assert "(A job done in the background.)" in x.main[0].prompts[-1] and "Take: 14 fleets pay." in x.main[0].prompts[-1], "the conversation is told what was done"


async def test_a_file_with_a_message_and_a_run_without_a_second_agent_stay_in_the_conversation(tmp_path):
    x = rig(tmp_path)
    await x.conv.handle(OWNER, DECK.replace("(The owner sent this file with no message.)", "What do you make of this?"), x.chat, material="a file you sent")
    await x.conv.wait()
    assert x.back == [] and len(x.main) == 1, "the owner asked something about it: that belongs to the conversation"
    y = rig(tmp_path / "single", worker=False)
    await y.conv.handle(OWNER, DECK, y.chat, material="a file you sent", background=True)
    await y.conv.wait()
    assert len(y.main) == 1 and "Corvid Freight" in str(y.main[0].prompts[0]), "with one agent, it is done in turn as before"


async def test_what_one_task_has_read_stays_on_the_record_when_another_begins(tmp_path):
    x = rig(tmp_path)
    await x.conv.handle(OWNER, DECK, x.chat, material="a file you sent", background=True)
    await started(x)
    assert x.gate.taint.sources == {"a file you sent"}
    remember = "mcp__odysseus__remember"
    assert x.gate.decide(remember, {"text": "Prefers Corvid Freight"}) == ASK
    await x.conv.handle(OWNER, "Remember that I like tea.", x.chat)
    await asyncio.sleep(0.1)
    assert "a file you sent" in x.gate.taint.sources, "a new message did not wipe what the other task has read"
    assert x.gate.decide(remember, {"text": "Likes tea"}) == ASK, "so a save is still put to the owner while a deck is being read elsewhere"
    x.back[0].go.set()
    await x.conv.wait()
    await x.conv.handle(OWNER, "Remember that I like coffee.", x.chat)
    await x.conv.wait()
    assert "a file you sent" in x.gate.taint.sources, "a new message in the same conversation keeps the record: the model still has what was read in front of it"
    await x.conv.handle(OWNER, "/new", x.chat)
    await x.conv.wait()
    await x.conv.handle(OWNER, "Remember that I like cocoa.", x.chat)
    await x.conv.wait()
    assert "a file you sent" not in x.gate.taint.sources, "a new conversation starts with a clean record of what was read"


async def test_changes_waiting_to_be_reported_are_not_thrown_away_by_the_other_task_starting(tmp_path):
    x = rig(tmp_path)
    await x.conv.handle(OWNER, DECK, x.chat, material="a file you sent", background=True)
    await started(x)
    x.memory.remember("Invests at pre-seed")           # as if the job in the background had saved it, and it is waiting to be reported
    await x.conv.handle(OWNER, "Hello.", x.chat)
    x.back[0].go.set()
    await x.conv.wait()
    assert sum("Memory:" in t and "Invests at pre-seed" in t for t in x.chat.sent) == 1, "reported once, by whichever reply came first, and not lost"


async def test_the_owner_goes_on_being_asked_until_the_last_task_has_ended(tmp_path):
    x = rig(tmp_path)
    await x.conv.handle(OWNER, DECK, x.chat, material="a file you sent", background=True)
    await started(x)
    reply = {"thread_id": "t-1", "to": "tara@corvidfreight.example", "body": "Thanks."}
    asking = asyncio.create_task(x.gate.permission(MAIL_REPLY, reply))       # the job in the background wants to send something
    for _ in range(100):
        if x.chat.asks:
            break
        await asyncio.sleep(0.01)
    await x.conv.handle(OWNER, "Hello.", x.chat)
    await asyncio.sleep(0.3)
    assert not x.conv.busy and x.gate.pending(), "the conversation's task ended, and the other task's question is still open"
    assert x.gate.overlapping is False and x.gate._asker is not None
    x.gate.resolve(x.chat.asks[0][1], False)
    await asking
    x.back[0].go.set()
    await x.conv.wait()
    assert x.gate._asker is None and x.gate.pending() == [], "when the last task ends, nobody is being asked"


async def test_counts_and_widened_approvals_take_the_stricter_reading_while_two_tasks_run(tmp_path):
    gate = make_gate(Journal(tmp_path / "j.jsonl"), with_apps=True)

    async def asker(approval):
        pass
    gate.begin_task("main", asker, "The owner said: one")
    gate._unasked = 2
    gate.begin_task("background", asker, "The owner said: two")
    assert gate._unasked == 2 and gate.overlapping, "a second task does not start a fresh count of unasked changes"
    assert "The owner said: one" in gate.request and "The owner said: two" in gate.request
    gate._standing["t-1"] = {"people": frozenset({"tara@corvidfreight.example"}), "until": datetime(2099, 1, 1), "sent": 0}

    async def clean(*a):
        return True
    gate._clean = clean
    reply = {"thread_id": "t-1", "to": "tara@corvidfreight.example", "body": "Thanks."}
    assert await gate._standing_covers(MAIL_REPLY, reply) is False, "no reply goes unasked while two tasks are running"
    gate.end_task("background")
    assert gate._asker is asker and gate.request == "The owner said: one"
    assert await gate._standing_covers(MAIL_REPLY, reply) is True
    gate.end_task("main")
    assert gate._asker is None


async def test_stop_ends_the_job_in_the_background_and_drops_what_waited_behind_it(tmp_path):
    x = rig(tmp_path)
    await x.conv.handle(OWNER, DECK, x.chat, material="a file you sent", background=True)
    await started(x)
    await x.conv.handle(OWNER, DECK.replace("Corvid", "Orbit"), x.chat, material="a file you sent", background=True)
    assert x.chat.sent[-1] == "Got it. I'll start on this when the one I'm working on is done."
    await x.conv.handle(OWNER, "/status", x.chat)
    assert x.chat.sent[-1].startswith("Idle, with a job in the background and 1 waiting behind it.")
    await x.conv.handle(OWNER, "stop", x.chat)
    assert x.chat.sent[-1] == "Stopped the job I was doing in the background."
    await x.conv.wait()
    assert not x.conv._bg_waiting and len(x.back[0].prompts) == 1, "the second deck was never started"
    ends = [r for r in x.journal.read() if r["kind"] == "task_end"]
    assert ends and ends[-1]["status"] == "stopped" and ends[-1].get("background") is True


def test_after_a_restart_a_job_cut_off_in_the_background_is_named(tmp_path):
    from odysseus.__main__ import restart_notice, unfinished
    journal = Journal(tmp_path / "j.jsonl")
    journal.write("start")
    journal.write("task_start", preview="FILE: Corvid deck.pdf", background=True)
    journal.write("task_start", preview="What time is my call?")
    journal.write("task_end", status="done", cost_usd=0.01)
    lost = unfinished(journal)
    assert lost["preview"] is None and lost["background"] == "FILE: Corvid deck.pdf", "the conversation's task ended; the other did not"
    assert 'A job I was doing in the background was also cut off: "FILE: Corvid deck.pdf".' in restart_notice(lost, True)
    journal.write("task_end", status="done", cost_usd=0.03, background=True)
    assert unfinished(journal) is None


async def test_with_more_agents_more_jobs_run_at_once_and_the_one_after_that_waits(tmp_path):
    x = rig(tmp_path)
    s = x.conv._s
    second = AgentRunner(s, x.journal, object(), client_factory=lambda options=None: x.back.append(Held("Second take.", options=options)) or x.back[-1], gate=x.gate)
    x.conv._workers.append(second)
    decks = [DECK, DECK.replace("Corvid", "Orbit"), DECK.replace("Corvid", "Tidal")]
    for d in decks:
        await x.conv.handle(OWNER, d, x.chat, material="a file you sent", background=True)
    for _ in range(200):
        if len(x.back) == 2 and all(b.prompts for b in x.back):
            break
        await asyncio.sleep(0.01)
    assert len(x.conv._jobs()) == 2 and len(x.conv._bg_waiting) == 1, "two at once, and the third waits its turn"
    assert x.chat.sent[-1] == "Got it. I'm working through what you've sent in turn, and will report on each as it is done."
    assert sorted(x.gate._tasks) == ["background-0", "background-1"] and x.gate.overlapping
    await x.conv.handle(OWNER, "/status", x.chat)
    assert x.chat.sent[-1].startswith("Idle, with 2 jobs in the background and 1 waiting behind them.")
    for b in x.back:
        b.go.set()
    for _ in range(300):
        if sum(len(b.prompts) for b in x.back) == 3:
            break
        await asyncio.sleep(0.01)
    for b in x.back:
        b.go.set()
    await x.conv.wait()
    assert sum(len(b.prompts) for b in x.back) == 3 and not x.conv._jobs() and x.gate._tasks == {}


def test_how_many_jobs_may_run_at_once_is_a_setting_with_a_ceiling():
    from odysseus.config import _how_many
    assert _how_many("") == 2 and _how_many("0") == 0 and _how_many("off") == 0 and _how_many("4") == 4
    assert _how_many("40") == 6 and _how_many("many") == 2 and _how_many("-3") == 0
