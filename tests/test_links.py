"""A file fetched from a link in the owner's own message."""
from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from odysseus.links import Fetcher, LinkError, PublicOnly, _public, find, target, viewer
from test_ocr import PDF, rig, said, send
from test_slice import OWNER

ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz_0123-45"


def test_which_links_are_fetched_and_from_where():
    assert target("https://corvid.example/decks/Corvid%20deck.pdf") == "https://corvid.example/decks/Corvid%20deck.pdf"
    assert target("https://corvid.example/deck.PDF).") == "https://corvid.example/deck.PDF"
    assert target(f"https://drive.google.com/file/d/{ID}/view?usp=sharing") == f"https://drive.google.com/uc?export=download&id={ID}"
    assert target(f"https://drive.google.com/open?id={ID}") == f"https://drive.google.com/uc?export=download&id={ID}"
    assert target(f"https://docs.google.com/presentation/d/{ID}/edit#slide=id.p") == f"https://docs.google.com/presentation/d/{ID}/export/pdf"
    assert target(f"https://docs.google.com/document/d/{ID}/edit") == f"https://docs.google.com/document/d/{ID}/export?format=pdf"
    assert target("https://www.dropbox.com/s/abc123/deck.pdf?dl=0") == "https://www.dropbox.com/s/abc123/deck.pdf?dl=1"
    for no in ("http://corvid.example/deck.pdf", "https://corvid.example/about", "https://docsend.com/view/abc123", "ftp://x.example/a.pdf",
               "https://drive.google.com/drive/folders/short", f"https://docs.google.com/spreadsheets/d/{ID}/edit"):
        assert target(no) is None, no
    assert find(f"have a look: https://docsend.com/view/x and https://docs.google.com/presentation/d/{ID}/edit, thanks")[0].endswith("/edit")
    assert find("no link here") is None and find("") is None
    assert viewer("see https://docsend.com/view/abc") == "docsend.com" and viewer("https://www.canva.com/design/x") == "canva.com"
    assert viewer("https://notdocsend.com/x") is None


def test_only_public_addresses_count_as_public():
    assert _public("93.184.216.34") and _public("2606:2800:220:1:248:1893:25c8:1946")
    for private in ("127.0.0.1", "10.0.0.5", "192.168.1.10", "172.16.0.1", "169.254.169.254", "::1", "fe80::1", "0.0.0.0", "not an address"):
        assert not _public(private), private


async def test_a_name_that_resolves_to_this_machine_is_refused_when_connecting():
    with pytest.raises(OSError):
        await PublicOnly().resolve("localhost", 80)
    with pytest.raises(LinkError, match="private address"):
        await Fetcher().fetch("https://127.0.0.1/deck.pdf")
    with pytest.raises(LinkError, match="private address"):
        await Fetcher().fetch("https://localhost/deck.pdf")
    with pytest.raises(LinkError, match="secure"):
        await Fetcher().fetch("http://corvid.example/deck.pdf")


async def served(routes):
    app = web.Application()
    for path, handler in routes.items():
        app.router.add_get(path, handler)
    server = TestServer(app)
    await server.start_server()
    return server, Fetcher(allow=lambda ip: True, schemes=("http",), max_bytes=5000, timeout_s=10)


async def test_a_pdf_is_fetched_through_a_redirect_and_named_and_everything_else_is_refused_with_a_reason():
    pdf = b"%PDF-1.7 " + b"x" * 300

    async def deck(request):
        return web.Response(body=pdf, headers={"Content-Disposition": 'attachment; filename="Corvid deck.pdf"'})

    async def moved(request):
        raise web.HTTPFound("/deck.pdf")

    async def page(request):
        return web.Response(text="<html>Sign in to continue</html>" + " " * 2000, content_type="text/html")

    async def big(request):
        return web.Response(body=b"%PDF-1.7 " + b"x" * 9000)

    async def away(request):
        raise web.HTTPFound("http://10.0.0.5/secret.pdf")

    async def forbidden(request):
        raise web.HTTPForbidden()
    server, f = await served({"/deck.pdf": deck, "/go": moved, "/login.pdf": page, "/big.pdf": big, "/away.pdf": away, "/no.pdf": forbidden})
    try:
        base = f"http://127.0.0.1:{server.port}"
        assert await f.fetch(base + "/go") == (pdf, "Corvid deck.pdf")
        with pytest.raises(LinkError, match="not a PDF"):
            await f.fetch(base + "/login.pdf")
        with pytest.raises(LinkError, match="larger than"):
            await f.fetch(base + "/big.pdf")
        with pytest.raises(LinkError, match="not allowed"):
            await f.fetch(base + "/no.pdf")
        with pytest.raises(LinkError, match="answered 404"):
            await f.fetch(base + "/missing.pdf")
        strict = Fetcher(allow=lambda ip: ip == "127.0.0.1", schemes=("http",), timeout_s=10)
        with pytest.raises(LinkError, match="private address"):
            await strict.fetch(base + "/away.pdf")          # a public page must not be able to send the program inwards
    finally:
        await server.close()


class FakeLinks:
    def __init__(self, fail=""):
        self.asked, self.fail = [], fail

    async def fetch(self, url):
        self.asked.append(url)
        if self.fail:
            raise LinkError(self.fail)
        return b"%PDF-1.7 fetched", "Corvid deck.pdf"


async def test_a_link_alone_in_the_owners_message_is_read_as_a_file_sent_with_no_message(tmp_path):
    x = rig(tmp_path)
    x.conv._links = FakeLinks()
    await x.conv.handle(OWNER, "https://corvid.example/deck.pdf", x.chat)
    await x.conv.wait()
    assert x.conv._links.asked == ["https://corvid.example/deck.pdf"] and x.ocr.calls == [(16, PDF)]
    prompt = await said(x.clients[0].prompts[0])
    assert "FILE: Corvid deck.pdf" in prompt and "(The owner sent this file with no message." in prompt
    assert [r["ok"] for r in x.journal.read() if r["kind"] == "link"] == [True]


async def test_words_beside_the_link_stay_as_the_owners_message_and_a_failure_is_told_to_the_model(tmp_path):
    x = rig(tmp_path)
    x.conv._links = FakeLinks()
    await x.conv.handle(OWNER, "Is the round priced? https://corvid.example/deck.pdf", x.chat)
    await x.conv.wait()
    prompt = await said(x.clients[0].prompts[0])
    assert prompt.index("END OF FILE") < prompt.index("Is the round priced? https://corvid.example/deck.pdf")
    y = rig(tmp_path / "y")
    y.conv._links = FakeLinks(fail="what is at that link is not a PDF but a web page")
    await y.conv.handle(OWNER, "https://corvid.example/deck.pdf", y.chat)
    await y.conv.wait()
    prompt = y.clients[0].prompts[0]
    assert y.ocr.calls == [] and "FILE:" not in prompt and "could not: what is at that link is not a PDF" in prompt and "Nothing of it has been read" in prompt


async def test_a_link_is_not_fetched_for_a_stranger_from_inside_a_file_or_when_it_is_not_a_file(tmp_path):
    x = rig(tmp_path)
    x.conv._links = FakeLinks()
    await x.conv.handle(999, "https://corvid.example/deck.pdf", x.chat)
    await send(x, caption="see https://corvid.example/other.pdf")       # a caption on a file: the file is what was sent
    await x.conv.handle(OWNER, "what is on https://corvid.example/about today?", x.chat)
    await x.conv.wait()
    assert x.conv._links.asked == []
