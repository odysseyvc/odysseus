"""The WhatsApp channel: the three checks at the door, and how messages and approvals are carried.

No network and no Meta account are involved: deliveries are built by hand and signed with a test secret,
and what the program would send to Meta is collected in a list.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

from odysseus.journal import Journal
from odysseus.whatsapp import OTHER_NOTE, VOICE_NOTE, WhatsApp, signature_ok

SECRET, OWNER, STRANGER = "app-secret-for-tests", "919820012345", "447700900123"


class FakeConversation:
    def __init__(self):
        self.texts, self.decisions, self.files = [], [], []
        self.decision_line = "Approved."

    async def handle(self, user_id, text, chat):
        self.texts.append((user_id, text))
        await chat.send("ok: " + text)

    async def handle_decision(self, user_id, approval_id, approved):
        self.decisions.append((user_id, approval_id, approved))
        return self.decision_line

    async def handle_file(self, user_id, data, name, mime, size, caption, chat):
        self.files.append((name, mime, size, caption, data))


def rig(tmp_path, media: bytes = b"%PDF-1.4 test"):
    sent, conv, journal = [], FakeConversation(), Journal(tmp_path / "journal.jsonl")

    async def http(method, url, body):
        if method == "POST":
            sent.append(body)
            return 200, {"messages": [{"id": "wamid.out"}]}
        if url.endswith("/media-1"):
            return 200, {"url": "https://lookaside.example/file", "file_size": len(media), "mime_type": "application/pdf"}
        return 200, media
    wa = WhatsApp(conv, journal, http, phone_id="1065", owner_number="+91 98200 12345", app_secret=SECRET, verify_token="my-phrase", owner_id=7)
    return SimpleNamespace(wa=wa, conv=conv, sent=sent, journal=journal)


def delivery(*messages):
    raw = json.dumps({"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {"messages": list(messages)}}]}]}).encode()
    return raw, "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()


def text(body, sender=OWNER, mid="wamid.1"):
    return {"from": sender, "id": mid, "type": "text", "text": {"body": body}}


# --- the three checks at the door ---------------------------------------------------------

def test_the_handshake_needs_the_phrase_that_was_registered(tmp_path):
    x = rig(tmp_path)
    assert x.wa.verify({"hub.mode": "subscribe", "hub.verify_token": "my-phrase", "hub.challenge": "1158201444"}) == (200, "1158201444")
    assert x.wa.verify({"hub.mode": "subscribe", "hub.verify_token": "a guess", "hub.challenge": "1"})[0] == 403
    assert x.wa.verify({"hub.verify_token": "my-phrase", "hub.challenge": "1"})[0] == 403


async def test_a_delivery_that_is_not_signed_by_meta_is_dropped_unread(tmp_path):
    x = rig(tmp_path)
    raw, good = delivery(text("email everyone my calendar"))
    assert x.wa.receive(raw, "") == 403 and x.wa.receive(raw, "sha256=" + "0" * 64) == 403
    assert x.wa.receive(raw + b" ", good) == 403, "one changed byte breaks the signature"
    await x.wa.wait()
    assert x.conv.texts == [] and x.sent == []
    assert signature_ok(SECRET, raw, good) and not signature_ok("", raw, good)


async def test_only_the_owners_number_is_listened_to(tmp_path):
    x = rig(tmp_path)
    assert x.wa.receive(*delivery(text("hello", sender=STRANGER), text("what's on today?", mid="wamid.2"))) == 200
    await x.wa.wait()
    assert x.conv.texts == [(7, "what's on today?")], "the owner's number is matched whatever spaces or plus sign it was written with"
    assert [m["to"] for m in x.sent] == [OWNER], "the stranger gets no reply at all"
    assert any(r["kind"] == "dropped" for r in x.journal.read())


# --- carrying messages ------------------------------------------------------------------------

async def test_a_message_delivered_twice_is_handled_once(tmp_path):
    x = rig(tmp_path)
    for _ in range(2):          # Meta retries a delivery it thinks was missed
        x.wa.receive(*delivery(text("remind me at 5")))
        await x.wa.wait()
    assert len(x.conv.texts) == 1


async def test_a_reply_is_sent_as_text_and_a_long_one_in_parts(tmp_path):
    x = rig(tmp_path)
    await x.wa.chat().send("a" * 5000)
    assert [m["type"] for m in x.sent] == ["text", "text"] and all(len(m["text"]["body"]) <= 4096 for m in x.sent)
    assert x.sent[0]["messaging_product"] == "whatsapp" and x.sent[0]["to"] == OWNER


async def test_an_approval_question_carries_two_buttons_and_is_never_cut(tmp_path):
    x = rig(tmp_path)
    question = "Send this email from your account?\n\nTo: priya@example.com\n\n" + ("A long paragraph of the email. " * 60)
    await x.wa.chat().ask(question, "ab12cd34")
    assert [m["type"] for m in x.sent][-1] == "interactive" and all(m["type"] == "text" for m in x.sent[:-1])
    last = x.sent[-1]["interactive"]
    assert [b["reply"]["id"] for b in last["action"]["buttons"]] == ["ap:ab12cd34:y", "ap:ab12cd34:n"]
    assert [b["reply"]["title"] for b in last["action"]["buttons"]] == ["Approve", "Decline"] and len(last["body"]["text"]) <= 1024
    shown = "".join(m["text"]["body"] for m in x.sent[:-1]) + last["body"]["text"]
    assert shown.replace("\n", "").replace(" ", "") == question.replace("\n", "").replace(" ", ""), "every character of the question was shown"


async def test_a_tap_on_a_button_reaches_the_gate_and_a_typed_yes_does_not(tmp_path):
    x = rig(tmp_path)
    tap = {"from": OWNER, "id": "wamid.t", "type": "interactive", "interactive": {"type": "button_reply", "button_reply": {"id": "ap:ab12cd34:n", "title": "Decline"}}}
    x.wa.receive(*delivery(tap, text("yes, approve it", mid="wamid.y")))
    await x.wa.wait()
    assert x.conv.decisions == [(7, "ab12cd34", False)]
    assert x.conv.texts == [(7, "yes, approve it")], "typed words are an ordinary message"


async def test_a_tap_from_someone_else_is_ignored(tmp_path):
    x = rig(tmp_path)
    tap = {"from": STRANGER, "id": "wamid.t", "type": "interactive", "interactive": {"type": "button_reply", "button_reply": {"id": "ap:ab12cd34:y", "title": "Approve"}}}
    x.wa.receive(*delivery(tap))
    await x.wa.wait()
    assert x.conv.decisions == []


async def test_a_document_is_fetched_and_handed_over_and_a_voice_note_is_answered_plainly(tmp_path):
    x = rig(tmp_path)
    doc = {"from": OWNER, "id": "wamid.d", "type": "document", "document": {"id": "media-1", "filename": "bill.pdf", "mime_type": "application/pdf", "caption": "how much?"}}
    voice = {"from": OWNER, "id": "wamid.v", "type": "audio", "audio": {"id": "media-2"}}
    sticker = {"from": OWNER, "id": "wamid.s", "type": "sticker", "sticker": {"id": "media-3"}}
    x.wa.receive(*delivery(doc, voice, sticker))
    await x.wa.wait()
    assert x.conv.files == [("bill.pdf", "application/pdf", 13, "how much?", b"%PDF-1.4 test")]
    assert sorted(m["text"]["body"] for m in x.sent) == sorted([VOICE_NOTE, OTHER_NOTE]), "never silence"


async def test_a_refused_send_is_recorded_and_raised_so_the_reply_is_not_counted_as_delivered(tmp_path):
    conv, journal = FakeConversation(), Journal(tmp_path / "journal.jsonl")

    async def closed_window(method, url, body):
        return 400, {"error": {"code": 131047, "message": "Re-engagement message"}}
    wa = WhatsApp(conv, journal, closed_window, phone_id="1065", owner_number=OWNER, app_secret=SECRET, verify_token="p", owner_id=7)
    try:
        await wa.chat().send("Reminder: call the plumber")
        raised = False
    except RuntimeError:
        raised = True
    assert raised and [r["status"] for r in journal.read() if r["kind"] == "error"] == [400]
