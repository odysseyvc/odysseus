"""iMessage as a third channel, bought from Linq (linqapp.com): one API for iMessage, with RCS and SMS behind it.

Nobody but Apple can speak iMessage from a server, so this channel is bought. Linq runs the lines; this file carries
messages between Linq and conversation.py and makes no decisions, as telegram.py and whatsapp.py do. It is off unless
its four settings are present, and ODYSSEUS_IMESSAGE=off switches it off without removing them.

Linq calls a web address when something happens, so this channel sits behind the same door as WhatsApp (door.py), at
/linq. The checks at the door, in order:

  the signature   every delivery is signed in the Standard Webhooks way: HMAC-SHA256, keyed with the subscription's
                  signing secret, over "<id>.<timestamp>.<exact bytes>". One that is not is dropped unread.
  the age         a signature older than five minutes is refused, so a recorded delivery cannot be played again later.
  once            a delivery is handled once: Linq delivers at least once, and replays carry a new event id, so both the
                  event's id and the message's id are remembered.
  the sender      only the owner's own number, in a chat of two, is listened to. Anyone else gets no reply at all.

Rules of this channel that shape what is possible:

  no buttons        iMessage has none. It has polls, and a vote in a poll arrives as a signed event naming the poll and
                    the option. So an approval question is sent as text, followed by a poll with Approve and Decline;
                    a vote is the tap, and while a poll stands a typed "yes" approves nothing. If the poll cannot be
                    made (polls exist only on iMessage itself, not on its SMS fallback), the owner is asked to answer
                    with one exact word, read by code and never by a model (typed.py states that exception).
  speaking first    Linq asks that a line converse and not broadcast, and a free line may write only to people who
                    wrote first. Reminders and routines therefore stay on Telegram; this channel only answers.
  the first message of a new chat may not contain a link (Linq refuses it), so the chat the owner opened is remembered
                    in the data folder and answers go into it.
  late deliveries   a message that arrives more than ten minutes after it was sent is reported, not acted on.
  files             a picture or document arrives as an address at Linq's file service. It is fetched without the
                    account's token, over https only, and no more than the size limit is read.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .conversation import Conversation, split_message
from . import fmt
from .journal import Journal
from .ocr import MAX_BYTES
from .typed import TypedAnswers, how
from .whatsapp import digits

API = "https://api.linqapp.com/api/partner/v3"
WEBHOOK_VERSION = "2026-02-03"      # the shape of delivery this file reads; pinned in the address given to Linq
EVENTS = ("message.received", "poll.vote.added")
TEXT_LIMIT = 4000                   # Linq allows 10,000 characters; a message that long is unreadable on a phone
STALE_AFTER_S = 600
SIGNATURE_TOLERANCE_S = 300

# (method, url, json body or None) -> (status, parsed json or raw bytes)
Http = Callable[[str, str, Any], Awaitable[tuple[int, Any]]]
Fetch = Callable[[str, int], Awaitable[bytes | None]]


def signature_ok(secret: str, raw: bytes, message_id: str, timestamp: str, header: str, now: float) -> bool:
    """A delivery signed as Standard Webhooks describes: `v1,<base64 of HMAC-SHA256 over "id.timestamp.body">`."""
    if not secret or not message_id or not header:
        return False
    try:
        sent = int(timestamp)
        key = base64.b64decode(secret[len("whsec_"):] if secret.startswith("whsec_") else secret, validate=True)
    except (TypeError, ValueError):
        return False
    if not key or abs(now - sent) > SIGNATURE_TOLERANCE_S:
        return False
    expected = base64.b64encode(hmac.new(key, f"{message_id}.{timestamp}.".encode() + raw, hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(expected, given[3:]) for given in header.split() if given.startswith("v1,"))


class LinqChat:
    """The owner's iMessage thread, as conversation.py sees it."""

    def __init__(self, linq: "Linq"):
        self._linq, self._typing = linq, 0.0

    async def send(self, text: str) -> None:
        for part in split_message(text, TEXT_LIMIT):
            await self._linq.post_text(fmt.plain(part) or part)       # this chat shows no marks

    async def draft(self, draft_id: int, text: str) -> None:
        # No reply can be shown as it is written. The "typing" bubble can: it lasts about a minute and a half and is
        # cleared by the reply, so it is asked for at most once a minute. A failure here is nothing.
        now = self._linq.clock()
        if now - self._typing < 60 or not self._linq.chat_id:
            return
        self._typing = now
        try:
            await self._linq.http("POST", f"{API}/chats/{self._linq.chat_id}/typing", {})
        except Exception:
            pass

    async def ask(self, text: str, approval_id: str, thread: bool = False) -> None:
        # The question is sent whole, then a poll whose options are the buttons. Only a vote in that poll answers it.
        await self.send(text)
        try:
            await self._linq.post_poll(approval_id, thread)
        except Exception:
            # No poll could be made. The question has been shown; say how to answer it in words (typed.py).
            await self.send(how(thread))
            self._linq.typed.asked(approval_id, thread, text)


class Linq:
    """Receives what Linq delivers, checks it, and hands the owner's messages to the conversation."""

    def __init__(self, conversation: Conversation, journal: Journal, http: Http, *, number: str, owner_number: str, secret: str,
                 owner_id: int, state_file: Path | None = None, fetch: Fetch | None = None,
                 clock: Callable[[], float] = time.time):
        self._conv, self._journal, self.http, self.clock = conversation, journal, http, clock
        self._number, self._owner, self._secret, self._owner_id = digits(number), digits(owner_number), secret, owner_id
        self._fetch, self.typed = fetch, TypedAnswers(clock)      # answers in words, for a question a poll could not carry
        self._unsigned, self._unsigned_logged, self._late_told = 0, 0.0, 0.0
        self._seen: deque[str] = deque(maxlen=1000)
        self._polls: dict[str, tuple[str, dict[str, str]]] = {}      # a poll's message id -> (approval id, option id -> y, n or t)
        self._tasks: set[asyncio.Task] = set()
        self._state, self.chat_id = state_file, ""
        try:
            if state_file is not None and state_file.exists():
                self.chat_id = str(json.loads(state_file.read_text(encoding="utf-8")).get("chat_id") or "")
        except (OSError, ValueError, AttributeError):
            self.chat_id = ""

    def chat(self) -> LinqChat:
        return LinqChat(self)

    # --- sending ---------------------------------------------------------------------------
    async def _call(self, method: str, url: str, body: Any, what: str) -> Any:
        status, data = await self.http(method, url, body)
        if status >= 300:
            error = data.get("error") if isinstance(data, dict) else None
            detail = {"code": error.get("code"), "message": str(error.get("message", ""))[:200]} if isinstance(error, dict) else {}
            self._journal.write("error", where=what, status=status, **detail)
            raise RuntimeError(f"the iMessage line refused the message ({status})")
        return data

    async def post_text(self, text: str) -> None:
        message = {"message": {"parts": [{"type": "text", "value": text}]}}
        if self.chat_id:
            await self._call("POST", f"{API}/chats/{self.chat_id}/messages", message, "linq.send")
            return
        # No chat is known yet: open the one between this line and the owner. Linq returns the existing chat if there is one.
        made = await self._call("POST", f"{API}/chats", {"from": "+" + self._number, "to": ["+" + self._owner], **message}, "linq.send")
        self._remember(str(((made or {}).get("chat") or {}).get("id") or "") if isinstance(made, dict) else "")

    async def post_poll(self, approval_id: str, thread: bool) -> None:
        if not self.chat_id:
            raise RuntimeError("no chat to put a poll in")
        wanted = [("Approve", "y"), ("Decline", "n")] + ([("Approve, and this thread today", "t")] if thread else [])
        made = await self._call("POST", f"{API}/chats/{self.chat_id}/polls",
                                {"poll": {"options": [{"text": label} for label, _ in wanted], "idempotency_key": f"ap-{approval_id}"}}, "linq.poll")
        options = ((made or {}).get("poll") or {}).get("options") if isinstance(made, dict) else None
        answers = {str(o.get("option_id")): dict(wanted).get(str(o.get("text")), "") for o in options or [] if isinstance(o, dict)}
        if not isinstance(made, dict) or not made.get("message_id") or sorted(answers.values()) != sorted(a for _, a in wanted):
            raise RuntimeError("Linq did not return the poll that was asked for")
        if len(self._polls) >= 50:
            self._polls.pop(next(iter(self._polls)))
        self._polls[str(made["message_id"])] = (approval_id, answers)

    def _remember(self, chat_id: str) -> None:
        if not chat_id or chat_id == self.chat_id:
            return
        self.chat_id = chat_id
        try:
            if self._state is not None:
                self._state.write_text(json.dumps({"chat_id": chat_id}), encoding="utf-8")
        except OSError as e:
            self._journal.write("error", where="linq.state", error=type(e).__name__)

    # --- a delivery ------------------------------------------------------------------------
    def receive(self, raw: bytes, headers: Any) -> int:
        """Check a delivery and start handling it. Returns the status to answer with, at once."""
        if not signature_ok(self._secret, raw, headers.get("webhook-id", ""), headers.get("webhook-timestamp", ""),
                            headers.get("webhook-signature", ""), self.clock()):
            # Anyone can post here; such posts are counted, and at most one line a minute is written (see whatsapp.py).
            self._unsigned += 1
            if self.clock() - self._unsigned_logged >= 60:
                self._journal.write("dropped", what="linq deliveries with a bad signature", count=self._unsigned)
                self._unsigned, self._unsigned_logged = 0, self.clock()
            return 403
        try:
            event = json.loads(raw)
        except ValueError:
            return 200
        if not isinstance(event, dict) or not isinstance(event.get("data"), dict):
            return 200
        task = asyncio.get_running_loop().create_task(self._handle(event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return 200

    async def wait(self) -> None:
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _new(self, *ids: Any) -> bool:
        ids = tuple(str(i) for i in ids if i)
        if any(i in self._seen for i in ids):
            return False
        self._seen.extend(ids)
        return True

    def _from_owner(self, d: dict[str, Any]) -> bool:
        sender, chat = d.get("sender_handle") or {}, d.get("chat") or {}
        ours = digits((chat.get("owner_handle") or {}).get("handle", ""))
        return (d.get("direction") == "inbound" and sender.get("is_me") is not True and chat.get("is_group") is not True
                and digits(sender.get("handle", "")) == self._owner and ours in ("", self._number))

    async def _handle(self, event: dict[str, Any]) -> None:
        try:
            kind, d = event.get("event_type"), event["data"]
            if kind == "poll.vote.added":
                if not self._new(event.get("event_id")):
                    return
                if not self._from_owner(d):
                    self._journal.write("dropped", what="linq vote from a number that is not the owner's")
                    return
                approval_id, answers = self._polls.get(str(d.get("message_id")), ("", {}))
                answer = answers.get(str(d.get("option_id")), "")
                if not answer:
                    return          # a vote in a poll this program is not waiting on
                self._polls.pop(str(d.get("message_id")), None)
                line = (await self._conv.handle_decision(self._owner_id, approval_id, True, thread=True) if answer == "t"
                        else await self._conv.handle_decision(self._owner_id, approval_id, answer == "y"))
                if line:
                    await self.chat().send(line)
                return
            if kind != "message.received" or not self._new(event.get("event_id"), d.get("id")):
                return
            if not self._from_owner(d):
                self._journal.write("dropped", what="linq message from a number that is not the owner's")
                return
            self._remember(str((d.get("chat") or {}).get("id") or ""))
            chat = self.chat()
            try:
                sent = datetime.fromisoformat(str(d.get("sent_at") or "").replace("Z", "+00:00"))
                age = self.clock() - (sent if sent.tzinfo else sent.replace(tzinfo=timezone.utc)).timestamp()
            except ValueError:
                age = 0
            if age > STALE_AFTER_S:
                self._journal.write("dropped", what="linq message that arrived late", minutes=int(age // 60))
                if self.clock() - self._late_told >= 60:
                    self._late_told = self.clock()
                    await chat.send("Something you sent earlier has only just reached me, because I could not be reached at the time. "
                                    "I have not acted on it. Send it again if you still want it.")
                return
            parts = [p for p in d.get("parts") or [] if isinstance(p, dict)]
            text = "\n".join(str(p.get("value") or "") for p in parts if p.get("type") == "text").strip()
            media = [p for p in parts if p.get("type") == "media"]
            if not media:
                found = self.typed.answer(text) if text else None
                if found is not None:
                    approval_id, answer, first = found
                    line = (await self._conv.handle_decision(self._owner_id, approval_id, True, thread=True) if answer == "t"
                            else await self._conv.handle_decision(self._owner_id, approval_id, answer == "y"))
                    await chat.send(f'{line or "Noted."} ("{first}")')
                elif text:
                    await self._conv.handle(self._owner_id, text, chat)
                return
            item = media[0]         # one file to a message, as on the other channels; the words with it are its caption
            size, mime, name = int(item.get("size_bytes") or 0), str(item.get("mime_type") or ""), str(item.get("filename") or "file")
            data = await self._fetch(str(item.get("url") or ""), MAX_BYTES) if self._fetch is not None and size <= MAX_BYTES else None
            if mime.startswith("audio/"):
                await self._conv.handle_voice(self._owner_id, data, name, size or len(data or b""), 0, chat)
            else:
                await self._conv.handle_file(self._owner_id, data, name, mime or None, size or len(data or b""), text, chat)
        except Exception as e:
            self._journal.write("error", where="linq.handle", error=type(e).__name__, message=str(e)[:300])


def make_fetch() -> tuple[Fetch, Callable[[], Awaitable[None]]]:
    """Fetches a file a delivery points at: https only, no token sent, and no more than `most` bytes read."""
    import httpx
    client = httpx.AsyncClient(timeout=60, follow_redirects=False)

    async def fetch(url: str, most: int) -> bytes | None:
        if not url.startswith("https://"):
            return None
        got = bytearray()
        async with client.stream("GET", url) as r:
            if r.status_code != 200:
                return None
            async for piece in r.aiter_bytes():
                got += piece
                if len(got) > most:
                    return None
        return bytes(got)
    return fetch, client.aclose


def attach(linq: Linq, door: Any) -> Callable[[], None]:
    """Answer /linq at the program's one door (door.py). Returns what takes it away again."""
    from aiohttp import web

    async def post(request):
        return web.Response(status=linq.receive(await request.read(), request.headers))
    return door.route("POST", "/linq", post)
