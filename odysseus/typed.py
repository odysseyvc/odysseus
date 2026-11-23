"""Answering an approval question in words, on a channel that has no buttons.

The rule everywhere else is that only a tap approves, and a typed "yes" is an ordinary message. The owner asked for an
exception on channels that cannot show buttons: to be asked in the same chat, and not sent to another app to tap. The
rule stands where buttons exist. This is the exception, and what keeps it narrow:

  code reads the reply, never a model. A message is an answer only if, with spaces and a final full stop or
      exclamation mark removed and in any letter case, it is exactly one of a few words: yes or approve; no or
      decline; thread (where that was offered). "yes please", "ok", "sure, send it" are ordinary messages.
  only while a question is waiting. The question was put on this same channel, by this program, less than ten minutes
      ago, which is as long as the gate itself waits. With nothing waiting, "yes" is an ordinary message.
  one answer, to the latest question. A reply answers the most recent question still waiting here, and the line sent
      back says which, by its first words.
  the same door. The reply arrived through the channel's own checks: a signed delivery, from the owner's number.

What it gives up against a tap: a button is tied to its question by the platform, and a word is tied only by being the
next thing said. Someone holding the owner's unlocked phone could answer either way; a one-word reply sent by habit,
meant for something else, is the new risk, and the line naming what was answered is how it is noticed.
"""
from __future__ import annotations

import time
from typing import Callable

WAIT_S = 600.0          # as long as the gate waits for an answer (gate.py)
YES, NO, THREAD = {"yes", "approve"}, {"no", "decline"}, {"thread"}


def how(thread: bool) -> str:
    """The line that ends a question asked this way."""
    return ("Reply YES to approve or NO to decline" + (", or THREAD to approve this and the rest of this thread today" if thread else "")
            + ". Any other reply is an ordinary message and approves nothing.")


class TypedAnswers:
    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._waiting: list[tuple[str, bool, str, float]] = []      # approval id, thread offered, the question's first words, when asked

    def asked(self, approval_id: str, thread: bool, question: str) -> None:
        first = " ".join(question.split())
        self._waiting.append((approval_id, thread, first[:60] + ("…" if len(first) > 60 else ""), self._clock()))
        del self._waiting[:-20]

    def answer(self, text: str) -> tuple[str, str, str] | None:
        """(approval id, "y" / "n" / "t", the question's first words) if this message answers a waiting question."""
        self._waiting = [w for w in self._waiting if self._clock() - w[3] < WAIT_S]
        word = (text or "").strip().rstrip(".!").strip().lower()
        if not self._waiting or word not in YES | NO | THREAD:
            return None
        approval_id, thread, first, _ = self._waiting[-1]
        if word in THREAD and not thread:
            return None
        self._waiting.pop()
        return approval_id, ("y" if word in YES else "n" if word in NO else "t"), first
