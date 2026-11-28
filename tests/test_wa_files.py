"""A file sent on WhatsApp: handed to the service first, then named in a message, on each of the two routes.

No test calls WhatsApp. What the program would send is collected in lists; file contents are made up.
"""
from __future__ import annotations

import pytest

from odysseus.files import deliver
from odysseus.journal import Journal
from odysseus.vobiz import VobizChat
from odysseus.typed import TypedAnswers
from odysseus.whatsapp import WhatsAppChat, file_kind

DECK = b"PK\x03\x04 a made-up deck"


def http_for(sent, uploads, media_key, refuse_upload=False, no_upload=False):
    async def http(method, url, body):
        sent.append((url, body))
        return 201, {"id": "out-1"}

    async def upload(url, fields, name, data, mime):
        uploads.append((url, fields, name, len(data), mime))
        return (400, {"error": "bad"}) if refuse_upload else (201, {media_key: "media-77", "type": mime})
    if not no_upload:
        http.upload = upload
    return http


def direct(tmp_path, **kw):
    sent, uploads = [], []
    chat = WhatsAppChat(http_for(sent, uploads, "id", **kw), "1065", "447700900123", Journal(tmp_path / "j.jsonl"))
    return chat, sent, uploads


def rented(tmp_path, **kw):
    sent, uploads = [], []
    chat = VobizChat(http_for(sent, uploads, "media_id", **kw), "https://api.vobiz.ai/api/v1/Account/MA_TEST/WhatsApp/Message/", "chan-1",
                     "447700900123", Journal(tmp_path / "j.jsonl"), TypedAnswers(lambda: 0.0))
    return chat, sent, uploads


async def test_on_the_direct_route_a_file_is_handed_over_and_then_named_in_a_message(tmp_path):
    chat, sent, uploads = direct(tmp_path)
    await chat.send_file("Northwind memo.pptx", data=DECK, caption="**Northwind** memo, made today")
    assert uploads == [("https://graph.facebook.com/v27.0/1065/media", {"messaging_product": "whatsapp"}, "Northwind memo.pptx", len(DECK),
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation")]
    url, body = sent[0]
    assert url.endswith("/1065/messages") and body["type"] == "document" and body["to"] == "447700900123"
    assert body["document"] == {"id": "media-77", "filename": "Northwind memo.pptx", "caption": "*Northwind* memo, made today"}


async def test_on_the_rented_number_the_same_in_that_routes_own_shapes(tmp_path):
    chat, sent, uploads = rented(tmp_path)
    await chat.send_file("review.pdf", data=b"%PDF-1.4 made up", caption="")
    assert uploads == [("https://api.vobiz.ai/api/v1/Account/MA_TEST/WhatsApp/Media/", {"channel_id": "chan-1"}, "review.pdf", 16, "application/pdf")]
    url, body = sent[0]
    assert url.endswith("/WhatsApp/Message/") and body["type"] == "document" and body["to"] == "+447700900123" and body["channel_id"] == "chan-1"
    assert body["media"] == {"id": "media-77", "filename": "review.pdf"}, "no caption field when there is nothing to say"


async def test_a_picture_goes_as_a_picture_only_when_asked_for_as_one(tmp_path):
    assert file_kind("chart.png", "photo")[0] == "image" and file_kind("chart.png")[0] == "document" and file_kind("deck.pptx", "photo")[0] == "document"
    chat, sent, uploads = rented(tmp_path)
    await chat.send_file("chart.png", data=b"\x89PNG made up", kind="photo", caption="Revenue by month")
    assert sent[0][1]["type"] == "image" and sent[0][1]["media"] == {"id": "media-77", "caption": "Revenue by month"}


async def test_what_cannot_be_sent_is_an_error_and_never_a_silent_nothing(tmp_path):
    for make in (direct, rented):
        chat, sent, uploads = make(tmp_path)
        with pytest.raises(ValueError):
            await chat.send_file("deck.pptx", handle="another-chats-id-for-it")      # no bytes: the caller falls back to its copy
        with pytest.raises(ValueError):
            await chat.send_file("big.pdf", data=b"x" * (101 * 1024 * 1024))
        chat, sent, uploads = make(tmp_path, refuse_upload=True)
        with pytest.raises(RuntimeError):
            await chat.send_file("deck.pptx", data=DECK)
        assert sent == [], "no message names a file that was not taken"
        chat, sent, uploads = make(tmp_path, no_upload=True)
        with pytest.raises(RuntimeError):
            await chat.send_file("deck.pptx", data=DECK)
    assert [r["where"] for r in Journal(tmp_path / "j.jsonl").read() if r.get("kind") == "error"] == ["whatsapp.upload", "vobiz.upload"]


async def test_a_kept_file_is_delivered_on_whatsapp_from_the_copy(tmp_path):
    chat, sent, uploads = rented(tmp_path)

    class Shelf:
        def data(self, entry):
            return DECK
    entry = {"id": "f1", "name": "deck.pptx", "handle": "a-telegram-file-id", "kind": "document", "at": "2027-01-15T08:00:00", "source": "chat"}
    assert await deliver(Shelf(), entry, chat) == ""
    assert len(uploads) == 1 and sent[0][1]["media"]["filename"] == "deck.pptx" and "deck.pptx, sent 2027-01-15" in sent[0][1]["media"]["caption"]


def test_a_file_asked_for_in_a_chat_goes_to_that_chat_when_it_can_take_files():
    import inspect
    from odysseus import __main__ as main
    src = inspect.getsource(main)
    assert "def files_go_to()" in src and src.count("deliver(shelf, entry, files_go_to())") == 2
