"""Telegram wiring. All decisions are made in conversation.py; this only carries messages."""
from __future__ import annotations

from aiogram import Bot, Dispatcher, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .conversation import Conversation, split_message
from .ocr import MAX_BYTES


class TelegramChat:
    def __init__(self, bot: Bot, chat_id: int):
        self._bot = bot
        self._chat_id = chat_id

    async def send(self, text: str) -> None:
        # Plain text: no parse mode, so nothing the model writes is read as markup.
        await self._bot.send_message(self._chat_id, text, parse_mode=None)

    async def draft(self, draft_id: int, text: str) -> None:
        await self._bot.send_message_draft(self._chat_id, draft_id, text=text, parse_mode=None)

    async def ask(self, text: str, approval_id: str) -> None:
        # The buttons carry only the approval's id. What will run is held by the gate, not by the message.
        buttons = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Approve", callback_data=f"ap:{approval_id}:y"),
            InlineKeyboardButton(text="Decline", callback_data=f"ap:{approval_id}:n"),
        ]])
        # The question is sent whole. If it is longer than one message, the buttons go on the last part,
        # so that they are reached only after everything they cover has been shown.
        parts = split_message(text)
        for part in parts[:-1]:
            await self._bot.send_message(self._chat_id, part, parse_mode=None)
        await self._bot.send_message(self._chat_id, parts[-1], parse_mode=None, reply_markup=buttons)


def build_dispatcher(bot: Bot, conversation: Conversation) -> Dispatcher:
    dp = Dispatcher()

    # Private chats only: a bot added to a group would otherwise hear other people.
    @dp.message(F.chat.type == "private", F.text)
    async def on_text(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        await conversation.handle(user_id, message.text or "", TelegramChat(bot, message.chat.id))

    # Photos and documents. The file is fetched only for the owner; conversation.py decides everything else.
    @dp.message(F.chat.type == "private", F.photo | F.document)
    async def on_file(message: Message) -> None:
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
        await conversation.handle_file(user_id, data, name, mime, size, message.caption or "", chat)

    @dp.callback_query(F.data.startswith("ap:"))
    async def on_tap(callback: CallbackQuery) -> None:
        _, approval_id, answer = (callback.data or "::").split(":", 2)
        line = await conversation.handle_decision(callback.from_user.id, approval_id, answer == "y")
        await callback.answer(line or None)
        if line and callback.message is not None:
            # Remove the buttons and leave a record of what was decided on the message itself.
            try:
                await callback.message.edit_text(f"{callback.message.text}\n\n{line}", parse_mode=None, reply_markup=None)
            except Exception:
                pass

    return dp
