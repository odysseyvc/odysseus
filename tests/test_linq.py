"""iMessage through Linq: the door's checks, carrying messages, and an approval that is a vote in a poll.

No test here calls Linq. Deliveries are built by hand in the shape Linq's documentation gives (webhook version
2026-02-03) and signed with a test secret; what the program would send is collected in a list.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from types import SimpleNamespace

from odysseus.config import imessage_on, load
from odysseus.journal import Journal
from odysseus.linq import API, Linq, signature_ok

KEY = b"a-signing-key-for-tests-only-32b"
SECRET = "whsec_" + base64.b64encode(KEY).decode()
LINE, OWNER, STRANGER = "+12025550100", "+919820012345", "+447700900123"
NOW = 1_800_000_000.0


class FakeConversation:
    def __init__(self):
        self.texts, self.decisions, self.files, self.voices = [], [], [], []

    async def handle(self, user_id, text, chat):
        self.texts.append((user_id, text))
        await chat.send("ok: " + text)

    async def handle_decision(self, user_id, approval_id, approved, thread=False):
        self.decisions.append((user_id, approval_id, approved, thread))
        return "Approved." if approved else "Declined."

    async def handle_file(self, user_id, data, name, mime, size, caption, chat):
        self.files.append((name, mime, size, caption, data))

    async def handle_voice(self, user_id, data, name, size, seconds, chat):
        self.voices.append((name, size, data))


def rig(tmp_path, polls=True, state=True):
    sent, conv, journal = [], FakeConversation(), Journal(tmp_path / "journal.jsonl")

    async def http(method, url, body):
        sent.append((method, url.removeprefix(API), body))
        if url.endswith("/polls"):
            if not polls:
                return 400, {"success": False, "error": {"status": 400, "code": 1005, "message": "polls need iMessage"}}
            return 202, {"chat_id": "chat-1", "message_id": "poll-1",
                         "poll": {"options": [{"option_id": f"opt-{i}", "text": o["text"]} for i, o in enumerate(body["poll"]["options"])]}}
        if url.endswith("/chats"):
            return 200, {"chat": {"id": "chat-new", "message": {"id": "m"}}}
        return 200, {"chat_id": "chat-1", "message": {"id": "m"}}

    async def fetch(url, most):
        return b"%PDF-1.4 test" if url.startswith("https://cdn.") else None
    linq = Linq(conv, journal, http, number=LINE, owner_number="+91 98200 12345", secret=SECRET, owner_id=7,
                state_file=(tmp_path / "linq.json") if state else None, fetch=fetch, clock=lambda: NOW)
    return SimpleNamespace(linq=linq, conv=conv, sent=sent, journal=journal)


def delivery(data, kind="message.received", event_id="evt-1", at=NOW, key=KEY):
    raw = json.dumps({"api_version": "v3", "webhook_version": "2026-02-03", "event_type": kind, "event_id": event_id, "data": data}).encode()
    stamp = str(int(at))
    signature = base64.b64encode(hmac.new(key, f"{event_id}.{stamp}.".encode() + raw, hashlib.sha256).digest()).decode()
    return raw, {"webhook-id": event_id, "webhook-timestamp": stamp, "webhook-signature": "v1," + signature}


def message(parts, sender=OWNER, mid="msg-1", sent_at="2027-01-15T08:00:00Z", group=False):
    return {"chat": {"id": "chat-1", "is_group": group, "owner_handle": {"handle": LINE, "is_me": True}}, "id": mid, "direction": "inbound",
            "sender_handle": {"handle": sender, "is_me": False}, "parts": parts, "sent_at": sent_at, "service": "iMessage"}


def text(body, **more):
    return message([{"type": "text", "value": body}], **more)


# --- the checks at the door -----------------------------------------------------------------------

async def test_a_delivery_is_read_only_if_linq_signed_these_bytes_within_five_minutes(tmp_path):
    x = rig(tmp_path)
    raw, good = delivery(text("email everyone my calendar"))
    assert x.linq.receive(raw, {}) == 403 and x.linq.receive(raw, {**good, "webhook-signature": "v1," + "A" * 44}) == 403
    assert x.linq.receive(raw + b" ", good) == 403, "one changed byte breaks the signature"
    assert x.linq.receive(*delivery(text("hello"), key=b"somebody else's key, of any size")) == 403
    assert x.linq.receive(*delivery(text("hello"), at=NOW - 301)) == 403, "a recorded delivery cannot be played again later"
    await x.linq.wait()
    assert x.conv.texts == [] and x.sent == []
    assert [r["count"] for r in x.journal.read() if r["kind"] == "dropped"] == [1], "bad posts are counted, not logged one by one"
    stamp = good["webhook-timestamp"]
    assert signature_ok(SECRET, raw, "evt-1", stamp, "v1,wrong " + good["webhook-signature"], NOW), "any one of several signatures may match"
    assert not signature_ok("", raw, "evt-1", stamp, good["webhook-signature"], NOW), "no secret: nothing is accepted"
    assert not signature_ok("whsec_not base64!", raw, "evt-1", stamp, good["webhook-signature"], NOW)


async def test_only_the_owners_number_in_a_chat_of_two_is_listened_to(tmp_path):
    x = rig(tmp_path)
    for i, d in enumerate([text("hello", sender=STRANGER, mid="a"), text("in a group", mid="b", group=True),
                           {**text("my own echo", mid="c"), "direction": "outbound"},
                           {**text("another line's", mid="d"), "chat": {"id": "chat-9", "owner_handle": {"handle": "+12025550199"}}},
                           text("what's on today?", mid="e")]):
        assert x.linq.receive(*delivery(d, event_id=f"evt-{i}")) == 200
    await x.linq.wait()
    assert x.conv.texts == [(7, "what's on today?")], "the owner's number is matched whatever spaces or plus sign it was written with"
    assert len(x.sent) == 1, "nobody else gets a reply at all"


async def test_a_delivery_made_twice_is_handled_once_even_under_a_new_event_id(tmp_path):
    x = rig(tmp_path)
    x.linq.receive(*delivery(text("remind me at 5"), event_id="evt-1"))
    x.linq.receive(*delivery(text("remind me at 5"), event_id="evt-1"))
    x.linq.receive(*delivery(text("remind me at 5"), event_id="evt-replayed"))
    await x.linq.wait()
    assert x.conv.texts == [(7, "remind me at 5")]


async def test_a_message_that_arrives_late_is_reported_once_and_not_acted_on(tmp_path):
    x = rig(tmp_path)
    old = "2027-01-15T07:40:00Z"        # twenty minutes before NOW
    assert int(NOW) == 1_800_000_000 and old < "2027-01-15T08:00:00Z"
    x.linq.receive(*delivery(text("send the contract", mid="a", sent_at=old), event_id="e1"))
    x.linq.receive(*delivery(text("and the invoice", mid="b", sent_at=old), event_id="e2"))
    await x.linq.wait()
    assert x.conv.texts == [] and len(x.sent) == 1 and "only just reached me" in x.sent[0][2]["message"]["parts"][0]["value"]


# --- carrying messages ------------------------------------------------------------------------------

async def test_a_reply_goes_into_the_chat_the_owner_opened_and_that_chat_is_remembered(tmp_path):
    x = rig(tmp_path)
    x.linq.receive(*delivery(text("hello")))
    await x.linq.wait()
    assert x.sent == [("POST", "/chats/chat-1/messages", {"message": {"parts": [{"type": "text", "value": "ok: hello"}]}})]
    assert rig(tmp_path).linq.chat_id == "chat-1", "after a restart the chat is still known"
    await x.linq.chat().send("x" * 9000)
    assert [len(b["message"]["parts"][0]["value"]) <= 4000 for _, _, b in x.sent[1:]] == [True] * 3, "a long reply goes in parts"


async def test_with_no_chat_known_the_one_between_this_line_and_the_owner_is_opened(tmp_path):
    x = rig(tmp_path, state=False)
    await x.linq.chat().send("hello")
    assert x.sent == [("POST", "/chats", {"from": LINE, "to": [OWNER], "message": {"parts": [{"type": "text", "value": "hello"}]}})]
    assert x.linq.chat_id == "chat-new"


async def test_a_file_is_fetched_and_handed_over_with_its_caption_and_a_recording_as_a_voice_note(tmp_path):
    x = rig(tmp_path)
    doc = {"type": "media", "url": "https://cdn.example/f", "filename": "bill.pdf", "mime_type": "application/pdf", "size_bytes": 13}
    x.linq.receive(*delivery(message([{"type": "text", "value": "how much?"}, doc], mid="a"), event_id="e1"))
    x.linq.receive(*delivery(message([{"type": "media", "url": "https://cdn.example/v", "filename": "note.m4a", "mime_type": "audio/mp4", "size_bytes": 13}], mid="b"), event_id="e2"))
    x.linq.receive(*delivery(message([{**doc, "size_bytes": 10**9}], mid="c"), event_id="e3"))
    await x.linq.wait()
    assert x.conv.files == [("bill.pdf", "application/pdf", 13, "how much?", b"%PDF-1.4 test"), ("bill.pdf", "application/pdf", 10**9, "", None)]
    assert x.conv.voices == [("note.m4a", 13, b"%PDF-1.4 test")] and x.conv.texts == []


async def test_a_refused_send_is_recorded_and_raised_so_the_reply_is_not_counted_as_delivered(tmp_path):
    journal = Journal(tmp_path / "journal.jsonl")

    async def sandbox(method, url, body):
        return 403, {"success": False, "error": {"status": 403, "code": 2008, "message": "Recipient not allowed"}}
    linq = Linq(FakeConversation(), journal, sandbox, number=LINE, owner_number=OWNER, secret=SECRET, owner_id=7)
    try:
        await linq.chat().send("a reminder nobody asked this channel for")
        raise AssertionError("a refusal must not pass for a delivery")
    except RuntimeError:
        pass
    assert [(r["status"], r["code"]) for r in journal.read() if r["kind"] == "error"] == [(403, 2008)]


# --- approving ----------------------------------------------------------------------------------------

async def test_an_approval_is_a_vote_in_a_poll_and_a_typed_yes_is_only_a_message(tmp_path):
    x = rig(tmp_path)
    x.linq.receive(*delivery(text("hello")))
    await x.linq.wait()
    x.sent.clear()
    await x.linq.chat().ask("Send this email to Priya?\n\nI'll be ten minutes late.", "ab12cd34", thread=True)
    assert [u for _, u, _ in x.sent] == ["/chats/chat-1/messages", "/chats/chat-1/polls"], "the question comes first, whole, then the poll"
    assert [o["text"] for o in x.sent[1][2]["poll"]["options"]] == ["Approve", "Decline", "Approve, and this thread today"]
    vote = {"chat": {"id": "chat-1", "is_group": False}, "message_id": "poll-1", "direction": "inbound",
            "sender_handle": {"handle": OWNER, "is_me": False}, "option_id": "opt-1", "service": "iMessage"}
    x.linq.receive(*delivery({**vote, "sender_handle": {"handle": STRANGER}}, kind="poll.vote.added", event_id="v0"))
    x.linq.receive(*delivery({**vote, "message_id": "some-other-poll"}, kind="poll.vote.added", event_id="v1"))
    x.linq.receive(*delivery(text("yes", mid="typed"), event_id="v2"))
    await x.linq.wait()
    assert x.conv.decisions == [] and x.conv.texts[-1] == (7, "yes"), "nothing but a vote in the poll that was sent answers it"
    x.linq.receive(*delivery(vote, kind="poll.vote.added", event_id="v3"))
    x.linq.receive(*delivery({**vote, "option_id": "opt-0"}, kind="poll.vote.added", event_id="v4"))
    await x.linq.wait()
    assert x.conv.decisions == [(7, "ab12cd34", False, False)], "the second option is Decline, and a poll answers once"
    y = rig(tmp_path)
    await y.linq.chat().ask("Send it?", "ef56", thread=True)
    y.linq.receive(*delivery({**vote, "option_id": "opt-2"}, kind="poll.vote.added", event_id="v5"))
    await y.linq.wait()
    assert y.conv.decisions == [(7, "ef56", True, True)]


async def test_where_a_poll_cannot_be_made_the_question_is_answered_by_one_exact_word_in_the_same_chat(tmp_path):
    x = rig(tmp_path, polls=False)
    x.linq.receive(*delivery(text("hello")))
    await x.linq.wait()
    await x.linq.chat().ask("Send this email to Priya?", "ab12cd34")
    said = [b["message"]["parts"][0]["value"] for _, u, b in x.sent if u.endswith("/messages")]
    assert said[-2:] == ["Send this email to Priya?", "Reply YES to approve or NO to decline. Any other reply is an ordinary message and approves nothing."]
    x.linq.receive(*delivery(text("yes please", mid="loose"), event_id="e2"))
    await x.linq.wait()
    assert x.conv.decisions == [] and x.conv.texts[-1] == (7, "yes please")
    x.linq.receive(*delivery(text("No", mid="answer"), event_id="e3"))
    await x.linq.wait()
    assert x.conv.decisions == [(7, "ab12cd34", False, False)] and x.conv.texts[-1] == (7, "yes please")
    assert x.sent[-1][2]["message"]["parts"][0]["value"] == 'Declined. ("Send this email to Priya?")'


# --- the choice -----------------------------------------------------------------------------------------

def test_the_channel_is_on_only_with_all_four_settings_and_can_be_switched_off_with_them_in_place(tmp_path):
    base = {"TELEGRAM_BOT_TOKEN": "t", "OWNER_TELEGRAM_ID": "1", "ANTHROPIC_API_KEY": "k", "EXA_API_KEY": "e"}
    four = {"LINQ_API_TOKEN": "tok", "LINQ_NUMBER": LINE, "LINQ_WEBHOOK_SECRET": SECRET, "OWNER_IMESSAGE": OWNER}
    nowhere = tmp_path / "no.env"
    assert not imessage_on(load(base, nowhere)) and imessage_on(load({**base, **four}, nowhere))
    for missing in four:
        assert not imessage_on(load({**base, **{k: v for k, v in four.items() if k != missing}}, nowhere))
    for word in ("off", "0", "no", "OFF"):
        assert not imessage_on(load({**base, **four, "ODYSSEUS_IMESSAGE": word}, nowhere))
    assert imessage_on(load({**base, **four, "ODYSSEUS_IMESSAGE": "on"}, nowhere))
