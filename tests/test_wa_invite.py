"""Joining on WhatsApp: an invitation that opens the chat with the code already written, and each person on the one
number heard and answered by their own assistant.

No test calls WhatsApp. Deliveries are built by hand and signed with a test secret; numbers and names are made up.
"""
from __future__ import annotations

from types import SimpleNamespace

from odysseus import people as people_mod
from odysseus.journal import Journal
from odysseus.people import House, People, wa_id, wa_link, wa_number
from odysseus.vobiz import Vobiz

from test_people import Chat, assistant
from test_vobiz import LINE, NOW, OWNER, SECRET, delivery, text

HOST, GUEST, OTHER = 7, "447700900123", "447700900999"


def rig(tmp_path):
    sent, journal = [], Journal(tmp_path / "journal.jsonl")
    people = People(tmp_path / "people.json", clock=lambda: NOW)
    built = {}

    async def assemble(user_id):
        built[user_id] = assistant(links=None)
        return built[user_id]

    async def link(code):
        return "https://t.me/thebot?start=" + code
    house = House(HOST, people, journal, assemble, link, clock=lambda: NOW)
    house.wa_link = lambda code: wa_link(LINE, code)
    host = assistant()
    house.add(HOST, host)

    async def post(method, url, body):
        sent.append(((body or {}).get("to"), ((body or {}).get("text") or {}).get("body", "")))
        return 201, {"id": "v-1"}
    v = Vobiz(host.conversation, journal, post, auth_id="MA_TEST", channel_id="chan-1", number=LINE, owner_number=OWNER,
              secret=SECRET, owner_id=HOST, clock=lambda: NOW, people=lambda: house)
    return SimpleNamespace(v=v, house=house, people=people, host=host, built=built, sent=sent, journal=journal)


async def code_for(x):
    chat = Chat()
    await x.house.handle(HOST, "/invite whatsapp Priya", chat)
    return chat.sent[-1].rsplit("join%20", 1)[1].strip()


def test_a_whatsapp_person_has_an_id_no_telegram_person_can_have():
    assert wa_id("+44 7700 900123") == people_mod.WA_BASE + 447700900123 > 2 ** 52
    assert wa_number(wa_id(GUEST)) == GUEST and wa_number(123456789) == "" and wa_id("") == 0 and wa_id("12") == 0
    assert wa_link("+91 80 4000 1234", "abc_DEF-123") == "https://wa.me/918040001234?text=join%20abc_DEF-123"


async def test_an_invitation_is_for_the_chat_it_was_asked_in_and_never_both(tmp_path):
    x = rig(tmp_path)
    chat = Chat()
    await x.house.handle(HOST, "/invite Priya", chat)                 # asked on Telegram
    assert "https://t.me/thebot?start=" in chat.sent[-1] and "wa.me" not in chat.sent[-1] and "tap Start" in chat.sent[-1]
    await x.house.handle(HOST, "/invite whatsapp Priya", chat)        # the other one, by name
    assert f"https://wa.me/{LINE}?text=join%20" in chat.sent[-1] and "t.me" not in chat.sent[-1] and "works once" in chat.sent[-1]
    assert [v["note"] for v in x.people.invites.values()] == ["Priya", "Priya"], "the word naming the chat is not part of the note"
    assert x.v.receive(*delivery(text("/invite Asha", mid="w1"))) == 200          # asked on WhatsApp
    await x.v.wait()
    said = [body for to, body in x.sent if to == "+" + OWNER][-1]
    assert f"https://wa.me/{LINE}?text=join%20" in said and "t.me" not in said
    x.house.wa_link = None
    await x.house.handle(HOST, "/invite whatsapp Priya", chat)
    assert "no WhatsApp number" in chat.sent[-1] and len(x.people.invites) == 3, "no code is spent on a link that cannot be made"


async def test_the_host_can_invite_from_whatsapp_itself(tmp_path):
    x = rig(tmp_path)
    assert x.v.receive(*delivery(text("/invite Priya"))) == 200
    await x.v.wait()
    assert any(to == "+" + OWNER and "join%20" in body for to, body in x.sent)
    assert x.host.conversation.heard == [], "a command of the host's reaches no model"


async def test_a_stranger_with_the_code_joins_and_is_then_heard_by_their_own_assistant(tmp_path):
    x = rig(tmp_path)
    code = await code_for(x)
    joining = text("join " + code, sender=GUEST, mid="j1")
    raw, sig = delivery(joining)
    assert x.v.receive(raw, sig) == 200
    await x.v.wait()
    uid = wa_id(GUEST)
    assert x.people.has(uid) and uid in x.built
    to_guest = [body for to, body in x.sent if to == "+" + GUEST]
    assert to_guest and to_guest[0].startswith("You're in.") and "telephone company" in to_guest[0], "told who else carries their messages"
    assert any("joined with one of your invitations" in t for t in x.host.notify.sent)
    assert x.v.receive(*delivery(text("what is on tomorrow?", sender=GUEST, mid="m2"), text("and mine?", mid="m3"))) == 200
    await x.v.wait()
    assert x.built[uid].conversation.heard == [("text", uid, "what is on tomorrow?")]
    assert x.host.conversation.heard == [("text", HOST, "and mine?")], "each on the one number reaches only their own assistant"
    # the same code again, from someone else, is nothing
    assert x.v.receive(*delivery(text("join " + code, sender=OTHER, mid="j4"))) == 200
    await x.v.wait()
    assert not x.people.has(wa_id(OTHER)) and not [1 for to, _ in x.sent if to == "+" + OTHER]


async def test_a_stranger_without_a_code_gets_nothing_and_guessing_is_bounded(tmp_path):
    x = rig(tmp_path)
    await code_for(x)
    tries = [text("join " + "x" * 22, sender=OTHER, mid=f"g{i}") for i in range(9)]
    assert x.v.receive(*delivery(text("hello, who is this?", sender=OTHER, mid="h"), text("join", sender=OTHER, mid="h2"), *tries)) == 200
    await x.v.wait()
    assert x.sent == [] and not x.people.has(wa_id(OTHER)) and len(x.people.invites) == 1
    refused = [r for r in x.journal.read() if r.get("kind") == "invite" and r.get("outcome") == "refused"]
    assert len(refused) == 9


async def test_a_word_answers_only_the_question_asked_of_that_person(tmp_path):
    x = rig(tmp_path)
    code = await code_for(x)
    assert x.v.receive(*delivery(text("join " + code, sender=GUEST, mid="j1"))) == 200
    await x.v.wait()
    uid = wa_id(GUEST)
    await x.v.chat(GUEST).ask("Send this email to Priya?", "ab12cd34")
    assert x.v.receive(*delivery(text("yes", mid="o1"))) == 200          # the host's "yes" is not the guest's
    await x.v.wait()
    assert ("decision", uid, "ab12cd34") not in x.built[uid].conversation.heard
    assert x.host.conversation.heard == [("text", HOST, "yes")]
    assert x.v.receive(*delivery(text("yes", sender=GUEST, mid="g1"))) == 200
    await x.v.wait()
    assert x.built[uid].conversation.heard == [("decision", uid, "ab12cd34")]


async def test_without_the_house_the_number_hears_only_the_owner_as_before(tmp_path):
    x = rig(tmp_path)
    code = await code_for(x)
    x.v._people = None
    assert x.v.receive(*delivery(text("join " + code, sender=GUEST, mid="j1"))) == 200
    await x.v.wait()
    assert x.sent == [] and not x.people.has(wa_id(GUEST))


def test_the_invitation_opens_the_route_that_knows_its_number_whichever_was_set_up_first():
    """Both routes were set up on the server. The direct one came first and knows no number, so no link could be made."""
    from types import SimpleNamespace
    from odysseus.__main__ import line_with_a_number
    direct, rented = SimpleNamespace(number=""), SimpleNamespace(number="918040001234")
    assert line_with_a_number(rented, direct) is rented and line_with_a_number(direct, rented) is rented
    assert line_with_a_number(None, direct) is None and line_with_a_number(None, None) is None
