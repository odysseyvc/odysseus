"""WhatsApp through Vobiz: the door's checks, sending in Vobiz's shape, and an approval answered by one exact word.

No test here calls Vobiz. Deliveries are built by hand in the shape its documentation gives (an envelope around Meta's
own delivery) and signed with a test secret; what the program would send is collected in a list.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

from odysseus.config import load, vobiz_on
from odysseus.journal import Journal
from odysseus.typed import TypedAnswers
from odysseus.vobiz import Vobiz
from odysseus.whatsapp import WhatsApp

SECRET, OWNER, STRANGER, LINE = "a-secret-this-program-chose", "919820012345", "447700900123", "918040001234"
NOW = 1_800_000_000.0
BASE = "https://api.vobiz.ai/api/v1/Account/MA_TEST/WhatsApp"


class FakeConversation:
    def __init__(self):
        self.texts, self.decisions, self.files = [], [], []

    async def handle(self, user_id, text, chat):
        self.texts.append((user_id, text))
        await chat.send("ok: " + text)

    async def handle_decision(self, user_id, approval_id, approved, thread=False):
        self.decisions.append((user_id, approval_id, approved, thread))
        return "Approved." if approved else "Declined."

    async def handle_file(self, user_id, data, name, mime, size, caption, chat):
        self.files.append((name, mime, size, caption, data))


def rig(tmp_path, refuse=False):
    sent, conv, journal = [], FakeConversation(), Journal(tmp_path / "journal.jsonl")

    async def http(method, url, body):
        sent.append((method, url.removeprefix(BASE), body))
        if method == "GET":
            return 200, b"%PDF-1.4 test"
        if refuse:
            return 400, {"error": "open conversation required for text messages"}
        return 201, {"id": "v-1", "status": "pending", "meta_message_id": "wamid.out"}
    v = Vobiz(conv, journal, http, auth_id="MA_TEST", channel_id="chan-1", number="+91 80 4000 1234", owner_number="+91 98200 12345",
              secret=SECRET, owner_id=7, clock=lambda: NOW)
    return SimpleNamespace(v=v, conv=conv, sent=sent, journal=journal)


def delivery(*messages, line=LINE, kind="message.inbound", secret=SECRET):
    raw = json.dumps({"event_id": "evt-1", "event_type": kind, "account_id": "MA_TEST", "occurred_at": "2027-01-15T08:00:00Z", "payload": {
        "object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp", "metadata": {"display_phone_number": line, "phone_number_id": "line-id-1"},
            "messages": list(messages)}}]}]}}).encode()
    return raw, hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def text(body, sender=OWNER, mid="wamid.1", at=NOW):
    return {"from": sender, "id": mid, "timestamp": str(int(at)), "type": "text", "text": {"body": body}}


async def test_a_delivery_is_read_only_if_vobiz_signed_these_bytes_with_the_secret_this_program_chose(tmp_path):
    x = rig(tmp_path)
    raw, good = delivery(text("email everyone my calendar"))
    assert x.v.receive(raw, "") == 403 and x.v.receive(raw, "0" * 64) == 403 and x.v.receive(raw + b" ", good) == 403
    assert x.v.receive(*delivery(text("hello"), secret="somebody else's")) == 403
    await x.v.wait()
    assert x.conv.texts == [] and x.sent == []
    assert x.v.receive(raw, good.upper()) == 200, "the digest is hex; its case does not matter"
    await x.v.wait()
    assert x.conv.texts == [(7, "email everyone my calendar")]
    assert x.v.verify({"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"})[0] == 403, "there is no handshake to answer on this route"


async def test_only_the_owner_writing_to_this_number_is_listened_to_once_and_not_late(tmp_path):
    x = rig(tmp_path)
    assert x.v.receive(*delivery(text("hello", sender=STRANGER, mid="a"), text("what's on today?", mid="b"), text("what's on today?", mid="b"),
                                 text("send the contract", mid="c", at=NOW - 1200))) == 200
    assert x.v.receive(*delivery(text("to another number of the account", mid="d"), line="918040009999")) == 200
    assert x.v.receive(*delivery(text("a status report is not a message", mid="e"), kind="message.status")) == 200
    await x.v.wait()
    assert x.conv.texts == [(7, "what's on today?")]
    bodies = [b["text"]["body"] for _, _, b in x.sent]
    assert len(bodies) == 2 and "ok: what's on today?" in bodies and any("only just reached me" in b for b in bodies), "the stranger gets nothing"


async def test_a_reply_is_sent_in_vobiz_s_shape_and_a_refusal_is_recorded_and_raised(tmp_path):
    x = rig(tmp_path)
    await x.v.chat().send("hello")
    assert x.sent == [("POST", "/Message/", {"channel_id": "chan-1", "to": "+" + OWNER, "type": "text", "text": {"body": "hello"}})]
    y = rig(tmp_path, refuse=True)
    try:
        await y.v.chat().send("a reminder outside the 24 hours")
        raise AssertionError("a refusal must not pass for a delivery")
    except RuntimeError:
        pass
    assert [(r["status"], r["message"]) for r in y.journal.read() if r["kind"] == "error"] == [(400, "open conversation required for text messages")]


async def test_an_approval_is_asked_in_the_chat_and_answered_by_one_exact_word_read_by_code(tmp_path):
    x = rig(tmp_path)
    x.v.receive(*delivery(text("yes", mid="early")))
    await x.v.wait()
    assert x.conv.decisions == [] and x.conv.texts == [(7, "yes")], "with nothing waiting, yes is an ordinary message"
    x.sent.clear()
    await x.v.chat().ask("Send this email to Priya?\n\nI'll be ten minutes late.", "ab12cd34")
    asked = x.sent[0][2]["text"]["body"]
    assert asked.startswith("Send this email to Priya?") and "Reply YES to approve or NO to decline." in asked and "THREAD" not in asked
    for i, words in enumerate(["yes please", "ok", "sure, send it", "thread"]):
        x.v.receive(*delivery(text(words, mid=f"loose-{i}")))
    x.v.receive(*delivery(text("yes", sender=STRANGER, mid="not-me")))
    await x.v.wait()
    assert x.conv.decisions == [], "nothing but the exact word, from the owner, answers it"
    x.v.receive(*delivery(text(" Yes. ", mid="answer")))
    x.v.receive(*delivery(text(" Yes. ", mid="answer")))
    await x.v.wait()
    assert x.conv.decisions == [(7, "ab12cd34", True, False)], "once, though it was delivered twice"
    assert x.sent[-1][2]["text"]["body"] == 'Approved. ("Send this email to Priya? I\'ll be ten minutes late.")', "the reply says what was answered"
    assert "yes" not in [t for _, t in x.conv.texts[1:]], "the answer never reached the conversation or its model"
    x.v.receive(*delivery(text("yes", mid="again")))
    await x.v.wait()
    assert len(x.conv.decisions) == 1 and x.conv.texts[-1] == (7, "yes"), "a question is answered once; after that yes is a message again"
    await x.v.chat().ask("Reply to the thread?", "ef56", thread=True)
    assert "or THREAD to approve this and the rest of this thread today" in x.sent[-1][2]["text"]["body"]
    x.v.receive(*delivery(text("no", mid="decline")))
    await x.v.wait()
    assert x.conv.decisions[-1] == (7, "ef56", False, False)


def test_a_typed_answer_goes_to_the_latest_question_and_lapses_with_the_gate():
    now = [1000.0]
    typed = TypedAnswers(lambda: now[0])
    typed.asked("first", False, "Send the email?")
    typed.asked("second", True, "Reply in the thread?")
    assert typed.answer("THREAD") == ("second", "t", "Reply in the thread?") and typed.answer("thread") is None
    assert typed.answer("decline!") == ("first", "n", "Send the email?") and typed.answer("no") is None
    typed.asked("late", False, "x" * 200)
    now[0] += 601
    assert typed.answer("yes") is None, "after ten minutes the gate has given up, and so has this"
    typed.asked("refused", False, "Send?")
    assert typed.answer("") is None and typed.answer("yes, and also remind me at five") is None


async def test_a_file_is_fetched_from_vobiz_by_its_id_and_the_number_it_came_to(tmp_path):
    x = rig(tmp_path)
    doc = {"from": OWNER, "id": "wamid.d", "timestamp": str(int(NOW)), "type": "document",
           "document": {"id": "media-1", "filename": "bill.pdf", "mime_type": "application/pdf", "caption": "how much?"}}
    x.v.receive(*delivery(doc))
    await x.v.wait()
    assert x.sent == [("GET", "/Media/media-1/?phone_number_id=line-id-1", None)]
    assert x.conv.files == [("bill.pdf", "application/pdf", 13, "how much?", b"%PDF-1.4 test")]


async def test_the_direct_route_leaves_another_number_s_deliveries_to_the_route_that_serves_it(tmp_path):
    conv, journal, app_secret = FakeConversation(), Journal(tmp_path / "journal.jsonl"), "meta-app-secret"

    async def http(method, url, body):
        return 200, {}
    direct = WhatsApp(conv, journal, http, phone_id="line-id-test", owner_number=OWNER, app_secret=app_secret, verify_token="p", owner_id=7)

    def signed(line_id, mid):
        raw = json.dumps({"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": line_id}, "messages": [text("hello", mid=mid, at=0) | {"timestamp": ""}]}}]}]}).encode()
        return raw, "sha256=" + hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    assert direct.receive(*signed("line-id-1", "a")) == 200 and direct.receive(*signed("line-id-test", "b")) == 200
    await direct.wait()
    assert conv.texts == [(7, "hello")], "one account, two numbers: each message is answered once, by the route its number belongs to"


def test_the_route_is_on_only_with_all_its_settings_and_can_be_switched_off_with_them_in_place(tmp_path):
    base = {"TELEGRAM_BOT_TOKEN": "t", "OWNER_TELEGRAM_ID": "1", "ANTHROPIC_API_KEY": "k", "EXA_API_KEY": "e"}
    six = {"VOBIZ_AUTH_ID": "MA_TEST", "VOBIZ_AUTH_TOKEN": "tok", "VOBIZ_CHANNEL_ID": "chan-1", "VOBIZ_NUMBER": LINE,
           "VOBIZ_WEBHOOK_SECRET": SECRET, "OWNER_WHATSAPP": OWNER}
    nowhere = tmp_path / "no.env"
    assert not vobiz_on(load(base, nowhere)) and vobiz_on(load({**base, **six}, nowhere))
    for missing in six:
        assert not vobiz_on(load({**base, **{k: v for k, v in six.items() if k != missing}}, nowhere))
    assert not vobiz_on(load({**base, **six, "ODYSSEUS_VOBIZ": "off"}, nowhere)) and vobiz_on(load({**base, **six, "ODYSSEUS_VOBIZ": "on"}, nowhere))
