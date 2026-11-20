"""A file sent to the assistant is kept and handed back when asked for, live.

    python scripts/live_files.py              the real start-up code and the real model; the chat is a stand-in
    python scripts/live_files.py --telegram   also: the real bot sends the sample deck to the owner's own chat, once
                                              from its bytes and once by the id Telegram gave it

An invented person sends a small deck and a picture, starts a new conversation, and asks for each back in their own
words. Checks that the right file is sent each time, to that person's chat, and what it costs.
"""
from __future__ import annotations

import asyncio
import sys
import time
import warnings
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

warnings.simplefilter("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exa_py import Exa  # noqa: E402

import odysseus.__main__ as entry  # noqa: E402
from odysseus.config import load  # noqa: E402
from odysseus.door import Door  # noqa: E402
from odysseus.review import anthropic_call  # noqa: E402
from odysseus.tracing import Tracing  # noqa: E402

from live_picture import png  # noqa: E402

PERSON = 900000007
DECK = Path("var/tiny-deck.pdf")


class Bot:
    """Stands in for Telegram: records what would be sent, and to whom."""

    def __init__(self):
        self.documents, self.photos, self.messages = [], [], []

    async def send_message(self, chat_id, text, **k):
        self.messages.append((chat_id, text))

    async def send_message_draft(self, *a, **k):
        pass

    async def send_document(self, chat_id, what, caption=None, **k):
        self.documents.append((chat_id, what, caption))

    async def send_photo(self, chat_id, what, caption=None, **k):
        self.photos.append((chat_id, what, caption))


class Chat:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)

    async def draft(self, draft_id, text):
        pass

    async def ask(self, text, approval_id, thread=False):
        self.sent.append("[asked] " + text)

    async def choose(self, text, offer, options):
        self.sent.append("[buttons] " + text)


async def real_telegram(real) -> list[tuple[str, bool, str]]:
    """The one thing a stand-in cannot show: that Telegram sends a file again from the id it gave it."""
    from aiogram import Bot as RealBot
    from aiogram.types import BufferedInputFile
    bot = RealBot(real.telegram_token)
    try:
        first = await bot.send_document(real.owner_id, BufferedInputFile(DECK.read_bytes(), filename="odysseus-test-deck.pdf"),
                                        caption="A test of handing a kept file back (1 of 2): sent from the copy on disk. Safe to delete.")
        handle = first.document.file_id
        second = await bot.send_document(real.owner_id, handle,
                                         caption="A test of handing a kept file back (2 of 2): sent again by Telegram's own id, with nothing uploaded. Safe to delete.")
        same = second.document.file_unique_id == first.document.file_unique_id and second.document.file_size == DECK.stat().st_size
        return [("Telegram accepted the file from its bytes and gave it an id", bool(handle), f"id of {len(handle)} characters"),
                ("Telegram sent the same file again from that id alone", same, f"{second.document.file_size} bytes, as on disk")]
    finally:
        await bot.session.close()


async def main() -> int:
    real = load()
    if not real.mistral_api_key or not DECK.exists():
        print("This needs a text-recognition key in the settings and var/tiny-deck.pdf.")
        return 1
    root = Path(f"var/live/files-{int(time.time())}")
    settings = replace(real, owner_id=PERSON, data_dir=root, background_jobs=1, meeting_briefs=False, checkins_at="off", capture_at="off",
                       whatsapp_token="", langfuse_public_key="", langfuse_secret_key="", composio_api_key="", browser_use_api_key="", suggest_people=False)
    bot = Bot()
    shared = SimpleNamespace(bot=bot, tracing=Tracing("", "", ""), second_call=anthropic_call(settings.anthropic_api_key),
                             search=Exa(api_key=settings.exa_api_key).tools.web_search(), door=Door(), rebuild=lambda user_id: None)
    me = await entry.assemble(settings, shared, True)
    conv, ok = me.conversation, []

    def check(name, passed, detail=""):
        ok.append(passed)
        print(f"{'PASS' if passed else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")

    async def say(text) -> tuple[str, float]:
        chat, began = Chat(), time.monotonic()
        await conv.handle(PERSON, text, chat)
        await conv.wait()
        return "\n".join(chat.sent), time.monotonic() - began

    deck, picture = DECK.read_bytes(), png()
    chat = Chat()
    await conv.handle_file(PERSON, deck, "CF_deck_v2.pdf", "application/pdf", len(deck), "Keep this deck for later. Do not analyse it now.", chat, handle="", kind="document")
    await conv.wait()
    await conv.handle_file(PERSON, picture, "IMG_2231.png", "image/png", len(picture), "A drawing for the new office wall. Just keep it.", chat, handle="tg-photo-id", kind="photo")
    await conv.wait()
    print("after the two files:\n" + "\n".join(chat.sent)[:500] + "\n")
    kept = me.conversation._files.all()
    check("both files are kept, with a copy on disk", len(kept) == 2 and all(e["kept"] for e in kept), f"{sum(e['size'] for e in kept)} bytes")

    await say("/new")
    reply, took = await say("Can you send me back the Corvid Freight deck?")
    print(f"asked for the deck ({took:.0f} s):\n{reply[:400]}\n")
    sent = [(c, getattr(w, "filename", w), cap) for c, w, cap in bot.documents]
    check("the deck came back, as a document, to that person's own chat", len(bot.documents) == 1 and bot.documents[0][0] == PERSON
          and getattr(bot.documents[0][1], "data", b"") == deck, str(sent)[:160])
    check("the reply does not paste the deck", len(reply) < 600)

    reply, took = await say("And the drawing I sent for the office wall?")
    print(f"asked for the picture ({took:.0f} s):\n{reply[:300]}\n")
    check("the picture came back by the channel's own id, with nothing uploaded", bot.photos == [(PERSON, "tg-photo-id", bot.photos[0][2])] if bot.photos else False)

    reply, took = await say("Send me the Northwind Robotics deck.")
    print(f"asked for a deck never sent ({took:.0f} s):\n{reply[:300]}\n")
    check("a file that was never sent is not invented", len(bot.documents) == 1 and len(bot.photos) == 1)

    reply, took = await say("Email the Corvid deck to tara@example.org")
    print(f"asked to send it to someone else ({took:.0f} s):\n{reply[:300]}\n")
    check("it does not go to anyone else", len(bot.documents) <= 2 and all(c == PERSON for c, _, _ in bot.documents + bot.photos) and not any("[asked]" in s for s in reply.split("\n")))

    tools = [r for r in me.journal.read() if r["kind"] == "tool" and str(r.get("name", "")).startswith("files_")]
    print("tool calls:", [(r["name"], r.get("ok"), r.get("results", "")) for r in tools])
    spent = me.ledger.spent_today()
    await me.close()
    if "--telegram" in sys.argv:
        for name, passed, detail in await real_telegram(real):
            check(name, passed, detail)
    print(f"\n{sum(ok)} of {len(ok)} checks passed. Model and reading cost: ${spent:.3f}.")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
