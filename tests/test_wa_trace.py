"""The record of what came and went on WhatsApp: one line for each message in, each message out, and each report of
what became of one that was sent. None of them holds a word of any message.

Deliveries are built by hand, as in test_vobiz.py, whose rig this uses. Nothing here calls Vobiz or Meta.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

from odysseus.conversation import via_of
from odysseus.journal import Journal
from odysseus.whatsapp import WhatsApp, WhatsAppChat, sent_id

from test_vobiz import LINE, NOW, OWNER, SECRET, STRANGER, delivery, rig, text


def kinds(journal, kind):
    return [r for r in journal.read() if r.get("kind") == kind]


async def test_each_message_in_and_each_message_out_is_one_line_with_its_route_and_no_words(tmp_path):
    x = rig(tmp_path)
    assert x.v.receive(*delivery(text("what is on my calendar", at=NOW - 4))) == 200
    await x.v.wait()
    came, went = kinds(x.journal, "wa_in"), kinds(x.journal, "wa_out")
    assert [(r["route"], r["type"], r["chars"], r["heard"], r["age_s"]) for r in came] == [("vobiz", "text", 22, True, 4)]
    assert [(r["route"], r["type"], r["chars"], r["id"]) for r in went] == [("vobiz", "text", len("ok: what is on my calendar"), "wamid.out")]
    assert "calendar" not in json.dumps(came + went), "how long a message is, never what it says"


async def test_a_message_from_someone_who_is_not_heard_is_recorded_as_having_come_and_not_been_heard(tmp_path):
    x = rig(tmp_path)
    x.v.receive(*delivery(text("hello", sender=STRANGER)))
    await x.v.wait()
    assert [(r["heard"], r["chars"]) for r in kinds(x.journal, "wa_in")] == [(False, 5)]
    assert kinds(x.journal, "wa_out") == [] and x.conv.texts == []


async def test_a_message_the_line_refuses_is_an_error_and_not_a_message_out(tmp_path):
    x = rig(tmp_path, refuse=True)
    x.v.receive(*delivery(text("hello")))
    await x.v.wait()
    assert kinds(x.journal, "wa_out") == []
    assert [r["where"] for r in kinds(x.journal, "error")][:1] == ["vobiz.send"]


def report(*statuses, kind="message.status", flat=None):
    payload = flat if flat is not None else {"object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": LINE, "phone_number_id": "line-id-1"}, "statuses": list(statuses)}}]}]}
    raw = json.dumps({"event_id": "evt-2", "event_type": kind, "account_id": "MA_TEST", "payload": payload}).encode()
    return raw, hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()


async def test_a_report_that_a_message_reached_the_phone_or_failed_is_kept_and_sent_and_read_are_not(tmp_path):
    x = rig(tmp_path)
    assert x.v.receive(*report({"id": "wamid.out", "status": "sent"}, {"id": "wamid.out", "status": "delivered"}, {"id": "wamid.out", "status": "read"},
                               {"id": "wamid.two", "status": "failed", "errors": [{"code": 131047, "title": "Re-engagement message"}]})) == 200
    got = kinds(x.journal, "wa_status")
    assert [(r["route"], r["status"], r["id"]) for r in got] == [("vobiz", "delivered", "wamid.out"), ("vobiz", "failed", "wamid.two")]
    assert got[1]["code"] == 131047 and got[1]["why"] == "Re-engagement message" and "code" not in got[0]
    assert x.conv.texts == [] and x.sent == [], "a report is recorded and nothing is done about it"


async def test_a_report_in_a_form_this_program_does_not_know_keeps_only_its_kind_and_outcome(tmp_path):
    x = rig(tmp_path)
    x.v.receive(*report(kind="message.failed", flat={"message_id": "v-9", "status": "failed", "to": "+" + OWNER, "body": "private words"}))
    x.v.receive(*report(kind="message.status", flat={"message_id": "v-9", "status": "read"}))
    got = kinds(x.journal, "wa_status")
    assert [(r["event"], r["status"], r["id"]) for r in got] == [("message.failed", "failed", "v-9")]
    assert "private" not in json.dumps(got) and OWNER not in json.dumps(got)


async def test_the_direct_route_keeps_the_same_three_lines(tmp_path):
    sent, journal = [], Journal(tmp_path / "journal.jsonl")

    async def http(method, url, body):
        sent.append(body)
        return 200, {"messages": [{"id": "wamid.direct"}]}

    class Conv:
        async def handle(self, user_id, said, chat):
            await chat.send("ok")
    w = WhatsApp(Conv(), journal, http, phone_id="line-1", owner_number=OWNER, app_secret=SECRET, verify_token="v", owner_id=7, clock=lambda: NOW)
    raw = json.dumps({"entry": [{"changes": [{"field": "messages", "value": {"metadata": {"phone_number_id": "line-1"},
                     "statuses": [{"id": "wamid.direct", "status": "delivered"}], "messages": [text("hi")]}}]}]}).encode()
    assert w.receive(raw, "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()) == 200
    await w.wait()
    assert [(r["kind"], r["route"]) for r in journal.read() if r["kind"].startswith("wa_")] == [("wa_status", "whatsapp"), ("wa_in", "whatsapp"), ("wa_out", "whatsapp")]


def test_a_task_is_recorded_with_the_way_it_came():
    assert via_of(WhatsAppChat(None, "line", OWNER, None)) == "whatsapp"
    assert via_of(SimpleNamespace(route="vobiz", channel="whatsapp")) == "vobiz"
    assert via_of(SimpleNamespace()) == "telegram", "a chat that does not say is Telegram's"
    assert sent_id({"messages": [{"id": "wamid." + "x" * 40}]}) == "x" * 16 and sent_id(b"") == "" and sent_id({"id": "v-1"}) == "v-1"
