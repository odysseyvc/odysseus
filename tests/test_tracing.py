"""Tracing is optional, must never break a task, and must never send a secret."""
from __future__ import annotations

from types import SimpleNamespace

from odysseus.tracing import REDACTED, Tracing, scrub


def test_without_keys_tracing_is_off_and_every_call_is_harmless():
    t = Tracing()
    assert t.on is False and t.check() is False
    with t.task("hello", session="s") as traced:
        traced.end("reply", status="done", cost_usd=0.01, finished=True)
    t.close()


def test_secrets_and_things_shaped_like_them_are_removed():
    secrets = ("composio-key-abcdef", "exa-key-123456")
    text = "env ANTHROPIC_API_KEY=sk-ant-api03-AbCdEfGhIjKl and composio-key-abcdef; card 4111 1111 1111 1111; bot 1234567890:AAEabcdefghijklmnopqrstuvwxyz0123456"
    clean = scrub(text, secrets)
    for leaked in ("sk-ant-api03", "composio-key-abcdef", "4111 1111", "AAEabcdefghijkl"):
        assert leaked not in clean
    assert clean.count(REDACTED) == 4 and "Wednesday at 12:30" in scrub("Lunch on Wednesday at 12:30", secrets)


def test_every_text_value_of_every_span_is_cleaned_before_it_is_sent():
    t = Tracing()
    t._secrets = ("composio-key-abcdef",)
    spans = {"a": SimpleNamespace(attributes={"input.value": "key composio-key-abcdef", "n": 3, "ok": "nothing here"}),
             "b": SimpleNamespace(attributes={"output.value": "plain"})}
    result = t._mask(params=SimpleNamespace(spans=spans))
    assert list(result.span_patches) == ["a"]
    assert result.span_patches["a"].set_attributes == {"input.value": f"key {REDACTED}"}
    assert t._mask(params=SimpleNamespace(spans={"b": spans["b"]})) is None
