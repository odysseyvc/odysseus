"""Ears: a voice note the owner sends is turned into text and then handled like a typed message.

A speech-to-text service does the listening (Mistral's Voxtral, through the same key that
reads files). What comes back is text, and from there nothing is new: the same
conversation code, the same gate, the same approvals.

Two things are different from typing, and both are handled in conversation.py:

  it can be misheard   so the owner is shown what was heard, before anything is done with
                       it, and the model is told that the text is a transcription and that
                       names and numbers in particular may be wrong.
  names are the hard   so the names the owner has mentioned before (taken from memory) are
  part                 given to the service as vocabulary to listen for.

The recording itself is not kept. The log records its length and how many characters
were heard, never the words.

A mouth, too: when the owner spoke, the reply is also sent as a voice note, made by the same
company's text-to-speech service. The written reply is still sent first and in full; the
spoken one is its opening, cut at a sentence, with links left out. Only the model's own
words are spoken, never the lines code adds beneath a reply. It can be turned off
(ODYSSEUS_SPEAK=off): speaking sends the reply's words to that service.
"""
from __future__ import annotations

import re
from typing import Protocol

MAX_BYTES = 20 * 1024 * 1024
MAX_SECONDS = 600            # ten minutes; longer than that is a recording, not a message
MAX_VOCABULARY = 80
SPEAK_MAX = 900              # characters of a reply that are spoken; about a minute
SPEECH_URL = "https://api.mistral.ai/v1/audio/speech"

VOICE_NOTE = ("[The owner sent this as a voice note. It was turned into text by a machine and may contain mistakes, especially in "
              "names, numbers, dates and times. If something that matters is unclear or surprising, ask the owner before acting on it.]")


class VoiceError(Exception):
    """A refusal the owner should be told about."""


class VoiceBackend(Protocol):
    def hear(self, data: bytes, filename: str, vocabulary: list[str]) -> str: ...


class MistralVoice:
    def __init__(self, api_key: str, model: str = "voxtral-small-transcribe-3"):
        self._api_key, self._model, self._client = api_key, model, None

    def hear(self, data: bytes, filename: str, vocabulary: list[str]) -> str:
        if self._client is None:
            from mistralai.client import Mistral
            self._client = Mistral(api_key=self._api_key)
        extra = {"context_bias": vocabulary[:MAX_VOCABULARY]} if vocabulary else {}
        result = self._client.audio.transcriptions.complete(model=self._model, file={"file_name": filename, "content": data}, **extra)
        return " ".join(str(getattr(result, "text", "") or "").split())

    speech_model, speech_voice = "voxtral-mini-tts-3-0", "gb_jane_neutral"

    def say(self, text: str) -> bytes:
        """The text as speech, in the format a voice note takes. One request; the answer carries the sound as base64."""
        import base64
        import httpx
        r = httpx.post(SPEECH_URL, headers={"Authorization": f"Bearer {self._api_key}"}, timeout=60,
                       json={"model": self.speech_model, "input": text, "voice_id": self.speech_voice, "response_format": "opus"})
        if r.status_code >= 400:
            raise VoiceError(f"the speech service answered {r.status_code}")
        return base64.b64decode(r.json().get("audio_data") or "")


def speakable(text: str, most: int = SPEAK_MAX) -> str:
    """The part of a reply worth saying aloud: plain sentences from its start, no links, no list marks, cut at a sentence."""
    plain = re.sub(r"(?:https?://|www\.)\S+", "", str(text))
    plain = re.sub(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", "", plain)
    plain = " ".join(re.sub(r"[*_`#>|]", "", plain).split())
    if len(plain) <= most:
        return plain
    cut = plain[:most]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[:end + 1] if end > most // 3 else cut.rsplit(" ", 1)[0] + ".") + " The rest is in the written reply."


def vocabulary_from(profile_text: str) -> list[str]:
    """Names worth listening for: capitalised words the owner's memory already holds, each once."""
    words: list[str] = []
    for w in re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", profile_text):
        if w not in words and w not in ("The", "They", "Their", "Owner", "This", "That"):
            words.append(w)
    return words[:MAX_VOCABULARY]
