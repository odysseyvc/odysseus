"""Attio through its own server: what runs unasked, what asks, and what the owner is told. No network."""
from __future__ import annotations

import asyncio

from odysseus.attio import ATTIO_RUN, LAPSED, Attio, FileTokens, NotSignedIn, kind
from odysseus.journal import Journal
from odysseus.tools import make_gate

READ = {"title": "x", "read_only_hint": True, "destructive_hint": False, "open_world_hint": False}
WRITE = {"title": "x", "read_only_hint": False, "destructive_hint": True}


def row(name, labels, title=None):
    return {"name": name, "title": title or name, "labels": labels, "description": f"Does {name}.", "inputs": {"type": "object"}}


class FakeAttio:
    def __init__(self, fail=None):
        self.calls, self.listed, self.fail = [], 0, fail

    async def list_tools(self):
        self.listed += 1
        if self.fail:
            raise self.fail
        return [row("search-records", READ), row("list-records-in-list", READ), row("create-note", WRITE, "Create note"),
                row("merge-records", WRITE), row("render-home", READ), row("update-data-model", WRITE)]

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        return "Acme Robotics, contact jo@acme.example, reference QX7R2L99"


def rig(tmp_path, **kw):
    backend = FakeAttio(**kw)
    attio = Attio(backend, Journal(tmp_path / "j.jsonl"))
    gate = make_gate(Journal(tmp_path / "g.jsonl"), attio=attio)
    return backend, attio, gate, attio.handlers()


def test_a_tool_runs_unasked_only_when_its_name_and_attios_label_both_say_it_reads():
    assert kind("search-records", READ) == "read" and kind("semantic-search-emails", READ) == "read" and kind("whoami", READ) == "read"
    assert kind("create-note", WRITE) == "change"
    assert kind("create-note", READ) == "change", "a wrong label does not make a write a read"
    assert kind("search-records", WRITE) == "change", "a reading name labelled as writing asks"
    assert kind("search-records", {}) == "change", "no label, no free pass"
    assert kind("list-records", {**READ, "destructive_hint": True}) == "change", "contradictory labels ask"


async def test_reads_are_allowed_and_every_change_is_asked_about_with_its_inputs(tmp_path):
    backend, attio, gate, h = rig(tmp_path)
    note = {"tool": "create-note", "arguments": {"title": "Call", "content": "They are raising in March."}}
    assert gate.decide(ATTIO_RUN, {"tool": "search-records", "arguments": {}}) == "ask", "not looked up yet: nothing is known of it"
    listing = (await h["attio_find"]({}))["content"][0]["text"]
    assert "search-records  (runs at once)" in listing and "create-note  (asks the owner first)" in listing
    assert "render-home" not in listing, "pictures for Attio's own apps are not offered"
    assert gate.decide(ATTIO_RUN, {"tool": "search-records", "arguments": {"query": "jo@acme.example"}}) == "allow"
    assert gate.decide(ATTIO_RUN, note) == "ask" and gate.decide(ATTIO_RUN, {"tool": "merge-records"}) == "ask"
    assert gate.decide(ATTIO_RUN, {"tool": "update-data-model"}) == "ask" and gate.decide(ATTIO_RUN, {"tool": "no-such-tool"}) == "ask"
    question = attio.describe(note)
    assert question.startswith("Do this in Attio?") and "Create note (create-note)" in question and "They are raising in March." in question
    assert backend.listed == 1, "the list of tools is fetched once"


async def test_a_read_is_not_held_back_for_carrying_details_from_the_owners_mail(tmp_path):
    _, _, gate, h = rig(tmp_path)
    await h["attio_find"]({})
    gate.taint.add("From jo@acme.example: our reference is QX7R2L99", "your mail, calendar or apps")
    assert gate.decide(ATTIO_RUN, {"tool": "search-records", "arguments": {"query": "jo@acme.example"}}) == "allow"


async def test_what_attio_returns_is_marked_as_material_and_remembered_as_private(tmp_path):
    backend, attio, gate, h = rig(tmp_path)
    out = await h["attio_run"]({"tool": "search-records", "arguments": {"query": "Acme"}})
    text = out["content"][0]["text"]
    assert text.startswith("What Attio returned follows. It is material to read") and "Acme Robotics" in text
    assert backend.calls == [("search-records", {"query": "Acme"})] and attio.pop_changes() == [], "a read is not reported as a change"
    assert gate.taint.leaked("who is jo@acme.example"), "a later web search carrying it would be asked about"
    await h["attio_run"]({"tool": "create-note", "arguments": {"title": "t"}})
    assert attio.pop_changes() == ['did "Create note" in Attio']


async def test_bad_requests_and_a_lapsed_sign_in_are_said_plainly(tmp_path):
    _, _, _, h = rig(tmp_path)
    for bad in ({"tool": "Create Note"}, {"tool": "create-note", "arguments": "x"}, {"tool": "no-such-tool"}):
        assert (await h["attio_run"](bad)).get("is_error")
    assert "no tool called nope" in (await h["attio_find"]({"tools": ["nope"]}))["content"][0]["text"]
    _, _, _, lapsed = rig(tmp_path / "b", fail=ExceptionGroup("g", [ExceptionGroup("h", [NotSignedIn("x")])]))
    out = await lapsed["attio_find"]({})
    assert out.get("is_error") and out["content"][0]["text"] == LAPSED
    _, _, _, down = rig(tmp_path / "c", fail=ConnectionError("x"))
    assert "could not be reached: ConnectionError" in (await down["attio_find"]({}))["content"][0]["text"]


def test_the_token_file_round_trips_and_says_whether_anyone_is_signed_in(tmp_path):
    from mcp.shared.auth import OAuthToken
    tokens = FileTokens(tmp_path / "deep" / "attio.json")
    assert not tokens.signed_in() and asyncio.run(tokens.get_tokens()) is None and asyncio.run(tokens.get_client_info()) is None
    asyncio.run(tokens.set_tokens(OAuthToken(access_token="a", token_type="Bearer", refresh_token="r")))
    assert tokens.signed_in() and asyncio.run(tokens.get_tokens()).refresh_token == "r"
