"""More than one person on one copy: who gets in, and that each message reaches only its sender's assistant."""
from __future__ import annotations

import json
from types import SimpleNamespace

import odysseus.__main__ as entry
from odysseus.config import load
from odysseus.journal import Journal
from odysseus.people import ALL_TRIES, CONNECT, INVITE_DAYS, OPEN_INVITES, TRIES, WELCOME, House, People, clean_name

HOST = 1


class Chat:
    def __init__(self, who=""):
        self.who, self.sent, self.buttons = who, [], []

    async def send(self, text):
        self.sent.append(text)

    async def links(self, text, links):
        self.buttons.append((text, links))


class Conv:
    def __init__(self):
        self.heard = []

    async def handle(self, user_id, text, chat, **more):
        self.heard.append(("text", user_id, text))

    async def handle_file(self, user_id, *a, **k):
        self.heard.append(("file", user_id))

    async def handle_voice(self, user_id, *a, **k):
        self.heard.append(("voice", user_id))

    async def handle_decision(self, user_id, approval_id, approved, thread=False):
        self.heard.append(("decision", user_id, approval_id))
        return "Approved."

    async def handle_choice(self, user_id, offer_id, index):
        self.heard.append(("choice", user_id, offer_id))
        return "Chosen."


def assistant(spent=0.0, links=(("Connect Gmail", "https://sign.example/g"), ("Connect Google Calendar", "https://sign.example/c"))):
    a = SimpleNamespace(conversation=Conv(), ledger=SimpleNamespace(spent_today=lambda: spent), notify=Chat(), closed=[],
                        apps=SimpleNamespace(connect_links=lambda: list(links)) if links is not None else None)

    async def close():
        a.closed.append(True)
    a.close = close
    return a


def rig(tmp_path, most=10, fail=False):
    clock = SimpleNamespace(t=1_000_000.0)
    people = People(tmp_path / "people.json", clock=lambda: clock.t)
    journal = Journal(tmp_path / "j.jsonl")
    built = {}

    async def assemble(user_id):
        if fail:
            raise RuntimeError("no room")
        built[user_id] = assistant()
        return built[user_id]

    async def link(code):
        return f"https://t.me/thebot?start={code}"
    house = House(HOST, people, journal, assemble, link, most=most, clock=lambda: clock.t)
    host = assistant(spent=0.42)
    house.add(HOST, host)
    return SimpleNamespace(house=house, people=people, journal=journal, built=built, host=host, clock=clock, path=tmp_path / "people.json")


async def invite(x, note=""):
    chat = Chat()
    await x.house.handle(HOST, f"/invite {note}".strip(), chat)
    return chat.sent[-1].rsplit("start=", 1)[1]


def test_a_code_works_once_for_a_week_and_only_its_hash_is_kept(tmp_path):
    clock = SimpleNamespace(t=1_000_000.0)
    people = People(tmp_path / "p.json", clock=lambda: clock.t)
    code = people.invite("for Tara")
    assert code not in (tmp_path / "p.json").read_text(encoding="utf-8"), "a copy of the file is not a way in"
    assert not people.redeem("wrong", 7) and not people.redeem("", 7) and not people.has(7)
    assert people.redeem(code, 7, "Tara\nMenon <b>") and people.has(7) and not people.redeem(code, 8), "once"
    assert People(tmp_path / "p.json").members["7"] == {"name": "Tara Menon b", "note": "for Tara", "joined": 1_000_000}
    late = people.invite()
    clock.t += INVITE_DAYS * 86400 + 1
    assert not people.redeem(late, 9), "lapsed"
    assert [people.invite() is not None for _ in range(OPEN_INVITES + 1)][-1] is False, "unused invitations are bounded"
    assert people.remove(7)["name"] == "Tara Menon b" and people.remove(7) is None and People(tmp_path / "p.json").ids() == []
    assert clean_name("  A\x00very" + "y" * 80) == ("A very" + "y" * 80)[:40]


async def test_a_stranger_gets_no_reply_and_a_wrong_code_gets_none_either(tmp_path):
    x = rig(tmp_path)
    await invite(x)
    for text in ("hello", "/start", "/start nope", "/start a b", "/invite", "/people"):
        chat = Chat("Mallory")
        await x.house.handle(66, text, chat)
        assert chat.sent == [] and chat.buttons == [], text
    await x.house.handle_file(66, b"x", "a.pdf", "application/pdf", 1, "", Chat())
    assert await x.house.handle_decision(66, "abc", True) == "" and await x.house.handle_choice(66, "abc", 0) == ""
    assert x.built == {} and not x.house.is_owner(66) and x.host.conversation.heard == []
    assert [r["kind"] for r in x.journal.read()].count("dropped") == 6


async def test_guessing_is_bounded_for_one_stranger_and_for_all_of_them(tmp_path):
    x = rig(tmp_path)
    code = await invite(x)
    for _ in range(TRIES):
        await x.house.handle(66, "/start guess", Chat())
    chat = Chat()
    await x.house.handle(66, f"/start {code}", chat)
    assert chat.sent == [] and not x.people.has(66), "after five wrong codes even the right one is not looked at for an hour"
    x.clock.t += 3601
    await x.house.handle(66, f"/start {code}", chat)
    assert x.people.has(66)
    y = rig(tmp_path / "b")
    code = await invite(y)
    for i in range(ALL_TRIES):
        await y.house.handle(1000 + i, "/start guess", Chat())
    await y.house.handle(77, f"/start {code}", Chat())
    assert not y.people.has(77), "many accounts guessing together are stopped too"


async def test_joining_builds_an_assistant_welcomes_with_sign_in_buttons_and_tells_the_host(tmp_path):
    x = rig(tmp_path)
    code = await invite(x, "Tara, partner")
    chat = Chat("Tara Menon")
    await x.house.handle(7, f"/start {code}", chat)
    assert list(x.built) == [7] and x.house.is_owner(7)
    assert chat.sent == [WELCOME] and "could read it" in WELCOME, "the newcomer is told who can read what is stored"
    assert chat.buttons == [(CONNECT, [("Connect Gmail", "https://sign.example/g"), ("Connect Google Calendar", "https://sign.example/c")])]
    assert x.host.notify.sent == ["Tara Menon joined with one of your invitations (id 7). /people lists everyone."]
    again = Chat("Someone Else")
    await x.house.handle(8, f"/start {code}", again)
    assert again.sent == [] and 8 not in x.built, "the link works for the first person only"
    assert code not in json.dumps(x.journal.read()) and "Tara" not in json.dumps(x.journal.read()), "ids in the log, not codes or names"


async def test_each_message_file_and_tap_reaches_only_its_senders_assistant(tmp_path):
    x = rig(tmp_path)
    for uid in (7, 8):
        await x.house.handle(uid, f"/start {await invite(x)}", Chat())
    await x.house.handle(7, "what is in my inbox", Chat())
    await x.house.handle(8, "remind me at five", Chat())
    await x.house.handle_file(7, b"d", "deck.pdf", "application/pdf", 1, "", Chat())
    await x.house.handle_voice(8, b"v", "v.ogg", 1, 1, Chat())
    assert await x.house.handle_decision(7, "ap1", True) == "Approved." and await x.house.handle_choice(8, "k1", 0) == "Chosen."
    await x.house.handle(HOST, "hello", Chat())
    assert x.built[7].conversation.heard == [("text", 7, "what is in my inbox"), ("file", 7), ("decision", 7, "ap1")]
    assert x.built[8].conversation.heard == [("text", 8, "remind me at five"), ("voice", 8), ("choice", 8, "k1")]
    assert x.host.conversation.heard == [("text", HOST, "hello")]


async def test_only_the_host_invites_lists_and_removes(tmp_path):
    x = rig(tmp_path)
    await x.house.handle(7, f"/start {await invite(x, 'Tara')}", Chat("Tara Menon"))
    for text in ("/invite", "/people", f"/remove {HOST}", "/remove 7"):
        chat = Chat()
        await x.house.handle(7, text, chat)
        assert chat.sent == [], "a member's message goes to their own assistant, which knows no such command"
    assert [h[2] for h in x.built[7].conversation.heard] == ["/invite", "/people", f"/remove {HOST}", "/remove 7"] and x.people.has(7)
    chat = Chat()
    await x.house.handle(HOST, "/people", chat)
    assert "- you, id 1: $0.42 today" in chat.sent[0] and "- Tara Menon (Tara), id 7: $0.00 today" in chat.sent[0]
    await x.house.handle(HOST, f"/remove {HOST}", chat)
    await x.house.handle(HOST, "/remove 999", chat)
    assert all("cannot remove yourself" in t for t in chat.sent[1:3]) and x.house.is_owner(HOST)
    member = x.built[7]
    await x.house.handle(HOST, "/remove 7", chat)
    assert member.closed == [True] and not x.house.is_owner(7) and not x.people.has(7) and "people/7" in chat.sent[-1]
    after = Chat()
    await x.house.handle(7, "still there?", after)
    assert after.sent == [] and len(member.conversation.heard) == 4, "a removed person is a stranger again"
    await x.house.handle(HOST, "/help", chat)
    assert x.host.conversation.heard[-1] == ("text", HOST, "/help") and chat.sent[-1].startswith("Hosting: /invite")


async def test_a_full_house_and_a_failed_setup_are_said_plainly(tmp_path):
    x = rig(tmp_path, most=2)
    code = await invite(x)
    spare = x.people.invite()
    await x.house.handle(7, f"/start {code}", Chat())
    chat = Chat()
    await x.house.handle(HOST, "/invite", chat)
    assert "at most 2 people" in chat.sent[0]
    await x.house.handle(8, f"/start {spare}", Chat())
    assert not x.people.has(8) and x.people.redeem(spare, 9), "a code met by a full house is not used up"
    y = rig(tmp_path / "b", fail=True)
    chat = Chat()
    await y.house.handle(7, f"/start {await invite(y)}", chat)
    assert "could not finish setting up" in chat.sent[0] and "could not be set up" in y.host.notify.sent[0] and not y.house.is_owner(7)
    z = rig(tmp_path / "c")
    z.built_none = True

    async def bare(user_id):
        return assistant(links=None)
    z.house._assemble = bare
    chat = Chat()
    await z.house.handle(7, f"/start {await invite(z)}", chat)
    assert chat.sent[1].startswith("Mail and calendar are not set up") and chat.buttons == []


def test_an_invited_person_gets_a_folder_a_limit_and_sign_ins_of_their_own(tmp_path):
    host = load({"TELEGRAM_BOT_TOKEN": "t", "OWNER_TELEGRAM_ID": "1", "ANTHROPIC_API_KEY": "k", "EXA_API_KEY": "e", "ODYSSEUS_DATA_DIR": str(tmp_path),
                 "ODYSSEUS_MEMBER_DAILY_USD": "1.5", "WHATSAPP_TOKEN": "w", "OWNER_WHATSAPP": "+10000000000", "ODYSSEUS_NOTIFY_ON": "whatsapp"},
                env_file=tmp_path / "none.env")
    m = entry.member_settings(host, 7)
    assert m.owner_id == 7 and m.data_dir == tmp_path / "people" / "7" and m.daily_usd == 1.5 and host.daily_usd == 3.0
    assert (m.whatsapp_token, m.owner_whatsapp, m.notify_on) == ("", "", "telegram"), "one WhatsApp number serves one person"
    assert entry.member_settings(load({"TELEGRAM_BOT_TOKEN": "t", "OWNER_TELEGRAM_ID": "1", "ANTHROPIC_API_KEY": "k", "EXA_API_KEY": "e"},
                                      env_file=tmp_path / "none.env"), 7).daily_usd == 3.0
    assert host.people_max == 10


async def test_the_program_starts_an_assistant_for_each_member_and_connects_only_the_hosts_at_once(tmp_path, monkeypatch):
    for key, value in {"TELEGRAM_BOT_TOKEN": "t", "OWNER_TELEGRAM_ID": "1", "ANTHROPIC_API_KEY": "k", "EXA_API_KEY": "e",
                       "COMPOSIO_API_KEY": "c", "BROWSER_USE_API_KEY": "", "ODYSSEUS_DATA_DIR": str(tmp_path)}.items():
        monkeypatch.setenv(key, value)
    people = People(tmp_path / "people.json")
    for uid in (7, 8):
        assert people.redeem(people.invite(), uid, f"Person {uid}")
    started, houses = [], []

    async def fake_start(self):
        started.append(self)

    class Bot:
        def __init__(self, token):
            self.session = self

        async def close(self):
            pass

    class Dispatcher:
        async def start_polling(self, bot, allowed_updates=None):
            pass

    def dispatcher(bot, house):
        houses.append(house)
        return Dispatcher()
    monkeypatch.setattr(entry, "Bot", Bot)
    monkeypatch.setattr(entry, "build_dispatcher", dispatcher)
    monkeypatch.setattr(entry.AgentRunner, "start", fake_start)
    assert await entry.main() == 0
    house = houses[0]
    assert sorted(house._at) == [1, 7, 8] and len(started) == 3, "three people, and only the host's three agents were connected at start"
    seven, eight, host = house._at[7], house._at[8], house._at[1]
    assert (tmp_path / "people" / "7" / "journal.jsonl").exists() and (tmp_path / "people" / "8" / "journal.jsonl").exists()
    assert seven.conversation._s.owner_id == 7 and seven.conversation._memory is not eight.conversation._memory is not host.conversation._memory
    assert seven.conversation._gate is not host.conversation._gate and seven.conversation._gate.taint is not eight.conversation._gate.taint
    assert seven.apps._b._user_id == "odysseus-7" and host.apps._b._user_id == "odysseus-1", "sign-ins are named by person"
    assert not host.conversation._connector._remote and seven.conversation._connector._remote, "an invited person's sign-in comes back through the chat"
    assert seven.conversation.is_owner(7) and not seven.conversation.is_owner(1) and not host.conversation.is_owner(7)
    assert [r["kind"] for r in Journal(tmp_path / "people" / "7" / "journal.jsonl").read()] == ["start"]
    # The host connects a service: only the host's assistant is built again, as a host, and nobody else's is touched.
    host.conversation._connector._restart()
    await house._rebuilding[1]
    again = house._at[1]
    assert again is not host and house._at[7] is seven and house._at[8] is eight
    assert not again.conversation._connector._remote and again.conversation._s.data_dir == tmp_path and again.conversation._s.owner_id == 1
    assert len(seven.agents) >= 4 and seven.gate is seven.conversation._gate, "every agent of a person is known, so that an idle one can be let go"


async def test_a_silent_persons_agents_are_let_go_and_the_hosts_are_not(tmp_path):
    x = rig(tmp_path)
    parked = []
    for uid in (HOST, 7, 8):
        a = x.host if uid == HOST else assistant()

        async def park(uid=uid):
            parked.append(uid)
            return 3
        a.park = park
        x.house.add(uid, a)
    await x.house.handle(7, "hello", Chat())
    x.clock.t += 600
    await x.house.handle(8, "hello", Chat())
    x.clock.t += 400
    assert await x.house.park_idle(900) == 3 and parked == [7], "7 has been silent for long enough; 8 has not; the host is never parked"
    assert [r["user_id"] for r in x.journal.read() if r.get("outcome") == "parked"] == [7]


async def test_an_agent_is_parked_only_when_nothing_runs_on_it(tmp_path):
    from odysseus.agent import AgentRunner
    gone = []

    class Client:
        async def disconnect(self):
            gone.append(1)
    r = AgentRunner.__new__(AgentRunner)
    r._journal, r._client, r._running = Journal(tmp_path / "j.jsonl"), None, False
    assert await r.park() is False, "never connected"
    r._client, r._running = Client(), True
    assert await r.park() is False and gone == [], "a task is running on it"
    r._running = False
    assert await r.park() is True and gone == [1] and r._client is None
