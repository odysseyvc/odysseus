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
self-hosted. Before anything is sent, every setting that is a secret (keys and tokens) is
removed wherever it appears, as are things shaped like card numbers and model keys.

The model and tool calls are captured by the instrumentation Langfuse recommends for the
Claude Agent SDK (openinference). What is added here is the task around them, the
conversation it belongs to, and scores computed by code: how it ended, what it cost, how
many steps, and what the gate did.
"""
from __future__ import annotations

import re
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

    def __init__(self, client: Any = None, span: Any = None):
        self._client, self._span = client, span

    def end(self, output: str, **scores: Any) -> None:
        if self._client is None:
            return
        try:
            self._span.update(output=output[:8000])
            for name, value in scores.items():
                if value is None:
                    continue
                kind = "CATEGORICAL" if isinstance(value, str) else "BOOLEAN" if isinstance(value, bool) else "NUMERIC"
                self._client.score_current_trace(name=name, value=(1 if value else 0) if isinstance(value, bool) else value, data_type=kind)
        except Exception:
            pass     # a trace that cannot be written must never end a task


class Tracing:
    def __init__(self, public_key: str = "", secret_key: str = "", base_url: str = "", secrets: tuple[str, ...] = (), environment: str = "live"):
        self.on = False
        self._client = None
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

    def _mask(self, *, params: Any) -> Any:
        """Called on every batch before it is sent. Removes secrets from every text value of every span."""
        from langfuse import MaskOtelSpansResult, OtelSpanPatch
        patches = {}
        for ident, span in params.spans.items():
            changed = {}
            for key, value in (span.attributes or {}).items():
                if isinstance(value, str):
                    clean = scrub(value, self._secrets)
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
            outer = self._client.start_as_current_observation(name="task", as_type="agent", input=scrub(text, self._secrets)[:8000])
            attrs = propagate_attributes(user_id="owner", session_id=session or None, tags=list(tags) or None, trace_name="task")
        except Exception:
            yield _Task()
            return
        with outer as span, attrs:
            yield _Task(self._client, span)

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.flush()
                self._client.shutdown()
            except Exception:
                pass
