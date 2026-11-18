"""Voice notes: heard, shown back, and then handled like any typed message."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from odysseus.agent import Outcome
from odysseus.config import Settings
from odysseus.conversation import Conversation
from odysseus.journal import Journal, Ledger
from odysseus.memory import MemoryStore
from odysseus.voice import MAX_SECONDS, VOICE_NOTE, vocabulary_from

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
    session_id = None

    def __init__(self):
        self.prompts = []

    async def run(self, prompt, on_text, preamble=None):
        self.prompts.append(prompt)
        return Outcome("done", text="Reminder set.")

    async def stop(self):
        return False

    async def reset(self):
        pass


class Ears:
    def __init__(self, heard="Remind me tomorrow at nine to call Priya.", fail=False):
        self.calls, self._heard, self._fail = [], heard, fail

    def hear(self, data, filename, vocabulary):
        self.calls.append((len(data), filename, vocabulary))
        if self._fail:
            raise RuntimeError("service down")
        return self._heard


def rig(tmp_path, ears=None, with_ears=True):
    journal = Journal(tmp_path / "journal.jsonl")
    memory = MemoryStore(tmp_path / "memory")
    memory.remember("Their partner is called Noor and their landlord is Ravi Shah.")
    runner, ears = Runner(), ears or Ears()
    conv = Conversation(replace(SETTINGS, data_dir=tmp_path), journal, Ledger(journal), runner, memory, voice=ears if with_ears else None)
    return SimpleNamespace(conv=conv, runner=runner, ears=ears, chat=Chat(), journal=journal)


async def test_a_voice_note_is_heard_shown_back_and_then_handled_as_a_message(tmp_path):
    x = rig(tmp_path)
    await x.conv.handle_voice(OWNER, b"OggS recording", "voice.ogg", 14, 4, x.chat)
    await x.conv.wait()
    assert x.chat.sent[0] == 'Heard: "Remind me tomorrow at nine to call Priya."', "the owner sees what was heard before anything is done"
    prompt = x.runner.prompts[0]
    assert VOICE_NOTE in prompt and "may contain mistakes" in prompt and "Remind me tomorrow at nine to call Priya." in prompt
    assert x.chat.sent[-1] == "Reminder set."
    rec = [r for r in x.journal.read() if r["kind"] == "voice"][0]
    assert rec["ok"] is True and rec["seconds"] == 4 and rec["chars"] == 41 and "Priya" not in str(rec), "the log holds lengths, not words"


async def test_names_from_memory_are_given_to_the_service_to_listen_for(tmp_path):
    x = rig(tmp_path)
    await x.conv.handle_voice(OWNER, b"OggS", "voice.ogg", 4, 2, x.chat)
    await x.conv.wait()
    assert {"Noor", "Ravi", "Shah"} <= set(x.ears.calls[0][2])
    assert vocabulary_from("They live in Pune. Their dentist is Dr Mehta.") == ["Pune", "Mehta"]


async def test_a_strangers_recording_is_never_listened_to(tmp_path):
    x = rig(tmp_path)
    await x.conv.handle_voice(999, b"OggS", "voice.ogg", 4, 2, x.chat)
    assert x.ears.calls == [] and x.chat.sent == [] and x.runner.prompts == []


async def test_too_long_failed_silent_and_unconfigured_are_each_explained_and_no_model_is_called(tmp_path):
    x = rig(tmp_path)
    await x.conv.handle_voice(OWNER, b"OggS", "voice.ogg", 4, MAX_SECONDS + 60, x.chat)
    assert "up to 10 minutes" in x.chat.sent[-1] and x.ears.calls == []
    await x.conv.handle_voice(OWNER, None, "voice.ogg", 50_000_000, 30, x.chat)
    assert "too big" in x.chat.sent[-1]
    broken = rig(tmp_path / "b", ears=Ears(fail=True))
    await broken.conv.handle_voice(OWNER, b"OggS", "voice.ogg", 4, 2, broken.chat)
    assert "could not make out" in broken.chat.sent[-1] and broken.runner.prompts == []
    silent = rig(tmp_path / "s", ears=Ears(heard=""))
    await silent.conv.handle_voice(OWNER, b"OggS", "voice.ogg", 4, 2, silent.chat)
    assert "heard no words" in silent.chat.sent[-1] and silent.runner.prompts == []
    none = rig(tmp_path / "n", with_ears=False)
    await none.conv.handle_voice(OWNER, b"OggS", "voice.ogg", 4, 2, none.chat)
    assert "can't listen to voice notes" in none.chat.sent[-1]
