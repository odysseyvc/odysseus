"""WhatsApp as a second channel, through Meta's own WhatsApp Cloud API.

Telegram is asked for messages (the program calls out). WhatsApp is the other way
round: Meta calls a web address when a message arrives. So this channel needs
something Telegram did not: a door. The program listens on a port, and that port
has to be reachable from the internet over HTTPS at an address registered with Meta.

Because there is now a door, three checks stand at it:

  the handshake   when the address is registered, Meta sends a token it was given; it must match
  the signature   every delivery is signed with the app's secret; one that is not is dropped unread
  the sender      only the owner's own number is listened to; anyone else gets no reply at all

All decisions are still made in conversation.py. This file only carries messages,
as telegram.py does.

Two rules of WhatsApp's shape what is possible:

  the 24-hour window   free-form messages can be sent only for 24 hours after the owner last
                       wrote. A reminder that falls outside the window is refused by WhatsApp.
                       Sending outside it needs message templates approved by Meta, which are
                       not built here. The failure is logged, never silent.
  buttons              an approval question carries Approve and Decline as reply buttons. A
                       button message holds 1,024 characters, so a longer question is sent as
                       ordinary messages first, with the buttons on the last part.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections import deque
from typing import Any, Awaitable, Callable

from .conversation import Conversation, split_message
from .journal import Journal
from .ocr import MAX_BYTES

GRAPH = "https://graph.facebook.com/v27.0"
TEXT_LIMIT, BUTTON_BODY_LIMIT = 4096, 1024
VOICE_NOTE = "I can't listen to voice notes yet. Please type it."
OTHER_NOTE = "I can only read text, photos and documents on WhatsApp for now."

# (method, url, json body or None) -> (status, parsed json or raw bytes)
Http = Callable[[str, str, Any], Awaitable[tuple[int, Any]]]


def digits(number: str) -> str:
    return "".join(c for c in str(number) if c.isdigit())


def signature_ok(app_secret: str, raw: bytes, header: str) -> bool:
    """Meta signs each delivery: HMAC-SHA256 of the exact bytes, keyed with the app's secret."""
    if not app_secret or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


class WhatsAppChat:
    """The owner's WhatsApp thread, as conversation.py sees it."""

    def __init__(self, http: Http, phone_id: str, to: str, journal: Journal):
        self._http, self._url, self._to, self._journal = http, f"{GRAPH}/{phone_id}/messages", to, journal

    async def _post(self, body: dict[str, Any]) -> None:
        status, data = await self._http("POST", self._url, {"messaging_product": "whatsapp", "recipient_type": "individual", "to": self._to, **body})
        if status >= 300:
            # Most often: the 24-hour window has closed. Raised so that the caller records the reply as not delivered.
            detail = json.dumps(data)[:300] if not isinstance(data, (bytes, str)) else str(data)[:300]
            self._journal.write("error", where="whatsapp.send", status=status, message=detail)
            raise RuntimeError(f"WhatsApp refused the message ({status})")

    async def send(self, text: str) -> None:
        for part in split_message(text, TEXT_LIMIT):
            await self._post({"type": "text", "text": {"preview_url": False, "body": part}})

    async def draft(self, draft_id: int, text: str) -> None:
        pass      # WhatsApp has no way to show a reply as it is being written

    async def ask(self, text: str, approval_id: str) -> None:
        # The question is sent whole. The buttons go on the last part, so they are reached only after
        # everything they cover has been shown. The buttons carry only the approval's id.
        parts = split_message(text, BUTTON_BODY_LIMIT)
        for part in parts[:-1]:
            await self._post({"type": "text", "text": {"preview_url": False, "body": part}})
        await self._post({"type": "interactive", "interactive": {
            "type": "button", "body": {"text": parts[-1]},
            "action": {"buttons": [{"type": "reply", "reply": {"id": f"ap:{approval_id}:y", "title": "Approve"}},
                                   {"type": "reply", "reply": {"id": f"ap:{approval_id}:n", "title": "Decline"}}]}}})


class WhatsApp:
    """Receives what Meta delivers, checks it, and hands the owner's messages to the conversation."""

    def __init__(self, conversation: Conversation, journal: Journal, http: Http, *, phone_id: str, owner_number: str,
                 app_secret: str, verify_token: str, owner_id: int):
        self._conv, self._journal, self._http = conversation, journal, http
        self._phone_id, self._owner, self._secret, self._verify, self._owner_id = phone_id, digits(owner_number), app_secret, verify_token, owner_id
        self._seen: deque[str] = deque(maxlen=500)      # Meta retries for 36 hours, so the same message can arrive twice
        self._tasks: set[asyncio.Task] = set()

    def chat(self) -> WhatsAppChat:
        return WhatsAppChat(self._http, self._phone_id, self._owner, self._journal)

    # --- the handshake -----------------------------------------------------------------
    def verify(self, query: dict[str, str]) -> tuple[int, str]:
        if query.get("hub.mode") == "subscribe" and self._verify and hmac.compare_digest(query.get("hub.verify_token", ""), self._verify):
            return 200, query.get("hub.challenge", "")
        return 403, ""

    # --- a delivery ----------------------------------------------------------------------
    def receive(self, raw: bytes, signature: str) -> int:
        """Check a delivery and start handling it. Returns the status to answer with, at once: Meta must not be kept waiting."""
        if not signature_ok(self._secret, raw, signature):
            self._journal.write("dropped", what="whatsapp delivery with a bad signature")
            return 403
        try:
            payload = json.loads(raw)
        except ValueError:
            return 200
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                for message in (change.get("value") or {}).get("messages") or []:
                    task = asyncio.get_running_loop().create_task(self._handle(message))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
        return 200

    async def wait(self) -> None:
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _handle(self, m: dict[str, Any]) -> None:
        try:
            if m.get("id") in self._seen:
                return
            self._seen.append(m.get("id"))
            if digits(m.get("from", "")) != self._owner:
                self._journal.write("dropped", what="whatsapp message from a number that is not the owner's")
                return
            chat, kind = self.chat(), m.get("type")
            if kind == "text":
                await self._conv.handle(self._owner_id, (m.get("text") or {}).get("body", ""), chat)
            elif kind == "interactive":
                reply = ((m.get("interactive") or {}).get("button_reply") or {}).get("id", "")
                if reply.startswith("ap:") and reply.count(":") == 2:
                    _, approval_id, answer = reply.split(":")
                    line = await self._conv.handle_decision(self._owner_id, approval_id, answer == "y")
                    if line == "This request is no longer waiting.":
                        await chat.send(line)
            elif kind in ("image", "document"):
                item = m.get(kind) or {}
                name = item.get("filename") or ("photo.jpg" if kind == "image" else "file")
                data, size = await self._download(item.get("id", ""))
                await self._conv.handle_file(self._owner_id, data, name, item.get("mime_type"), size, item.get("caption") or "", chat)
            elif kind == "audio":
                await chat.send(VOICE_NOTE)        # said plainly: silence would read as a broken bot
            else:
                await chat.send(OTHER_NOTE)
        except Exception as e:
            self._journal.write("error", where="whatsapp.handle", error=type(e).__name__, message=str(e)[:300])

    async def _download(self, media_id: str) -> tuple[bytes | None, int]:
        """A file arrives as an id. Meta is asked where it is, and it is fetched with the same token."""
        status, info = await self._http("GET", f"{GRAPH}/{media_id}", None)
        if status >= 300 or not isinstance(info, dict) or not info.get("url"):
            return None, 0
        size = int(info.get("file_size") or 0)
        if size > MAX_BYTES:
            return None, size
        status, data = await self._http("GET", info["url"], None)
        return (data, size or len(data)) if status < 300 and isinstance(data, bytes) else (None, size)


def make_http(token: str) -> tuple[Http, Callable[[], Awaitable[None]]]:
    """The real connection to Meta. The token is sent in a header and never appears in an address or a log."""
    import httpx
    client = httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}"})

    async def http(method: str, url: str, body: Any) -> tuple[int, Any]:
        r = await client.request(method, url, json=body) if body is not None else await client.request(method, url)
        if "application/json" in r.headers.get("content-type", ""):
            return r.status_code, r.json()
        return r.status_code, r.content
    return http, client.aclose


async def serve(whatsapp: WhatsApp, port: int):
    """Open the door: one address, /whatsapp, answering Meta's handshake and its deliveries. Returns the thing to stop it with."""
    from aiohttp import web

    async def get(request):
        status, body = whatsapp.verify(dict(request.query))
        return web.Response(status=status, text=body)

    async def post(request):
        return web.Response(status=whatsapp.receive(await request.read(), request.headers.get("X-Hub-Signature-256", "")))

    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_get("/whatsapp", get)
    app.router.add_post("/whatsapp", post)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    return runner
