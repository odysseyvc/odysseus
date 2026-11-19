"""A new conversation after a pause, so that one message does not pay for a week of them."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from odysseus.agent import AgentRunner
from odysseus.config import Settings
from odysseus.conversation import Conversation
from odysseus.journal import Journal, Ledger
from odysseus.memory import MemoryStore
from odysseus.search import MAX_RESULT_CHARS, make_search_handler
from test_slice import OWNER, FakeChat, FakeClient, result

T0 = datetime(2026, 11, 20, 9, 0)


def rig(tmp_path, **settings):
    s = Settings(telegram_token="t", owner_id=OWNER, anthropic_api_key="k", exa_api_key="e", data_dir=tmp_path, **settings)
    journal, clients, clock = Journal(tmp_path / "j.jsonl"), [], SimpleNamespace(t=T0)

    def factory(options=None):
        clients.append(FakeClient([[result(text=f"Reply {len(clients) + 1}.")] for _ in range(6)], options=options))
        return clients[-1]
    runner = AgentRunner(s, journal, object(), client_factory=factory, persist=True)
    memory = MemoryStore(tmp_path / "memory")
    memory.remember("Prefers morning meetings")
    conv = Conversation(s, journal, Ledger(journal), runner, memory, now=lambda: clock.t)
    return SimpleNamespace(conv=conv, runner=runner, clients=clients, clock=clock, chat=FakeChat(), journal=journal)


async def say(x, text):
    await x.conv.handle(OWNER, text, x.chat)
    await x.conv.wait()


async def test_after_a_long_pause_the_next_message_begins_a_new_conversation_with_the_last_exchanges_and_memory(tmp_path):
    x = rig(tmp_path)
    await say(x, "Who is Tara Menon?")
    x.clock.t += timedelta(minutes=30)
    await say(x, "And her co-founder?")
    assert len(x.clients) == 1 and "EARLIER IN THIS CONVERSATION" not in x.clients[0].prompts[1], "half an hour is the same conversation"
    x.clock.t += timedelta(hours=3)
    await say(x, "Draft a note to her.")
    assert len(x.clients) == 2 and x.clients[1].options.resume is None, "a new conversation, not the old one picked up"
    prompt = x.clients[1].prompts[0]
    assert "EARLIER IN THIS CONVERSATION" in prompt and "The owner said: And her co-founder?" in prompt and "You replied: Reply" in prompt
    assert "MEMORY" in prompt and "Prefers morning meetings" in prompt, "what was saved is shown again"
    assert prompt.index("EARLIER IN THIS CONVERSATION") < prompt.index("Draft a note to her.")
    assert [r["reason"] for r in x.journal.read() if r["kind"] == "fresh"] == ["pause"]
    x.clock.t += timedelta(minutes=5)
    await say(x, "Shorter.")
    assert len(x.clients) == 2 and "EARLIER IN THIS CONVERSATION" not in x.clients[1].prompts[1], "the recap is said once"


async def test_a_large_conversation_starts_afresh_once_the_discount_has_lapsed_and_a_small_one_does_not(tmp_path):
    x = rig(tmp_path)
    await say(x, "Summarise the deck.")
    x.runner.context_tokens = 55_000
    x.clock.t += timedelta(minutes=4)
    await say(x, "Go on.")
    assert len(x.clients) == 1, "within a few minutes the large conversation is still cheap to continue"
    x.runner.context_tokens = 55_000
    x.clock.t += timedelta(minutes=15)
    await say(x, "File it.")
    assert len(x.clients) == 2 and [r["reason"] for r in x.journal.read() if r["kind"] == "fresh"] == ["size"]
    small = rig(tmp_path / "small")
    await say(small, "Hello.")
    small.runner.context_tokens = 18_000
    small.clock.t += timedelta(minutes=15)
    await say(small, "Still there?")
    assert len(small.clients) == 1


async def test_the_recap_holds_the_owners_words_and_not_a_files_text_and_can_be_switched_off(tmp_path):
    x = rig(tmp_path)
    await say(x, "FILE: deck.pdf (11 pages)\nnote\n\n" + "secret page text " * 400 + "\nEND OF FILE\n\nWhat do you make of this?")
    x.clock.t += timedelta(hours=5)
    await say(x, "Remind me what you said.")
    prompt = x.clients[1].prompts[0]
    assert "The owner said: [FILE: deck.pdf (11 pages)] What do you make of this?" in prompt and "secret page text" not in prompt
    off = rig(tmp_path / "off", fresh_after_min=0)
    await say(off, "Hello.")
    off.clock.t += timedelta(days=3)
    off.runner.context_tokens = 90_000
    await say(off, "Hello again.")
    assert len(off.clients) == 1


async def test_a_searchs_results_are_cut_to_a_size_worth_carrying(tmp_path):
    class Spec:
        description, input_schema = "d", {}

        def execute(self, args):
            return SimpleNamespace(results=[1, 2, 3])

        def format(self, response):
            return "x" * (MAX_RESULT_CHARS + 9000)
    out = await make_search_handler(Spec(), Journal(tmp_path / "j.jsonl"))({"query": "q", "objective": "o"})
    text = out["content"][0]["text"]
    assert text.count("x") == MAX_RESULT_CHARS and f"[Cut: {MAX_RESULT_CHARS + 9000} characters came back." in text


async def test_after_a_restart_the_pause_is_measured_from_the_last_task_the_journal_holds(tmp_path):
    import json
    import time

    def started_after(folder, hours_ago):       # the program starting with a journal whose last task ended that long ago
        folder.mkdir()
        (folder / "j.jsonl").write_text(json.dumps({"ts": time.time() - hours_ago * 3600, "kind": "task_end", "status": "done", "cost_usd": 0.01}) + chr(10),
                                        encoding="utf-8")
        x = rig(folder)
        x.runner.session_id = "saved-before-the-restart"
        return x
    assert started_after(tmp_path / "long", 3).conv._fresh_reason() == "pause"
    assert started_after(tmp_path / "short", 0.1).conv._fresh_reason() == "", "a restart soon after the last task carries on with the same conversation"
