"""Live check of voice notes, end to end, without Telegram.

A sentence is spoken by a speech-synthesis service into an OGG/Opus recording, which is
the format Telegram voice notes arrive in. The recording is then handled exactly as a
voice note from the owner would be: the real speech-to-text service hears it, the owner
is shown what was heard, and the real model acts on it. Costs about two cents.

What this does not show: how well a real voice is heard on a real phone in a real room.
A synthesized voice is clear, steady and accent-free.

    .venv/Scripts/python scripts/live_voice.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
import warnings
from dataclasses import replace
from pathlib import Path

warnings.simplefilter("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exa_py import Exa  # noqa: E402
from mistralai.client import Mistral  # noqa: E402

from evals.world import FakeSearch  # noqa: E402
from odysseus.agent import AgentRunner, conversation_prompt  # noqa: E402
from odysseus.config import load  # noqa: E402
from odysseus.conversation import Conversation  # noqa: E402
from odysseus.journal import Journal, Ledger  # noqa: E402
from odysseus.memory import MemoryStore  # noqa: E402
from odysseus.schedule import ScheduleStore  # noqa: E402
from odysseus.tools import make_gate, make_tool_server  # noqa: E402
from odysseus.voice import MistralVoice  # noqa: E402

SPOKEN = "Remind me tomorrow at nine in the morning to call Priya Nair about the Saffron Table booking."


class Chat:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)

    async def draft(self, draft_id, text):
        pass

    async def ask(self, text, approval_id):
        pass


async def main() -> int:
    settings = replace(load(), data_dir=Path(f"var/live/voice-{int(time.time())}"))
    speech = Mistral(api_key=settings.mistral_api_key).audio.speech.complete(input=SPOKEN, model="voxtral-mini-tts-3", response_format="opus",
                                                                           voice_id="01a14675-dc30-778a-8d01-b5dead5a522a")
    recording = base64.b64decode(speech.audio_data)
    journal = Journal(settings.data_dir / "journal.jsonl")
    memory = MemoryStore(settings.data_dir / "memory")
    schedule = ScheduleStore(settings.data_dir / "schedule.db")
    spec = Exa(api_key="unused").tools.web_search()
    gate = make_gate(journal)
    runner = AgentRunner(settings, journal, make_tool_server(FakeSearch(spec.description, spec.input_schema), journal, memory, schedule, gate=gate),
                         gate=gate, system_prompt=conversation_prompt(False))
    conv = Conversation(settings, journal, Ledger(journal), runner, memory, schedule=schedule, gate=gate,
                        voice=MistralVoice(settings.mistral_api_key, settings.voice_model))
    chat = Chat()
    started = time.monotonic()
    await conv.handle_voice(settings.owner_id, recording, "voice.ogg", len(recording), 6, chat)
    await conv.wait()
    await runner.close()
    jobs = schedule.jobs()
    ok = bool(jobs) and jobs[0].kind == "reminder" and jobs[0].next_run.hour == 9 and "priya" in jobs[0].text.lower()
    print(json.dumps({
        "recording": {"bytes": len(recording), "is_ogg": recording[:4] == b"OggS"},
        "shown_to_the_owner_first": chat.sent[0] if chat.sent else None,
        "reply": chat.sent[1:] if len(chat.sent) > 1 else None,
        "scheduled": [j.describe() for j in jobs],
        "seconds": round(time.monotonic() - started, 1),
        "voice_record": [{k: r.get(k) for k in ("ok", "seconds", "bytes", "chars")} for r in journal.read() if r["kind"] == "voice"],
        "errors": [r for r in journal.read() if r["kind"] == "error"],
        "a_reminder_for_nine_about_priya_was_set": ok}, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
