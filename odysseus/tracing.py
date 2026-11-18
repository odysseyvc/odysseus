"""A record of every task that can be read afterwards: each model call, each tool call, and how it ended.

The journal (journal.py) records what happened as lines in a file. That is enough to
audit one task and poor for reading many: there is no way to see a whole conversation,
compare tasks, or find the ones that went badly. This sends the same events to Langfuse,
a trace store with a viewer, so that real use can be read and each failure turned into a
test (Chapter 15).

It is off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set. Nothing here is
needed for Odysseus to work, and no failure here may break a task.

What leaves the machine when it is on: the owner's messages, the model's replies, and
what tools returned, which includes the text of mail the model read. That is a copy of
private content held by one more company, or on the owner's own server if Langfuse is
self-hosted. Two things are removed before anything is sent:

  secrets    every setting that is a key or token, wherever it appears, and anything shaped
             like a card number or a model key
  contents   what the mail, calendar and app tools returned. Each such text is replaced by a
             note of its length, so a trace shows that mail was read, how much, and what
             happened next, and not the mail itself. LANGFUSE_FULL_CONTENT=1 keeps the text.

The owner's own messages and the model's replies are sent, because reading them is the
point. A reply that quotes an email therefore carries that much of it.

The model and tool calls are captured by the instrumentation Langfuse recommends for the
Claude Agent SDK (openinference). What is added here is the task around them, the
conversation it belongs to, and scores computed by code: how it ended, what it cost, how
many steps, and what the gate did.
"""
from __future__ import annotations

import json
import re
from collections import deque
from contextlib import contextmanager
from typing import Any, Iterator

_SHAPES = re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}|\b(?:\d[ -]?){13,16}\b|\b\d{8,10}:[A-Za-z0-9_-]{30,}\b")
REDACTED = "[removed]"


def scrub(text: str, secrets: tuple[str, ...]) -> str:
    for s in secrets:
        if s and s in text:
            text = text.replace(s, REDACTED)
    return _SHAPES.sub(REDACTED, text)


class _Task:
    """One task being traced. Every method is safe to call when tracing is off."""

    def __init__(self, client: Any = None, span: Any = None, clean: Any = None):
        self._client, self._span, self._clean = client, span, clean or (lambda t: t)

    def end(self, output: str, **scores: Any) -> None:
        if self._client is None:
            return
        try:
            self._span.update(output=self._clean(output)[:8000])
            for name, value in scores.items():
                if value is None:
                    continue
                kind = "CATEGORICAL" if isinstance(value, str) else "BOOLEAN" if isinstance(value, bool) else "NUMERIC"
                self._client.score_current_trace(name=name, value=(1 if value else 0) if isinstance(value, bool) else value, data_type=kind)
        except Exception:
            pass     # a trace that cannot be written must never end a task


class Tracing:
    def __init__(self, public_key: str = "", secret_key: str = "", base_url: str = "", secrets: tuple[str, ...] = (), environment: str = "live",
                 full_content: bool = False):
        self.on = False
        self._client = None
        self._full = full_content
        self._private: deque[tuple[str, ...]] = deque(maxlen=400)     # recent texts read from the accounts, in each form they may appear
        self._secrets = tuple(s for s in (*secrets, secret_key) if s and len(s) >= 6)
        if not (public_key and secret_key):
            return
        try:
            from langfuse import Langfuse
            from openinference.instrumentation.claude_agent_sdk import ClaudeAgentSDKInstrumentor
            self._client = Langfuse(public_key=public_key, secret_key=secret_key, base_url=base_url or None, environment=environment,
                                    mask_otel_spans=self._mask)
            ClaudeAgentSDKInstrumentor().instrument()
            self.on = True
        except Exception as e:      # missing packages or a bad setting: run without tracing and say so once
            print(f"odysseus tracing off: {type(e).__name__}: {str(e)[:200]}", flush=True)
            self._client = None

    def hide(self, text: str) -> None:
        """Called with each thing read from the owner's accounts. From now on it is kept out of what is sent."""
        if self._full or len(text) < 40:
            return
        once = json.dumps(text, ensure_ascii=False)[1:-1]            # as it appears inside a JSON string
        twice = json.dumps(once, ensure_ascii=False)[1:-1]           # and inside JSON that is itself inside a JSON string
        ascii_once = json.dumps(text)[1:-1]
        self._private.append((f"[{len(text)} characters read from the owner's accounts; not recorded]",
                              *dict.fromkeys((text, once, twice, ascii_once, json.dumps(ascii_once)[1:-1]))))

    def clean(self, text: str) -> str:
        for note, *forms in self._private:
            for form in forms:
                if form in text:
                    text = text.replace(form, note)
        return scrub(text, self._secrets)

    def _mask(self, *, params: Any) -> Any:
        """Called on every batch before it is sent. Removes secrets from every text value of every span."""
        from langfuse import MaskOtelSpansResult, OtelSpanPatch
        patches = {}
        for ident, span in params.spans.items():
            changed = {}
            for key, value in (span.attributes or {}).items():
                if isinstance(value, str):
                    clean = self.clean(value)
                    if clean != value:
                        changed[key] = clean
            if changed:
                patches[ident] = OtelSpanPatch(set_attributes=changed)
        return MaskOtelSpansResult(span_patches=patches) if patches else None

    def check(self) -> bool:
        try:
            return bool(self._client and self._client.auth_check())
        except Exception:
            return False

    @contextmanager
    def task(self, text: str, session: str = "", tags: tuple[str, ...] = ()) -> Iterator[_Task]:
        """Wrap one task. The model and tool calls made inside it appear beneath it in the trace."""
        if not self.on:
            yield _Task()
            return
        try:
            from langfuse import propagate_attributes
            outer = self._client.start_as_current_observation(name="task", as_type="agent", input=self.clean(text)[:8000])
            attrs = propagate_attributes(user_id="owner", session_id=session or None, tags=list(tags) or None, trace_name="task")
        except Exception:
            yield _Task()
            return
        with outer as span, attrs:
            yield _Task(self._client, span, self.clean)

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.flush()
                self._client.shutdown()
            except Exception:
                pass
