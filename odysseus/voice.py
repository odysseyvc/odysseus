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
"""
from __future__ import annotations

import re
from typing import Protocol

MAX_BYTES = 20 * 1024 * 1024
MAX_SECONDS = 600            # ten minutes; longer than that is a recording, not a message
MAX_VOCABULARY = 80

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


def vocabulary_from(profile_text: str) -> list[str]:
    """Names worth listening for: capitalised words the owner's memory already holds, each once."""
    words: list[str] = []
    for w in re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", profile_text):
        if w not in words and w not in ("The", "They", "Their", "Owner", "This", "That"):
            words.append(w)
    return words[:MAX_VOCABULARY]
