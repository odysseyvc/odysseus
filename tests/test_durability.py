"""Messages that arrive during a task wait their turn; the conversation survives a restart; an interrupted task is announced."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from odysseus.__main__ import restart_notice, unfinished
from odysseus.agent import AgentRunner, Outcome
from odysseus.config import Settings
from odysseus.conversation import MAX_WAITING, Conversation
from odysseus.journal import Journal, Ledger

OWNER = 7
SETTINGS = Settings(telegram_token="t", owner_id=OWNER, anthropic_api_key="k", exa_api_key="e")


class Chat:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)

    async def draft(self, draft_id, text):
        pass

    async def ask(self, text, approval_id):
        pass


class Runner:
    """Stands in for the agent: each task waits until it is released, then answers with what it was asked."""

    def __init__(self):
        self.started, self.release, self.forgotten, self.session_id = [], asyncio.Event(), 0, None
        self._stopped = False

    async def run(self, prompt, on_text, preamble=None):
        self.started.append(prompt.split("\n")[0])
        await self.release.wait()
        if self._stopped:
            return Outcome("stopped")
        return Outcome("done", text="did: " + prompt.split("\n")[0])

    async def stop(self):
        self._stopped = True
        self.release.set()
        return True

    async def reset(self):
        pass

    async def forget_session(self):
        self.forgotten += 1


def rig(tmp_path):
    journal = Journal(tmp_path / "journal.jsonl")
    runner = Runner()
    conv = Conversation(replace(SETTINGS, data_dir=tmp_path), journal, Ledger(journal), runner)
    return SimpleNamespace(conv=conv, runner=runner, chat=Chat(), journal=journal)


# --- waiting behind a running task -----------------------------------------------------------

async def test_messages_sent_during_a_task_run_afterwards_in_the_order_they_arrived(tmp_path):
    x = rig(tmp_path)
    await x.conv.handle(OWNER, "first", x.chat)
    await asyncio.sleep(0.02)
    await x.conv.handle(OWNER, "second", x.chat)
    await x.conv.handle(OWNER, "third", x.chat)
    assert x.runner.started == ["first"] and x.chat.sent.count("Noted. I'll do this as soon as the current task is finished. Send stop to end everything.") == 2
    x.runner.release.set()
    await x.conv.wait()
    assert x.runner.started == ["first", "second", "third"]
    assert [m for m in x.chat.sent if m.startswith("did:")] == ["did: first", "did: second", "did: third"]
    assert x.conv.busy is False


async def test_only_so_many_messages_can_wait(tmp_path):
    x = rig(tmp_path)
    await x.conv.handle(OWNER, "first", x.chat)
    await asyncio.sleep(0.02)
    for i in range(MAX_WAITING + 1):
        await x.conv.handle(OWNER, f"extra {i}", x.chat)
    assert f"I already have {MAX_WAITING} messages waiting" in x.chat.sent[-1]
    x.runner.release.set()
    await x.conv.wait()
    assert len(x.runner.started) == 1 + MAX_WAITING


async def test_stop_ends_the_task_and_drops_what_was_waiting_and_says_so(tmp_path):
    x = rig(tmp_path)
    await x.conv.handle(OWNER, "first", x.chat)
    await asyncio.sleep(0.02)
    await x.conv.handle(OWNER, "second", x.chat)
    await x.conv.handle(OWNER, "stop", x.chat)
    await x.conv.wait()
    assert x.runner.started == ["first"], "the waiting message never ran"
    assert "Also dropped: 1 message that was waiting." in x.chat.sent and "Stopped." in x.chat.sent[-1]


async def test_commands_are_answered_at_once_even_while_a_task_runs(tmp_path):
    x = rig(tmp_path)
    await x.conv.handle(OWNER, "first", x.chat)
    await asyncio.sleep(0.02)
    await x.conv.handle(OWNER, "/status", x.chat)
    assert x.chat.sent[-1].startswith("Working on a task.")
    x.runner.release.set()
    await x.conv.wait()


# --- a new conversation, on request ---------------------------------------------------------------

async def test_new_starts_a_fresh_conversation_and_is_refused_in_the_middle_of_a_task(tmp_path):
    x = rig(tmp_path)
    await x.conv.handle(OWNER, "/new", x.chat)
    assert x.runner.forgotten == 1 and "Started a new conversation" in x.chat.sent[-1]
    await x.conv.handle(OWNER, "first", x.chat)
    await asyncio.sleep(0.02)
    await x.conv.handle(OWNER, "/new", x.chat)
    assert x.runner.forgotten == 1 and "Send stop first" in x.chat.sent[-1]
    x.runner.release.set()
    await x.conv.wait()


# --- surviving a restart ------------------------------------------------------------------------------

def runner_at(folder: Path, persist=True) -> AgentRunner:
    return AgentRunner(replace(SETTINGS, data_dir=folder), Journal(folder / "journal.jsonl"), None, persist=persist)


async def test_the_conversation_id_is_kept_on_disk_and_picked_up_by_the_next_run_of_the_program(tmp_path):
    first = runner_at(tmp_path)
    assert first.session_id is None and first.resumed is False
    first.session_id = "sess-123"
    first._save_session()
    second = runner_at(tmp_path)
    assert second.session_id == "sess-123" and second.resumed is True
    assert second.options().resume == "sess-123"
    assert second.options().env["CLAUDE_CONFIG_DIR"] == str((tmp_path / "agent").resolve()), "the transcript is kept beside the other data"
    await second.forget_session()
    assert runner_at(tmp_path).session_id is None, "/new is remembered too"


def test_a_runner_that_is_not_asked_to_persist_saves_no_conversation_but_still_keeps_transcripts_in_the_data_folder(tmp_path):
    r = runner_at(tmp_path, persist=False)
    r.session_id = "sess-1"
    r._save_session()
    assert not (tmp_path / "session.json").exists()
    # Routines and triage read mail too. Their transcripts must not land in a folder outside the one the owner chose.
    assert r.options().env["CLAUDE_CONFIG_DIR"] == str((tmp_path / "agent").resolve())


def test_a_damaged_session_file_means_a_new_conversation_not_a_crash(tmp_path):
    (tmp_path / "session.json").write_text("{not json", encoding="utf-8")
    assert runner_at(tmp_path).session_id is None


# --- saying what a restart interrupted -------------------------------------------------------------------

def test_a_task_with_no_end_in_the_log_was_interrupted(tmp_path):
    j = Journal(tmp_path / "journal.jsonl")
    assert unfinished(j) is None
    j.write("task_start", chars=20, preview="Email Ravi about the tap")
    j.write("task_end", status="done", cost_usd=0.01)
    assert unfinished(j) is None, "it finished"
    j.write("task_start", chars=30, preview="Move my 1:1 with Sam to 4pm")
    j.write("gate", tool="mcp__odysseus__calendar_change", verdict="asked", approval="ab12")
    task = unfinished(j)
    assert task == {"preview": "Move my 1:1 with Sam to 4pm", "asked": True, "waiting": []}
    notice = restart_notice(task, resumed=True)
    assert '"Move my 1:1 with Sam to 4pm"' in notice and "not finished" in notice and "no longer counts" in notice
    assert "no longer have our earlier conversation" not in notice
    assert "no longer have our earlier conversation" in restart_notice(task, resumed=False)


def test_messages_that_were_waiting_when_the_program_stopped_are_named_in_the_notice(tmp_path):
    j = Journal(tmp_path / "journal.jsonl")
    j.write("task_start", chars=30, preview="Summarise my inbox")
    j.write("queued", waiting=1, q=1, preview="Remind me to call Sam at 5")
    j.write("queued", waiting=2, q=2, preview="What is on tomorrow?")
    j.write("queued", waiting=3, q=3, preview="Book the dentist")
    j.write("dequeued", q=1)                    # this one was reached before the stop
    task = unfinished(j)
    assert task["waiting"] == ["What is on tomorrow?", "Book the dentist"]
    notice = restart_notice(task, resumed=True)
    assert "2 messages you sent were waiting behind it and were lost" in notice and '"Book the dentist"' in notice
    j.write("start")                            # the program came back and said so
    assert unfinished(j) is None, "what was lost is reported once, not at every later restart"
    j.write("task_start", chars=5, preview="One")
    j.write("queued", waiting=1, q=1, preview="Two")
    j.write("queue_cleared", dropped=1)         # the owner sent stop: nothing was lost unknowingly
    j.write("task_end", status="stopped", cost_usd=0.0)
    assert unfinished(j) is None


def test_a_routine_ending_does_not_hide_an_interrupted_conversation_task(tmp_path):
    j = Journal(tmp_path / "journal.jsonl")
    j.write("task_start", chars=10, preview="Summarise my inbox")
    j.write("task_end", status="done", cost_usd=0.001, routine="a1b2")
    assert unfinished(j)["preview"] == "Summarise my inbox"
