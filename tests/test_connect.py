"""Connecting a service from the chat."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from odysseus import mcpapp
from odysseus.attio import FileTokens, _return_port
from urllib.parse import urlsplit

from odysseus.connect import Connector, _published_by, slug_for

REGISTRY = {"exa": ["https://mcp.exa.ai/mcp"], "exa.ai": ["https://mcp.exa.ai/mcp"], "twins": ["https://mcp.twins.com/mcp", "https://mcp.twins.ai/mcp"]}
from odysseus.journal import Journal
from test_ocr import rig
from test_slice import OWNER


def tool(name, read=None):
    labels = None if read is None else SimpleNamespace(model_dump=lambda exclude_none=True: {"read_only_hint": read, "destructive_hint": False})
    return SimpleNamespace(name=name, annotations=labels)


class FakeServer:
    """Stands in for the sign-in and the server: shows a link, and 'approves' by writing tokens."""

    def __init__(self, tools=(), fail=None, hang=False):
        self.tools, self.fail, self.hang, self.opened = list(tools), fail, hang, []

    @asynccontextmanager
    async def __call__(self, tokens, show, url, scope, port):
        self.opened.append((url, scope, port))
        await show(f"https://service.example/authorize?scope={(scope or 'everything').replace(' ', '+')}&state=s")
        if self.hang:
            await asyncio.sleep(60)
        if self.fail:
            raise self.fail
        tokens._write("tokens", {"access_token": "t", "token_type": "Bearer"})
        yield SimpleNamespace(list_tools=self._list)

    async def _list(self):
        return SimpleNamespace(tools=self.tools)


@pytest.fixture(autouse=True)
def clean_registry():
    before = dict(mcpapp.APPS)
    yield
    mcpapp.APPS.clear()
    mcpapp.APPS.update(before)


def make(tmp_path, server, public=True, timeout_s=5):
    sent, restarts = [], []

    async def send(text):
        sent.append(text)

    async def is_public(url):
        return public
    async def lookup(label, site):
        return list(REGISTRY.get(site or label, []))

    async def probe(url):
        return urlsplit(url).path.rstrip("/").endswith("/mcp") and "website" not in url
    c = Connector(tmp_path, Journal(tmp_path / "j.jsonl"), lambda: restarts.append(1), opener=server, port=lambda: 51111, public=is_public,
                  timeout_s=timeout_s, lookup=lookup, probe=probe)
    return c, send, sent, restarts


def test_a_short_name_is_made_from_a_servers_address():
    assert slug_for("https://mcp.linear.app/mcp") == "linear" and slug_for("https://docsend.com/mcp") == "docsend"
    assert slug_for("https://api.x.com/mcp") == "x" and slug_for("https://mcp.notion-tools.io/sse") == "notiontools"


async def test_any_server_is_connected_by_its_address_saved_and_the_program_restarts(tmp_path):
    server = FakeServer([tool("list_issues", True), tool("search_docs", True), tool("create_issue", False), tool("get_everything")])
    c, send, sent, restarts = make(tmp_path, server)
    await c.connect(" https://mcp.linear.app/mcp", send)
    await c._task
    assert server.opened == [("https://mcp.linear.app/mcp", None, 51111)]
    assert "open this link in a browser on the computer I am running on" in sent[0] and "https://service.example/authorize" in sent[0]
    assert "Linear is connected: 4 tools. 2 only read" in sent[1] and "2 add or change something and will be put to you first" in sent[1]
    assert "Nobody has read this server's tools before" in sent[1], "a tool with no label is not trusted to be a read, whatever it is called"
    assert restarts == [1]
    assert json.loads((tmp_path / "services.json").read_text())["linear"] == {"url": "https://mcp.linear.app/mcp"}
    assert FileTokens(tmp_path / "linear.json").signed_in()
    mcpapp.APPS.pop("linear")
    mcpapp.load_custom(tmp_path)          # as at the next start
    assert mcpapp.APPS["linear"]["custom"] and mcpapp.APPS["linear"]["scope"] is None and "written by that service" in mcpapp.APPS["linear"]["prompt"]
    assert [a.slug for a in mcpapp.connected(tmp_path, Journal(tmp_path / "j2.jsonl"))] == ["linear"]


async def test_a_known_service_is_connected_by_name_with_the_permissions_this_program_chose(tmp_path):
    server = FakeServer([tool("list_visits", True), tool("create_space_link", False)])
    c, send, sent, restarts = make(tmp_path, server)
    await c.connect("docsend", send)
    await c._task
    assert server.opened[0][:2] == ("https://docsend.com/mcp", "documents:read spaces:read analytics:read contacts:read")
    assert "It asks for these permissions: documents:read spaces:read analytics:read contacts:read" in sent[0]
    assert "DocSend is connected: 1 tools. 1 only read" in sent[1], "the writing tool is not offered: the sign-in cannot write"
    assert not (tmp_path / "services.json").exists() and restarts == [1]


async def test_what_is_refused_before_anything_is_opened(tmp_path):
    server = FakeServer()
    for arg, words, public in (("http://mcp.site.example/mcp", "only connect to https", True), ("nonsense", "could not find a server for nonsense", True),
                               ("twins", "more than one server", True), ("https://website.nowhere.example", "could not find a server for website.nowhere.example", True),
                               ("https://10.0.0.5/mcp", "not lead to a public server", False),
                               ("https://mcp.attio.example/mcp attio", "short name", True), ("https://x.example/mcp", "short name", True)):
        c, send, sent, restarts = make(tmp_path, server, public=public)
        await c.connect(arg, send)
        assert c._task is None and words in sent[0], (arg, sent)
    assert server.opened == []
    c, send, sent, _ = make(tmp_path, server)
    await c.connect("x", send)
    assert "does not let a program register itself" in sent[0] and server.opened == []


async def test_a_sign_in_nobody_approves_and_one_that_fails_save_nothing_and_do_not_restart(tmp_path):
    c, send, sent, restarts = make(tmp_path, FakeServer(hang=True), timeout_s=0.2)
    await c.connect("https://mcp.linear.app/mcp", send)
    assert c.busy
    await c.connect("https://mcp.other.example/mcp", send)
    assert "already waiting for one sign-in" in sent[-1]
    await c._task
    assert "Nobody approved" in sent[-1] and restarts == [] and not (tmp_path / "services.json").exists()
    c, send, sent, restarts = make(tmp_path, FakeServer(fail=RuntimeError("no oauth")))
    await c.connect("https://mcp.linear.app/mcp", send)
    await c._task
    assert "could not be connected" in sent[-1] and "may not offer the standard sign-in" in sent[-1] and restarts == []


async def test_the_list_and_forgetting_a_service(tmp_path):
    c, send, sent, restarts = make(tmp_path, FakeServer([tool("list_issues", True)]))
    await c.connect("", send)
    assert sent[0].startswith("Connected: none.") and "Granola (granola)" in sent[0] and "/connect exa" in sent[0]
    await c.connect("https://mcp.linear.app/mcp", send)
    await c._task
    await c.connect("", send)
    assert "Connected: Linear (linear)." in sent[-1]
    await c.disconnect(" linear", send)
    assert "Linear is forgotten here" in sent[-1] and "withdraw the permission at Linear itself" in sent[-1] and restarts == [1, 1]
    assert not (tmp_path / "linear.json").exists() and "linear" not in mcpapp.APPS and json.loads((tmp_path / "services.json").read_text()) == {}
    await c.disconnect("nothing", send)
    assert "Send /disconnect and the name" in sent[-1]


def test_a_program_that_registered_before_comes_back_to_the_same_port(tmp_path):
    fresh = FileTokens(tmp_path / "a.json")
    assert _return_port(fresh, 51111) == 51111 and _return_port(fresh, None) == 8765
    fresh._write("client", {"client_id": "c", "redirect_uris": ["http://127.0.0.1:8765/callback"]})
    assert _return_port(fresh, 51111) == 8765, "the service remembers the address it was given"


async def test_only_the_owners_typed_command_connects_and_not_in_the_middle_of_a_task(tmp_path):
    x = rig(tmp_path)
    calls = []

    class Fake:
        async def connect(self, arg, send):
            calls.append(("connect", arg.strip()))

        async def disconnect(self, arg, send):
            calls.append(("disconnect", arg.strip()))
    await x.conv.handle(OWNER, "/connect https://mcp.linear.app/mcp", x.chat)
    assert "not set up here" in x.chat.sent[-1]
    x.conv._connector = Fake()
    await x.conv.handle(999, "/connect https://mcp.evil.example/mcp", x.chat)
    await x.conv.handle(OWNER, "/connect https://MCP.Linear.app/mcp Linear", x.chat)
    await x.conv.handle(OWNER, "/services", x.chat)
    await x.conv.handle(OWNER, "/disconnect linear", x.chat)
    assert calls == [("connect", "https://MCP.Linear.app/mcp Linear"), ("connect", ""), ("disconnect", "linear")]
    assert x.clients == [], "no model is involved"


class OpenServer(FakeServer):
    """A public server: it answers without any sign-in, so no link is shown and no token exists."""

    @asynccontextmanager
    async def __call__(self, tokens, show, url, scope, port):
        self.opened.append((url, scope, port))
        yield SimpleNamespace(list_tools=self._list)


async def test_a_public_server_that_asks_for_no_sign_in_is_connected_and_survives_a_restart(tmp_path):
    c, send, sent, restarts = make(tmp_path, OpenServer([tool("read_wiki", True), tool("ask_question", False)]))
    c._also = ("Attio",)
    await c.connect("https://mcp.deepwiki.example/mcp", send)
    await c._task
    assert len(sent) == 1 and "Deepwiki is connected (it asked for no sign-in: it is a public server): 2 tools. 1 only read" in sent[0]
    assert json.loads((tmp_path / "services.json").read_text())["deepwiki"] == {"url": "https://mcp.deepwiki.example/mcp", "open": True}
    mcpapp.APPS.pop("deepwiki")
    mcpapp.load_custom(tmp_path)
    assert [a.slug for a in mcpapp.connected(tmp_path, Journal(tmp_path / "j2.jsonl"))] == ["deepwiki"], "no token, and still loaded at the next start"
    await c.connect("", send)
    assert sent[-1].startswith("Connected: Attio, Deepwiki (deepwiki).")
    await c.disconnect("deepwiki", send)
    assert "Deepwiki is forgotten here" in sent[-1] and "deepwiki" not in mcpapp.APPS and restarts == [1, 1]
    empty, send, sent, restarts = make(tmp_path / "e", OpenServer([]))
    await empty.connect("https://mcp.nothing.example/mcp", send)
    await empty._task
    assert "offers no tools" in sent[-1] and restarts == []


async def test_a_company_is_found_by_its_name_or_its_website_and_the_owner_is_told_which_server(tmp_path):
    for typed, told in (("exa", "I found Exa's server in the public registry of servers, published under its own domain: https://mcp.exa.ai/mcp"),
                        ("Exa.ai", "exa.ai is a website, not a server. The server that belongs to it is https://mcp.exa.ai/mcp"),
                        ("https://www.exa.ai/", "www.exa.ai is a website, not a server"), ("mcp.exa.ai/mcp", None)):
        server = OpenServer([tool("web_search_exa", True)])
        c, send, sent, restarts = make(tmp_path / typed.replace("/", "_").replace(":", ""), server)
        await c.connect(typed, send)
        await c._task
        assert server.opened[0][0] == "https://mcp.exa.ai/mcp", typed
        if told:
            assert told in sent[0] and sent[0].endswith("Connecting to it now."), sent[0]
        assert "Exa is connected" in sent[-1] and restarts == [1]
        mcpapp.APPS.pop("exa", None)


async def test_a_website_with_no_registry_entry_is_tried_at_the_usual_addresses(tmp_path):
    server = OpenServer([tool("list_things", True)])
    c, send, sent, restarts = make(tmp_path, server)
    await c.connect("https://acme.example", send)
    await c._task
    assert server.opened[0][0] == "https://mcp.acme.example/mcp" and "The server that belongs to it is https://mcp.acme.example/mcp" in sent[0]


def test_a_registry_name_gives_the_domain_it_was_published_under():
    assert _published_by("ai.exa/exa") == "exa.ai" and _published_by("app.linear/linear") == "linear.app"
    assert _published_by("ai.smithery/exa-labs-exa-code-mcp") == "smithery.ai", "someone else's server with the word in its name is not the company's"


async def test_the_data_vendors_are_known_by_name_and_get_no_special_trust(tmp_path):
    server = FakeServer([tool("search_companies", True), tool("save_list", False)])
    c, send, sent, restarts = make(tmp_path, server)
    await c.connect("", send)
    for name in ("Harmonic (harmonic)", "Specter (specter)", "Crunchbase (crunchbase)", "Crustdata (crustdata)", "Evertrace (evertrace)", "Dealroom (dealroom)"):
        assert name in sent[0]
    await c.connect("harmonic", send)
    await c._task
    assert server.opened[0][:2] == ("https://mcp.api.harmonic.ai", None)
    assert "Harmonic is connected: 2 tools. 1 only read" in sent[-1], "a tool that saves still asks, whoever the vendor is"
    assert not mcpapp.APPS["harmonic"].get("read_only_connection") and "metered" in mcpapp.APPS["harmonic"]["prompt"]
