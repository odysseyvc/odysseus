"""Telegram wiring. All decisions are made in conversation.py; this only carries messages."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .conversation import Conversation, split_message
from .ocr import MAX_BYTES
from .voice import MAX_BYTES as VOICE_MAX_BYTES, MAX_SECONDS as VOICE_MAX_SECONDS


STALE_AFTER_S = 600       # a message older than this when it arrives is reported, not acted on


from aiogram.types import LinkPreviewOptions  # noqa: E402

from . import fmt  # noqa: E402

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


class TelegramChat:
    def __init__(self, bot: Bot, chat_id: int, who: str = "", message_id: int | None = None, sent_at: Any = None):
        self._bot = bot
        self.sent_at = sent_at      # when the owner sent the message (Telegram's time), for anything dated by it: booknotes.py
        self._chat_id = chat_id
        self.who = who          # the sender's name as Telegram gives it; used only when someone joins (people.py)
        # The message this chat object was made for. When that message had to wait behind another task, its answer is
        # sent as a reply to it (`quote`, set by the conversation), so that several answers arriving together can be told apart.
        self._message_id, self.quote = message_id, False

    def _quoting(self) -> dict:
        if not (self.quote and self._message_id):
            return {}
        from aiogram.types import ReplyParameters
        return {"reply_parameters": ReplyParameters(message_id=self._message_id, allow_sending_without_reply=True)}

    async def react(self, emoji: str) -> bool:
        """Put a reaction on the message this chat object was made for (react.py). False when there is no such message."""
        if not self._message_id:
            return False
        from aiogram.types import ReactionTypeEmoji
        await self._bot.set_message_reaction(self._chat_id, self._message_id, reaction=[ReactionTypeEmoji(emoji=emoji)])
        return True

    async def links(self, text: str, links: list[tuple[str, str]]) -> None:
        """A message with one button per address. Each button opens its address in the person's own browser."""
        rows = [[InlineKeyboardButton(text=label[:40], url=url)] for label, url in links[:6]]
        await self._bot.send_message(self._chat_id, text, parse_mode=None, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    async def send_file(self, name: str, data: bytes | None = None, handle: str = "", kind: str = "document", caption: str = "") -> None:
        """Send a file: by the id Telegram gave it when it arrived, which uploads nothing, or from its bytes."""
        what = handle or BufferedInputFile(data or b"", filename=name or "file")
        if kind == "photo" and handle:
            await self._bot.send_photo(self._chat_id, what, caption=caption[:1000] or None, parse_mode=None)
        else:
            await self._bot.send_document(self._chat_id, what, caption=caption[:1000] or None, parse_mode=None)

    async def send_voice(self, data: bytes) -> None:
        """A spoken reply. Telegram shows it as a voice note only if it is Opus in an Ogg file; anything else goes as a sound file."""
        if data[:4] == b"OggS":
            await self._bot.send_voice(self._chat_id, BufferedInputFile(data, filename="reply.ogg"))
        else:
            await self._bot.send_audio(self._chat_id, BufferedInputFile(data, filename="reply.opus"))

    async def send(self, text: str) -> None:
        # The reply's few marks are turned into Telegram's own form by code (fmt.py), which closes every tag it opens
        # and escapes everything else. If Telegram still refuses the result, the same words go out with no marks.
        # No link previews: to make one Telegram fetches the link, and some links the program sends (a sign-in link) are
        # meant to be opened once, by the owner.
        async def out(body: str, mode: str | None) -> None:
            try:
                await self._bot.send_message(self._chat_id, body, parse_mode=mode, link_preview_options=NO_PREVIEW, **self._quoting())
            except TelegramRetryAfter as e:
                # Telegram says it is being sent too much, and how long to wait. Wait that long, once, and send again.
                await asyncio.sleep(min(float(e.retry_after), 30.0) + 0.5)
                await self._bot.send_message(self._chat_id, body, parse_mode=mode, link_preview_options=NO_PREVIEW, **self._quoting())
        marked = fmt.telegram(text)
        if not marked.strip():
            return
        try:
            await out(marked, "HTML")
        except TelegramBadRequest:
            await out(fmt.plain(text) or text, None)

    async def send_html(self, text: str) -> None:
        """A message the program wrote itself, with Telegram's HTML for bold, italics and links. Anything taken from
        elsewhere must be escaped by the caller (resurface.py does). No link previews, as with send."""
        try:
            await self._bot.send_message(self._chat_id, text, parse_mode="HTML", link_preview_options=NO_PREVIEW)
        except TelegramRetryAfter as e:
            await asyncio.sleep(min(float(e.retry_after), 30.0) + 0.5)
            await self._bot.send_message(self._chat_id, text, parse_mode="HTML", link_preview_options=NO_PREVIEW)

    async def draft(self, draft_id: int, text: str) -> None:
        # A reply still arriving is shown in the same form as the finished one. A mark not yet closed is shown as typed.
        try:
            await self._bot.send_message_draft(self._chat_id, draft_id, text=fmt.telegram(fmt.settle(text)) or text, parse_mode="HTML")
        except TelegramBadRequest:
            await self._bot.send_message_draft(self._chat_id, draft_id, text=fmt.plain(text) or text, parse_mode=None)

    async def typing(self) -> None:
        """Show "typing..." in the chat. It lasts a few seconds, which covers the wait for the first step."""
        await self._bot.send_chat_action(self._chat_id, "typing")

    async def choose(self, text: str, approval_id: str, options: list[str]) -> None:
        """A question with one button for each answer. The buttons carry the question's id and the answer's position."""
        await self._bot.send_message(self._chat_id, text, parse_mode=None, reply_markup=_choices(approval_id, options))

    async def ask(self, text: str, approval_id: str, thread: bool = False) -> None:
        # The buttons carry only the approval's id. What will run is held by the gate, not by the message.
        rows = [[InlineKeyboardButton(text="Approve", callback_data=f"ap:{approval_id}:y"),
                 InlineKeyboardButton(text="Decline", callback_data=f"ap:{approval_id}:n")]]
        if thread:      # a reply: the owner may approve this one and let the rest of the thread go for today (gate.py)
            rows.append([InlineKeyboardButton(text="Approve, and this thread today", callback_data=f"ap:{approval_id}:t")])
        buttons = InlineKeyboardMarkup(inline_keyboard=rows)
        # The question is sent whole. If it is longer than one message, the buttons go on the last part,
        # so that they are reached only after everything they cover has been shown.
        parts = split_message(text)
        for part in parts[:-1]:
            await self._bot.send_message(self._chat_id, part, parse_mode=None)
        await self._bot.send_message(self._chat_id, parts[-1], parse_mode=None, reply_markup=buttons)


def _choices(approval_id: str, options: list[str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=o[:40], callback_data=f"ap:{approval_id}:c{i}")] for i, o in enumerate(options[:8])])


def addressed_to(text: str, username: str, is_reply_to_me: bool) -> tuple[str, bool]:
    """A group message with the assistant's own name taken out, and whether it was said to the assistant."""
    mention = re.compile(rf"(?i)(?<![\w@])@{re.escape(username)}\b") if username else None
    named = bool(mention and mention.search(text))
    clean = " ".join(mention.sub(" ", text).split()) if named else text.strip()
    return clean, named or is_reply_to_me


def build_dispatcher(bot: Bot, conversation: Conversation) -> Dispatcher:
    dp = Dispatcher()
    told_late = [0.0]
    # Telegram hands over several messages at once and the library starts on all of them together, so three messages typed
    # quickly were taken up in whatever order their first pause ended: "3+3" was answered after "6+6". One person's messages
    # are now let in one at a time, in the order they were received. A tap on a button does not wait in this line: it has
    # to be able to answer a question while a message is being dealt with.
    turns: dict[int, asyncio.Lock] = {}

    def in_turn(user_id: int) -> asyncio.Lock:
        if len(turns) > 500:
            for known in [u for u, lock in turns.items() if not lock.locked()][:250]:
                del turns[known]
        return turns.setdefault(user_id, asyncio.Lock())

    async def late(message: Message) -> bool:
        """True if this message was sent long before it arrived (the program was not running). Telegram holds messages
        for a day and hands them over at start-up; an hours-old instruction is not run as if it had just been given."""
        user_id = message.from_user.id if message.from_user else 0
        sent = message.date if message.date.tzinfo else message.date.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - sent).total_seconds()
        if age <= STALE_AFTER_S or not conversation.is_owner(user_id):
            return False
        loop_time = asyncio.get_running_loop().time()
        if loop_time - told_late[0] >= 60 or told_late[0] == 0.0:
            told_late[0] = loop_time
            await TelegramChat(bot, message.chat.id).send("Something you sent earlier has only just reached me, because I was not running at "
                                                          "the time. I have not acted on it. Send it again if you still want it.")
        return True

    # Private chats only: a bot added to a group would otherwise hear other people.
    @dp.message(F.chat.type == "private", F.text)
    async def on_text(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        async with in_turn(user_id):
            if await late(message):
                return
            await conversation.handle(user_id, message.text or "", TelegramChat(bot, message.chat.id, who=message.from_user.full_name if message.from_user else "",
                                                                                message_id=message.message_id,
                                                                                sent_at=message.date if message.date.tzinfo else message.date.replace(tzinfo=timezone.utc)))

    # One group, if the host switched the firm's assistant on in it (people.House.handle_group). Text only. Whether a
    # message is addressed to the assistant is decided here, from what Telegram says: a mention of its name, or a reply
    # to one of its own messages. Everything else said in the group is dropped there without being read or logged.
    @dp.message(F.chat.type.in_({"group", "supergroup"}), F.text)
    async def on_group_text(message: Message) -> None:
        if not hasattr(conversation, "handle_group") or message.from_user is None or message.from_user.is_bot:
            return
        sent = message.date if message.date.tzinfo else message.date.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - sent).total_seconds() > STALE_AFTER_S:
            return
        me = await bot.me()
        text, addressed = addressed_to(message.text or "", me.username or "",
                                       bool(message.reply_to_message and message.reply_to_message.from_user
                                            and message.reply_to_message.from_user.id == me.id))
        await conversation.handle_group(message.from_user.id, message.chat.id, text, TelegramChat(bot, message.chat.id), addressed=addressed)

    # Photos and documents. The file is fetched only for the owner; conversation.py decides everything else.
    @dp.message(F.chat.type == "private", F.photo | F.document)
    async def on_file(message: Message) -> None:
        if await late(message):
            return
        user_id = message.from_user.id if message.from_user else 0
        chat = TelegramChat(bot, message.chat.id)
        if message.photo:
            item, name, mime = message.photo[-1], "photo.jpg", "image/jpeg"      # the last size is the largest
        else:
            item, name, mime = message.document, message.document.file_name or "file", message.document.mime_type
        size = item.file_size or 0
        data = None
        if conversation.is_owner(user_id) and size <= MAX_BYTES:
            try:
                buffer = await bot.download(item.file_id)
                data = buffer.read() if buffer else None
            except Exception:
                data = None
        async with in_turn(user_id):
            await conversation.handle_file(user_id, data, name, mime, size, message.caption or "", chat,
                                           handle=item.file_id, kind="photo" if message.photo else "document")

    # Voice notes and audio files. Fetched only for the owner, and never kept.
    @dp.message(F.chat.type == "private", F.voice | F.audio)
    async def on_voice(message: Message) -> None:
        if await late(message):
            return
        user_id = message.from_user.id if message.from_user else 0
        item = message.voice or message.audio
        size, seconds = item.file_size or 0, int(item.duration or 0)
        name = getattr(item, "file_name", None) or "voice.ogg"
        data = None
        if conversation.is_owner(user_id) and size <= VOICE_MAX_BYTES and seconds <= VOICE_MAX_SECONDS:
            try:
                buffer = await bot.download(item.file_id)
                data = buffer.read() if buffer else None
            except Exception:
                data = None
        async with in_turn(user_id):
            await conversation.handle_voice(user_id, data, name, size, seconds, TelegramChat(bot, message.chat.id))

    # Anything else the owner sends (a sticker, a video, a location) is answered, so that silence never has to be interpreted.
    @dp.message(F.chat.type == "private")
    async def on_other(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        if conversation.is_owner(user_id):
            await TelegramChat(bot, message.chat.id).send("I can read text, photos, documents and voice notes. I can't use that kind of message yet.")

    @dp.callback_query(F.data.startswith("ap:"))
    async def on_tap(callback: CallbackQuery) -> None:
        _, approval_id, answer = (callback.data or "::").split(":", 2)
        where = callback.message.chat if callback.message is not None else None
        if where is not None and where.type != "private":
            # A question the group's assistant put to the group. It is answered there, by a member, and never reaches
            # anyone's own assistant.
            line = (await conversation.handle_group_decision(callback.from_user.id, where.id, approval_id, answer in ("y", "t"))
                    if hasattr(conversation, "handle_group_decision") and answer in ("y", "n", "t") else "")
        elif answer[:1] == "c" and answer[1:].isdigit():
            line = await conversation.handle_choice(callback.from_user.id, approval_id, int(answer[1:]))
        elif answer == "t":
            line = await conversation.handle_decision(callback.from_user.id, approval_id, True, thread=True)
        else:
            line = await conversation.handle_decision(callback.from_user.id, approval_id, answer == "y")
        await callback.answer(line or None)
        if line and callback.message is not None:
            # Remove the buttons and leave a record of what was decided on the message itself.
            try:
                await callback.message.edit_text(f"{callback.message.text}\n\n{line}", parse_mode=None, reply_markup=None)
            except Exception:
                pass

    return dp
