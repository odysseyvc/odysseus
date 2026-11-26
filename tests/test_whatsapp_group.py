"""The group's assistant in a WhatsApp group: what the door hands over, who is heard, what counts as spoken to, and an
approval answered by one exact word.

No test here calls Meta, and Meta has refused this number its Groups API, so nothing here has been checked against it.
Deliveries are built by hand in the shape Meta's documentation gives and signed with a test secret; what the program
would send is collected in a list.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

from odysseus.config import load, whatsapp_group_on
from odysseus.journal import Journal
from odysseus.vobiz import Vobiz
from odysseus.whatsapp import WhatsApp
from odysseus.whatsapp_group import CHAT_BASE, WhatsAppGroups, chat_id_of, members_from, spoken_to
from test_people import HOST, Chat, firm_rig, invite

SECRET, OWNER_NUMBER, TARA_NUMBER, STRANGER = "app-secret", "919820012345", "919811100022", "447700900123"
DIRECT_LINE, GROUP_LINE, GROUP, OTHER = "line-direct", "line-group", "120363000000000001", "120363000000000002"
NOW = 1_800_000_000.0


class FakeHouse:
    def __init__(self):
        self.heard, self.decisions = [], []

    async def handle_group(self, user_id, chat_id, text, chat, addressed=False):
        self.heard.append((user_id, chat_id, text, addressed))
        if addressed:
            await chat.send("ok: " + text)

    async def handle_group_decision(self, user_id, chat_id, approval_id, approved, thread=False):
        self.decisions.append((user_id, chat_id, approval_id, approved))
        return "Approved." if approved else "Declined."


class FakeConversation:
    def __init__(self):
        self.texts, self.decisions = [], []

    async def handle(self, user_id, text, chat):
        self.texts.append(text)

    async def handle_decision(self, user_id, approval_id, approved, thread=False):
        self.decisions.append((approval_id, approved))
        return "Approved."


def rig(tmp_path, house=None, group=GROUP, members=None, refuse=False):
    sent, told, journal = [], [], Journal(tmp_path / "journal.jsonl")
    state = tmp_path / "whatsapp_group.json"
    if group and not state.exists():
        state.write_text(json.dumps({"group_id": group, "subject": "Deal room"}), encoding="utf-8")

    async def http(method, url, body):
        sent.append((method, url, body))
        return (400, {"error": {"code": 131215}}) if refuse else (200, {"messages": [{"id": "wamid.out"}]})

    async def tell(line):
        told.append(line)
    house = house or FakeHouse()
    groups = WhatsAppGroups(house, journal, http, phone_id=GROUP_LINE, owner_number="+91 98200 12345", owner_id=HOST,
                            members=members if members is not None else {TARA_NUMBER: 7}, name="odysseus", tell=tell,
                            state_file=state, clock=lambda: NOW)
    conv = FakeConversation()
    door = WhatsApp(conv, journal, http, phone_id=DIRECT_LINE, owner_number="+91 98200 12345", app_secret=SECRET, verify_token="v",
                    owner_id=HOST, clock=lambda: NOW, groups=lambda: groups)
    return SimpleNamespace(door=door, groups=groups, house=house, conv=conv, sent=sent, told=told, journal=journal, state=state)


def said(body, sender=OWNER_NUMBER, group=GROUP, mid="wamid.1", age=0, user="", kind="text"):
    m = {"id": mid, "timestamp": str(int(NOW - age)), "type": kind, "from_user_id": user or ("IN." + sender if sender else "IN.unknown")}
    if sender:
        m["from"] = sender
    if group:
        m["group_id"] = group
    if kind == "text":
        m["text"] = {"body": body}
    return m


def signed(*changes, secret=SECRET):
    raw = json.dumps({"object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": list(changes)}]}).encode()
    return raw, "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def messages(*ms, line=GROUP_LINE):
    return {"field": "messages", "value": {"messaging_product": "whatsapp", "metadata": {"display_phone_number": "918040001234", "phone_number_id": line},
                                           "messages": list(ms)}}


async def deliver(x, *changes, secret=SECRET):
    status = x.door.receive(*signed(*changes, secret=secret))
    await x.door.wait()
    await x.groups.wait()
    return status


def texts(x):
    return [(b["to"], b["recipient_type"], b["text"]["body"]) for _, _, b in x.sent if b and b.get("type") == "text"]


def test_what_counts_as_spoken_to_is_read_from_the_text_alone():
    assert spoken_to("Odysseus, what do we know about Corvid?", "odysseus") == ("what do we know about Corvid?", True)
    assert spoken_to("  @odysseus what's their revenue", "odysseus") == ("what's their revenue", True)
    assert spoken_to("can @Odysseus check the deck?", "odysseus") == ("can check the deck?", True)
    assert spoken_to("lunch at one?", "odysseus") == ("lunch at one?", False)
    assert spoken_to("I asked odysseus yesterday", "odysseus")[1] is False, "the name in passing is talk about it, not to it"
    assert spoken_to("Odysseusly speaking", "odysseus")[1] is False and spoken_to("mail x@odysseus.example", "odysseus")[1] is False
    assert spoken_to("hello", "") == ("hello", False)
    assert spoken_to("odysseus", "odysseus") == ("", True)


def test_a_group_has_a_number_of_its_own_and_members_are_read_from_the_setting():
    assert chat_id_of(GROUP) == chat_id_of(GROUP) != chat_id_of(OTHER) and chat_id_of(GROUP) > CHAT_BASE
    assert members_from("+91 98111 00022=7, 447700900123 = 12 ; bad=x, =3, 55=") == {"919811100022": 7, "447700900123": 12}
    assert members_from("") == {}


async def test_the_host_and_a_listed_member_are_heard_and_a_stranger_or_another_group_is_not(tmp_path):
    x = rig(tmp_path)
    assert await deliver(x, messages(said("Odysseus, what do we know about Corvid?", mid="a"))) == 200
    await deliver(x, messages(said("lunch at one?", sender=TARA_NUMBER, mid="b")))
    await deliver(x, messages(said("Odysseus tell me the pipeline", sender=STRANGER, mid="c")))
    await deliver(x, messages(said("Odysseus hello", group=OTHER, mid="d")))
    await deliver(x, messages(said("", kind="image", mid="e")))
    cid = chat_id_of(GROUP)
    assert x.house.heard == [(HOST, cid, "what do we know about Corvid?", True), (7, cid, "lunch at one?", False)]
    assert texts(x) == [(GROUP, "group", "ok: what do we know about Corvid?")], "the answer goes to the group, as a group message"
    assert x.sent[0][1].endswith(f"/{GROUP_LINE}/messages")
    assert x.conv.texts == [], "nothing said in a group reaches the host's own assistant"
    log = (tmp_path / "journal.jsonl").read_text(encoding="utf-8") if (tmp_path / "journal.jsonl").exists() else ""
    assert "pipeline" not in log and STRANGER not in log and "Corvid" not in log, "what a stranger said is not written down, nor what anyone said"


async def test_a_delivery_is_handled_once_and_a_late_one_not_at_all(tmp_path):
    x = rig(tmp_path)
    await deliver(x, messages(said("Odysseus one", mid="same")))
    await deliver(x, messages(said("Odysseus one", mid="same")))
    await deliver(x, messages(said("Odysseus old", mid="late", age=3600)))
    assert [h[2] for h in x.house.heard] == ["one"]
    assert await deliver(x, messages(said("Odysseus forged", mid="f")), secret="wrong") == 403 and len(x.house.heard) == 1


async def test_a_person_is_known_by_metas_id_once_it_has_come_with_their_number(tmp_path):
    x = rig(tmp_path)
    await deliver(x, messages(said("Odysseus first", sender="", user="IN.tara", mid="a")))
    assert x.house.heard == [], "an id never seen beside a known number is nobody"
    await deliver(x, messages(said("Odysseus second", sender=TARA_NUMBER, user="IN.tara", mid="b")))
    await deliver(x, messages(said("Odysseus third", sender="", user="IN.tara", mid="c")))
    assert [(h[0], h[2]) for h in x.house.heard] == [(7, "second"), (7, "third")]
    again = rig(tmp_path)
    await deliver(again, messages(said("Odysseus fourth", sender="", user="IN.tara", mid="d")))
    assert [(h[0], h[2]) for h in again.house.heard] == [(7, "fourth")], "kept across a restart"
    spoof = rig(tmp_path, members={})
    await deliver(spoof, messages(said("Odysseus as host", sender=OWNER_NUMBER, user="IN.other", mid="e")))
    assert spoof.house.heard[0][0] == HOST, "the host's own number needs no entry"


async def test_a_question_is_answered_by_one_exact_word_from_someone_who_is_heard(tmp_path):
    x = rig(tmp_path)
    await x.groups.chat().ask("Send the note to Corvid?", "ap1", thread=True)
    question = texts(x)[-1][2]
    assert question.startswith("Send the note to Corvid?") and "Reply YES to approve or NO to decline." in question and "THREAD" not in question
    await deliver(x, messages(said("yes", sender=STRANGER, mid="a")))
    await deliver(x, messages(said("yes please", sender=TARA_NUMBER, mid="b")))
    assert x.house.decisions == [] and x.house.heard == [(7, chat_id_of(GROUP), "yes please", False)], "a stranger's word and a longer reply answer nothing"
    await deliver(x, messages(said("Yes.", sender=TARA_NUMBER, mid="c")))
    assert x.house.decisions == [(7, chat_id_of(GROUP), "ap1", True)]
    assert texts(x)[-1] == (GROUP, "group", 'Approved. ("Send the note to Corvid?")')
    await deliver(x, messages(said("yes", sender=TARA_NUMBER, mid="d")))
    assert len(x.house.decisions) == 1 and x.house.heard[-1][2] == "yes", "with nothing waiting, yes is an ordinary message"


async def test_a_question_that_whatsapp_refuses_was_not_asked(tmp_path):
    x = rig(tmp_path, refuse=True)
    try:
        await x.groups.chat().ask("Send it?", "ap1")
        raise AssertionError("a refused question must not pass as asked")
    except RuntimeError:
        pass
    x2 = rig(tmp_path)
    x2.groups._typed = x.groups._typed
    await deliver(x2, messages(said("yes", mid="a")))
    assert x2.house.decisions == []
    assert '"where": "whatsapp_group.send"' in (tmp_path / "journal.jsonl").read_text(encoding="utf-8")


async def test_what_is_said_to_the_number_alone_is_not_the_groups_and_what_is_said_in_a_group_is_never_the_owners(tmp_path):
    x = rig(tmp_path)
    await deliver(x, messages(said("to the number alone", group="", mid="a")))                         # the group's number, no group: another route's
    await deliver(x, messages(said("to the direct number", group="", mid="b"), line=DIRECT_LINE))       # the direct route's own
    await deliver(x, messages(said("Odysseus in a group on the direct number", mid="c"), line=DIRECT_LINE))
    assert x.house.heard == [] and x.conv.texts == ["to the direct number"]
    alone = WhatsApp(x.conv, x.journal, x.door._http, phone_id=DIRECT_LINE, owner_number=OWNER_NUMBER, app_secret=SECRET, verify_token="v",
                     owner_id=HOST, clock=lambda: NOW)
    assert alone.receive(*signed(messages(said("yes", mid="d"), line=DIRECT_LINE))) == 200
    await alone.wait()
    assert x.conv.texts == ["to the direct number"], "with nothing serving groups, a group message is still not the owner's"


async def test_a_word_said_in_a_group_does_not_answer_a_question_asked_through_vobiz(tmp_path):
    sent, conv = [], FakeConversation()

    async def http(method, url, body):
        sent.append(body)
        return 201, {}
    v = Vobiz(conv, Journal(tmp_path / "j.jsonl"), http, auth_id="MA_TEST", channel_id="c", number="918040001234", owner_number=OWNER_NUMBER,
              secret=SECRET, owner_id=HOST, clock=lambda: NOW)
    await v.chat().ask("Send the email?", "ap9")
    await v._handle(said("yes", mid="g1"))
    assert conv.decisions == [] and conv.texts == []
    await v._handle(said("yes", group="", mid="g2"))
    assert conv.decisions == [("ap9", True)]


async def test_the_group_is_learned_when_meta_reports_it_made_and_forgotten_when_it_is_deleted(tmp_path):
    x = rig(tmp_path, group="")
    await deliver(x, messages(said("Odysseus hello", mid="a")))
    assert x.house.heard == [], "before any group is known, none is served"
    made = {"timestamp": int(NOW), "group_id": GROUP, "type": "group_create", "request_id": "r1", "subject": "Deal room",
            "invite_link": "https://chat.whatsapp.com/TESTLINK", "join_approval_mode": "auto_approve"}
    await deliver(x, {"field": "group_lifecycle_update", "value": {"messaging_product": "whatsapp", "groups": [made]}})
    assert x.groups.group_id == GROUP and json.loads(x.state.read_text(encoding="utf-8"))["subject"] == "Deal room"
    assert "https://chat.whatsapp.com/TESTLINK" in x.told[-1] and "/firm on" in x.told[-1]
    assert "TESTLINK" not in (tmp_path / "journal.jsonl").read_text(encoding="utf-8"), "the link lets anyone in, so it is told and not logged"
    await deliver(x, messages(said("Odysseus hello", mid="b")))
    assert [h[2] for h in x.house.heard] == ["hello"]
    await deliver(x, {"field": "group_lifecycle_update", "value": {"groups": [{**made, "group_id": OTHER, "subject": "Second"}]}})
    assert x.groups.group_id == GROUP and "one group assistant" in x.told[-1]
    await deliver(x, {"field": "group_participants_update", "value": {"groups": [
        {"timestamp": int(NOW), "group_id": GROUP, "type": "group_participants_add", "reason": "invite_link",
         "added_participants": [{"wa_id": STRANGER, "user_id": "GB.1"}]}]}})
    assert STRANGER not in (tmp_path / "journal.jsonl").read_text(encoding="utf-8") and len(x.told) == 2
    await deliver(x, {"field": "group_status_update", "value": {"groups": [{"timestamp": int(NOW), "type": "group_suspend", "group_id": GROUP}]}})
    assert "suspended" in x.told[-1]
    await deliver(x, {"field": "group_lifecycle_update", "value": {"groups": [{"timestamp": int(NOW), "group_id": GROUP, "type": "group_delete", "request_id": "r2"}]}})
    assert x.groups.group_id == "" and "deleted" in x.told[-1]
    failed = rig(tmp_path / "f" if (tmp_path / "f").mkdir() is None else tmp_path, group="")
    await deliver(failed, {"field": "group_lifecycle_update", "value": {"groups": [{"type": "group_create", "errors": [{"code": 131215}]}]}})
    assert failed.groups.group_id == "" and "did not make the group" in failed.told[-1]


async def test_with_the_real_house_the_host_switches_it_on_in_the_group_and_a_member_is_heard_by_name(tmp_path):
    h = firm_rig(tmp_path)
    code = await invite(h, "Tara")
    await h.house.handle(7, f"/start {code}", Chat(who="Tara Menon"))
    x = rig(tmp_path, house=h.house)
    cid = chat_id_of(GROUP)
    await deliver(x, messages(said("/firm on", sender=TARA_NUMBER, mid="a")))
    assert h.firms == [], "a member cannot switch it on"
    await deliver(x, messages(said("/firm on", mid="b")))
    assert len(h.firms) == 1 and h.firms[0].chat_id == cid and "this group's assistant" in texts(x)[-1][2]
    firm = h.firms[0].conversation
    await deliver(x, messages(said("lunch at one?", sender=TARA_NUMBER, mid="c")))
    await deliver(x, messages(said("Odysseus, what do we know about Corvid?", sender=TARA_NUMBER, mid="d")))
    assert firm.heard == [("text", cid, "[In the firm's group chat. Tara Menon says:]\nwhat do we know about Corvid?")]
    assert h.host.conversation.heard == [] and h.built[7].conversation.heard == [], "nobody's own assistant hears the group"
    await x.groups.chat().ask("File Corvid?", "a1")
    await deliver(x, messages(said("approve", sender=TARA_NUMBER, mid="e")))
    assert firm.heard[-1] == ("decision", cid, "a1")
    assert json.loads((tmp_path / "group.json").read_text(encoding="utf-8")) == {"id": cid, "attio": False}


def test_it_is_a_choice_on_with_its_number_and_the_direct_route_off_by_one_word(tmp_path):
    base = {"TELEGRAM_BOT_TOKEN": "t", "OWNER_TELEGRAM_ID": "1", "ANTHROPIC_API_KEY": "k", "EXA_API_KEY": "e", "ODYSSEUS_DATA_DIR": str(tmp_path)}
    direct = {"WHATSAPP_TOKEN": "tok", "WHATSAPP_PHONE_ID": "p1", "WHATSAPP_APP_SECRET": "s", "OWNER_WHATSAPP": "919820012345"}

    def settings(**more):
        return load({**base, **more}, tmp_path / "no-such-file")
    assert whatsapp_group_on(settings(**direct)) is False
    assert whatsapp_group_on(settings(WHATSAPP_GROUP_PHONE_ID="p2")) is False, "it arrives by the direct route's door, so it needs that route"
    on = settings(**direct, WHATSAPP_GROUP_PHONE_ID="p2", WHATSAPP_GROUP_MEMBERS="919811100022=7", ODYSSEUS_GROUP_NAME="@Argus")
    assert whatsapp_group_on(on) is True and on.group_name == "Argus" and members_from(on.whatsapp_group_members) == {"919811100022": 7}
    assert whatsapp_group_on(settings(**direct, WHATSAPP_GROUP_PHONE_ID="p2", ODYSSEUS_WHATSAPP_GROUP="off")) is False
