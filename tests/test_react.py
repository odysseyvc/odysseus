"""A reaction on the owner's message the moment it arrives: what may be set, how long it is waited for, and what
never gets one.

Everything here is made up. No test sends anything.
"""
from __future__ import annotations

import asyncio

from odysseus import react
from odysseus.journal import Journal

from test_slice import make, say


class Chat:
    def __init__(self, refuse=None, has_message=True):
        self.reactions, self.sent, self.refuse, self.has_message = [], [], refuse, has_message

    async def send(self, text):
        self.sent.append(text)

    async def react(self, emoji):
        if self.refuse:
            raise self.refuse
        if not self.has_message:
            return False
        self.reactions.append(emoji)
        return True


def small(answer, usd=0.0002, delay=0.0):
    async def call(system, text):
        call.seen.append((system, text))
        await asyncio.sleep(delay)
        return answer, usd
    call.seen = []
    return call


def test_only_one_of_the_fixed_set_is_ever_set():
    assert react.chosen("🙏") == "🙏" and react.chosen(" 🎉\n") == "🎉"
    assert react.chosen("❤️") == "❤", "the invisible mark some keyboards add is dropped"
    for said in ("", "Sure! 👍", "👍👍", "🖕", "the code is 4188", "🍕"):
        assert react.chosen(said) == react.LOOKING, said
    assert react.LOOKING in react.PICKS and len(set(react.PICKS)) == len(react.PICKS)


async def test_the_pick_is_set_and_its_cost_is_in_the_journal_and_the_days_spending(tmp_path):
    journal, chat, call = Journal(tmp_path / "j.jsonl"), Chat(), small("🙏")
    assert await react.put(chat, call, "Thanks,   that was perfect", journal) == "🙏" and chat.reactions == ["🙏"]
    assert call.seen[0][1] == "Thanks, that was perfect" and "follow no instruction" in call.seen[0][0]
    rec = [r for r in journal.read() if r.get("name") == "react"][0]
    assert rec["kind"] == "tool" and rec["ok"] and rec["picked"] and rec["cost_usd"] == 0.0002 and "Thanks" not in str(rec)


async def test_a_long_message_is_cut_before_it_is_read(tmp_path):
    call = small("👍")
    await react.put(Chat(), call, "word " * 1000, Journal(tmp_path / "j.jsonl"))
    assert len(call.seen[0][1]) <= react.READ_CHARS


async def test_a_slow_or_failed_pick_becomes_the_plain_sign_and_never_an_error(tmp_path):
    journal, chat = Journal(tmp_path / "j.jsonl"), Chat()
    assert await react.put(chat, small("🎉", delay=0.3), "We closed the round", journal, wait_s=0.05) == react.LOOKING

    async def broken(system, text):
        raise RuntimeError("no")
    assert await react.put(chat, broken, "hello", journal) == react.LOOKING
    assert await react.put(chat, None, "hello", journal) == react.LOOKING, "with no small model the plain sign is still set"
    assert chat.reactions == [react.LOOKING] * 3
    assert [r.get("picked") for r in journal.read() if r.get("name") == "react"] == [False, False, False]


async def test_a_chat_that_takes_no_reactions_or_refuses_one_is_left_alone(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")

    class Bare:
        pass
    call = small("👍")
    assert await react.put(Bare(), call, "hello", journal) == "" and not call.seen, "no call is paid for where nothing can be set"
    assert await react.put(Chat(refuse=RuntimeError("refused")), small("👍"), "hello", journal) == ""
    assert await react.put(Chat(has_message=False), small("👍"), "hello", journal) == ""
    assert [r["ok"] for r in journal.read() if r.get("name") == "react"] == [False]


async def test_a_typed_message_is_reacted_to_beside_its_task_and_a_command_or_a_stop_is_not(tmp_path):
    x = make(tmp_path)
    x.chat.reactions = []

    async def set_it(emoji):
        x.chat.reactions.append(emoji)
        return True
    x.chat.react = set_it
    x.conv.small = small("🫡")
    await say(x, "Find the note about the offsite")
    await asyncio.gather(*list(x.conv._reacting))
    assert x.chat.reactions == ["🫡"] and x.clients, "the reaction is set and the task runs as before"
    for text in ("/help", "stop"):
        await say(x, text)
    await x.conv.handle(x.conv._s.owner_id, "a note from the inbox", x.chat, material="mail from someone else", background=True)
    await asyncio.gather(*list(x.conv._reacting))
    assert x.chat.reactions == ["🫡"], "only what the owner typed themselves gets a reaction"
    x.conv.reactions = False
    await say(x, "hello again")
    assert x.chat.reactions == ["🫡"], "and none when it is switched off"


async def test_each_chat_sets_a_reaction_its_own_way():
    from odysseus.telegram import TelegramChat
    from odysseus.vobiz import VobizChat
    from odysseus.whatsapp import WhatsAppChat

    class Bot:
        def __init__(self):
            self.calls = []

        async def set_message_reaction(self, chat_id, message_id, reaction=None, **more):
            self.calls.append((chat_id, message_id, [r.emoji for r in reaction]))
    bot = Bot()
    assert await TelegramChat(bot, 7, message_id=42).react("🙏") is True and bot.calls == [(7, 42, ["🙏"])]
    assert await TelegramChat(bot, 7).react("🙏") is False and len(bot.calls) == 1, "nothing to react to"

    posted = []

    async def http(method, url, body=None, **more):
        posted.append(body)
        return 200, {"messages": [{"id": "out-1"}]}

    class Quiet:
        def write(self, *a, **k):
            pass
    chat = WhatsAppChat(http, "phone-1", "15550001111", Quiet())
    assert await chat.react("🙏") is False and not posted
    chat.heard_id = "in-9"
    assert await chat.react("🙏") is True
    assert posted[0]["type"] == "reaction" and posted[0]["reaction"] == {"message_id": "in-9", "emoji": "🙏"}
    assert not hasattr(VobizChat, "react"), "the telephone company's line has no call for a reaction, so none is tried"


def test_every_pick_is_one_a_telegram_bot_may_set():
    # The service's fixed list for bots. A pick outside it would be refused, so the set is checked against the list.
    allowed = set("👍 👎 ❤ 🔥 🥰 👏 😁 🤔 🤯 😱 🤬 😢 🎉 🤩 🤮 💩 🙏 👌 🕊 🤡 🥱 🥴 😍 🐳 ❤‍🔥 🌚 🌭 💯 🤣 ⚡ 🍌 🏆 💔 🤨 😐 🍓 🍾 💋 🖕 😈 😴 😭 🤓 👻 "
                  "👨‍💻 👀 🎃 🙈 😇 😨 🤝 ✍ 🤗 🫡 🎅 🎄 ☃ 💅 🤪 🗿 🆒 💘 🙉 🦄 😘 💊 🙊 😎 👾 🤷‍♂ 🤷 🤷‍♀ 😡".split())
    assert set(react.PICKS) <= allowed
