"""Stage 2 tests: memory. As in stage 1, the model is a scripted stand-in.

The stand-in here "calls" the memory tools by running their handlers directly,
so these tests cover the store, the tools, what the model is shown and what the
owner is told. They do not show that a real model chooses to save or use
anything; that is what the live check is for.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from odysseus.agent import AgentRunner, tool_policy
from odysseus.config import Settings
from odysseus.conversation import Conversation
from odysseus.journal import Journal, Ledger
from odysseus.memory import MEMORY_NOTE, MemoryError_, MemoryStore
from odysseus.tools import ALLOWED_TOOLS, MEMORY_TOOLS, make_memory_handlers, make_tool_server
from claude_agent_sdk import PermissionResultAllow

from test_slice import OWNER, FakeChat, FakeClient, FakeSpec, result

NOW = datetime(2026, 11, 17, 10, 30)


def store(tmp_path, now=lambda: NOW):
    return MemoryStore(tmp_path / "memory", now=now)


# --- The store ---------------------------------------------------------------

def test_a_saved_fact_survives_a_restart(tmp_path):
    a = store(tmp_path)
    e = a.remember("Is vegetarian")
    b = store(tmp_path)                      # a new process reading the same files
    assert [(x.id, x.text, x.date) for x in b.profile()] == [(e.id, "Is vegetarian", "2026-11-17")]
    assert "Is vegetarian" in b.profile_text()


def test_a_changed_fact_replaces_the_old_one_and_the_old_one_is_kept_in_the_history(tmp_path):
    m = store(tmp_path)
    old = m.remember("Is vegetarian")
    new = m.remember("Eats fish but no meat", replaces=old.id)
    assert [e.text for e in m.profile()] == ["Eats fish but no meat"]
    h = m.history()
    assert [r["action"] for r in h] == ["add", "replace"]
    assert h[1]["old_text"] == "Is vegetarian" and h[1]["text"] == "Eats fish but no meat" and h[1]["id"] == new.id


def test_replacing_an_unknown_id_changes_nothing(tmp_path):
    m = store(tmp_path)
    m.remember("Lives in Pune")
    with pytest.raises(MemoryError_):
        m.remember("Lives in Mumbai", replaces="zzzz")
    assert [e.text for e in m.profile()] == ["Lives in Pune"]


def test_forget_removes_and_records(tmp_path):
    m = store(tmp_path)
    e = m.remember("Prefers aisle seats")
    assert m.forget(e.id).text == "Prefers aisle seats" and m.profile() == []
    assert m.forget("nope") is None
    assert m.history()[-1]["action"] == "forget" and m.history()[-1]["old_text"] == "Prefers aisle seats"


def test_secrets_duplicates_and_overlong_entries_are_refused(tmp_path):
    m = store(tmp_path)
    for bad in ("Card is 4111 1111 1111 1111", "key sk-ant-abcdefghijklmnopqrstuvwxyz", "x" * 301, "   "):
        with pytest.raises(MemoryError_):
            m.remember(bad)
    m.remember("Is vegetarian")
    with pytest.raises(MemoryError_):
        m.remember("is vegetarian")
    assert len(m.profile()) == 1 and len(m.history()) == 1


def test_the_profile_has_a_fixed_size(tmp_path):
    m = store(tmp_path)
    for i in range(60):
        m.remember(f"Fact number {i}")
    with pytest.raises(MemoryError_):
        m.remember("One too many")


def test_notes_are_dated_appended_and_searchable(tmp_path):
    day = [datetime(2026, 11, 17, 9, 0)]
    m = store(tmp_path, now=lambda: day[0])
    m.note("Started comparing flights to Bengaluru for the December trip")
    day[0] = datetime(2026, 11, 18, 14, 5)
    m.note("Owner chose the 7am IndiGo flight")
    assert (tmp_path / "memory" / "notes" / "2026-11-17.md").read_text(encoding="utf-8").startswith("- 09:00 Started")
    hits = m.search("which flight Bengaluru")
    assert hits and any("Bengaluru" in body for _, body in hits)
    assert store(tmp_path).search("IndiGo")[0][0] == "note 2026-11-18", "the index is rebuilt from the files"
    assert m.search("!!! ()") == [] and m.search('" OR 1') is not None, "odd input is not read as search syntax"


def test_the_version_moves_only_when_the_profile_changes(tmp_path):
    m = store(tmp_path)
    v = m.version
    m.note("something happened")
    assert m.version == v
    m.remember("Is vegetarian")
    assert m.version == v + 1


# --- The tools ---------------------------------------------------------------

async def test_memory_tools_report_refusals_to_the_model_and_log_them(tmp_path):
    journal, m = Journal(tmp_path / "j.jsonl"), store(tmp_path)
    h = make_memory_handlers(m, journal)
    ok = await h["remember"]({"text": "Is vegetarian"})
    assert "Saved as [" in ok["content"][0]["text"]
    assert (await h["remember"]({"text": "Card 4111111111111111"}))["is_error"] is True
    assert (await h["forget"]({"id": "zzzz"}))["is_error"] is True
    await h["note"]({"text": "Asked about trains to Pune"})
    out = (await h["recall"]({"query": "trains"}))["content"][0]["text"]
    assert out.startswith(MEMORY_NOTE) and "trains to Pune" in out
    assert [r["ok"] for r in journal.read()] == [True, False, False, True, True]


async def test_the_gate_allows_memory_tools_and_the_server_builds(tmp_path):
    for name in MEMORY_TOOLS:
        assert name in ALLOWED_TOOLS and isinstance(await tool_policy(name, {}, None), PermissionResultAllow)
    server = make_tool_server(FakeSpec(), Journal(tmp_path / "j.jsonl"), store(tmp_path))
    assert server["name"] == "odysseus"


# --- What the model is shown, and what the owner is told -----------------------

def make(tmp_path, scripts, on_query=None):
    settings = Settings(telegram_token="t", owner_id=OWNER, anthropic_api_key="k", exa_api_key="e", data_dir=tmp_path)
    journal = Journal(tmp_path / "journal.jsonl")
    memory = store(tmp_path)
    clients = []

    class Client(FakeClient):
        async def query(self, prompt):
            await super().query(prompt)
            if on_query:
                await on_query(prompt, memory)

    def factory(options=None):
        clients.append(Client(scripts, options=options))
        return clients[-1]

    runner = AgentRunner(settings, journal, tool_server=object(), client_factory=factory)
    conv = Conversation(settings, journal, Ledger(journal), runner, memory, now=lambda: NOW)
    return SimpleNamespace(conv=conv, memory=memory, clients=clients, chat=FakeChat(), runner=runner, journal=journal)


async def say(x, text):
    await x.conv.handle(OWNER, text, x.chat)
    await x.conv.wait()


async def test_memory_is_shown_at_the_start_and_again_only_when_it_changes(tmp_path):
    x = make(tmp_path, [[result()]])
    x.memory.remember("Is vegetarian")
    await say(x, "one")
    await say(x, "two")
    x.memory.remember("Prefers aisle seats")
    x.memory.pop_changes()
    await say(x, "three")
    p = x.clients[0].prompts
    assert p[0].startswith("MEMORY\n" + MEMORY_NOTE) and "Is vegetarian" in p[0] and "one" in p[0]
    assert not p[1].startswith("MEMORY"), "unchanged memory is not repeated"
    assert p[2].startswith("MEMORY") and "Prefers aisle seats" in p[2]
    assert all(q.endswith("[Tuesday 17 November 2026, 10:30]") for q in p), "every message carries the time it was sent"


async def test_a_new_conversation_after_a_restart_is_shown_the_memory(tmp_path):
    x = make(tmp_path, [[result()]])
    x.memory.remember("Is vegetarian")
    await say(x, "one")
    y = make(tmp_path, [[result()]])           # the program restarted: new runner, same files
    await say(y, "suggest a dinner")
    assert y.clients[0].prompts[0].startswith("MEMORY") and "Is vegetarian" in y.clients[0].prompts[0]


async def test_memory_is_shown_again_after_the_conversation_is_condensed(tmp_path):
    x = make(tmp_path, [[result()]])
    x.memory.remember("Is vegetarian")
    await say(x, "one")
    await x.runner._on_compact({}, None, None)   # the SDK's signal that it is about to condense
    await say(x, "two")
    assert x.clients[0].prompts[1].startswith("MEMORY") and "compact" in [r["kind"] for r in x.journal.read()]


async def test_the_owner_is_told_of_every_change_the_model_makes(tmp_path):
    async def model_saves(prompt, memory):
        if "vegetarian" in prompt:
            memory.remember("Is vegetarian")
            memory.note("Owner mentioned their diet while planning dinner")

    x = make(tmp_path, [[result(text="Noted.")]], on_query=model_saves)
    await say(x, "I'm vegetarian, by the way")
    assert x.chat.sent[-1].startswith("Noted.\n\nMemory: saved \"Is vegetarian\"; noted \"Owner mentioned")
    await say(x, "thanks")
    assert "Memory:" not in x.chat.sent[-1]


async def test_the_owner_can_read_and_prune_memory_without_the_model(tmp_path):
    x = make(tmp_path, [[result()]])
    e = x.memory.remember("Is vegetarian")
    await x.conv.handle(OWNER, "/memory", x.chat)
    assert f"[{e.id}] Is vegetarian" in x.chat.sent[-1]
    await x.conv.handle(OWNER, f"/forget {e.id}", x.chat)
    assert x.chat.sent[-1] == 'Forgotten: "Is vegetarian"' and x.memory.profile() == []
    await x.conv.handle(OWNER, "/forget", x.chat)
    assert "followed by an id" in x.chat.sent[-1]
    assert x.clients == [], "none of this reached the model"
    assert x.memory.history()[-1]["by"] == "owner"


async def test_a_stranger_cannot_read_memory(tmp_path):
    x = make(tmp_path, [[result()]])
    x.memory.remember("Is vegetarian")
    await x.conv.handle(999, "/memory", x.chat)
    assert x.chat.sent == []
