"""WhatsApp by a second route: through Vobiz, a telephony company that sells WhatsApp numbers and its own interface to
them, in place of this program speaking to Meta itself (whatsapp.py).

The owner chose it to get a number without buying and registering one. The WhatsApp account is still the owner's own
at Meta; Vobiz holds a token for it, receives Meta's deliveries and passes them on, and sends on the program's behalf.
So one more company carries every message on this route and keeps a readable copy. It is off unless its five settings
are present, and ODYSSEUS_VOBIZ=off switches it off without removing them. The direct route is untouched and both can
be on at once, each for its own number.

What is the same as the direct route, because it is the same code: Vobiz wraps Meta's own delivery in an envelope and
passes it on unchanged, so everything after the door (only the owner's number, once only, nothing acted on late,
files and voice notes handed over) is whatsapp.py's `_handle`.

What differs, and is all this file holds:

  the signature   Vobiz signs each delivery with a secret this program chose when it registered its address:
                  HMAC-SHA256 of the exact bytes, in hex, with no prefix. There is no timestamp in it, so a recorded
                  delivery could be posted again; it would be the same message id, which is handled once, and after
                  ten minutes it is a late message, which is not acted on.
  the number      a delivery is read only if it is for the number this route was set up with.
  sending         Vobiz's own shape, to its own address, with the account's id and token in headers.
  no buttons      Vobiz documents no way to send a message with reply buttons. The owner asked not to be sent to
                  another app for these, so an approval question is asked here in words and answered by one exact
                  word, read by code and never by a model (typed.py states the exception and its limits). If the
                  owner's tap on a button does arrive (Meta's shape allows it), it is handled as on the direct route.
  the window      Vobiz itself refuses a free-form message when no conversation is open (the 24 hours). The refusal
                  is raised, so a reminder sent here falls back to Telegram as on the direct route.
  files           fetched from Vobiz by the id in the delivery, with the account's headers.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from .conversation import Conversation, split_message
from . import fmt
from .journal import Journal
from .ocr import MAX_BYTES
from .typed import TypedAnswers, how
from .whatsapp import STALE_AFTER_S, TEXT_LIMIT, Http, WhatsApp, digits, reports, sent_id, signature_ok, size_of


def base(auth_id: str) -> str:
    return f"https://api.vobiz.ai/api/v1/Account/{auth_id}/WhatsApp"


class VobizChat:
    """The owner's WhatsApp thread on the Vobiz number, as conversation.py sees it."""

    def __init__(self, http: Http, url: str, channel_id: str, to: str, journal: Journal, typed: TypedAnswers):
        self._http, self._url, self._channel, self._to, self._journal, self._typed = http, url, channel_id, to, journal, typed
        self.who = ""       # the sender's name as WhatsApp gives it; used only when someone joins (people.py)
        self.channel = "whatsapp"
        self.route = "vobiz"
        # Said once to a person who joins on this number, after the welcome: one more company carries what they send.
        self.welcome_more = "\n\nOne more thing about this number: messages on it pass through the telephone company that provides it, which can read them."

    async def _post(self, body: dict[str, Any]) -> None:
        status, data = await self._http("POST", self._url, {"channel_id": self._channel, "to": "+" + self._to, **body})
        if status < 300:
            self._journal.write("wa_out", route=self.route, type=str(body.get("type", ""))[:20], chars=size_of(body), id=sent_id(data))
        if status >= 300:
            # Most often: no conversation is open, which is the 24-hour window. Raised so the reply is not counted as delivered.
            error = data.get("error") if isinstance(data, dict) else data
            self._journal.write("error", where="vobiz.send", status=status, message=(json.dumps(error) if isinstance(error, dict) else str(error))[:300])
            raise RuntimeError(f"the WhatsApp line refused the message ({status})")

    async def send(self, text: str) -> None:
        for part in split_message(text, TEXT_LIMIT):
            await self._post({"type": "text", "text": {"body": fmt.whatsapp(part) or part}})

    async def draft(self, draft_id: int, text: str) -> None:
        pass

    async def status(self, line: str) -> None:
        """A few fixed words on what is being done, sent once when a task turns out to need a step (conversation.py)."""
        await self.send(line)

    async def ask(self, text: str, approval_id: str, thread: bool = False) -> None:
        # The question is sent whole, and how to answer it comes last. It counts as asked only once it has been sent.
        await self.send(text + "\n\n" + how(thread))
        self._typed.asked(approval_id, thread, text)


class Vobiz(WhatsApp):
    """Receives what Vobiz passes on, checks it, and hands the owner's messages to the conversation."""
    route = "vobiz"

    def __init__(self, conversation: Conversation, journal: Journal, http: Http, *, auth_id: str, channel_id: str, number: str,
                 owner_number: str, secret: str, owner_id: int, **more: Any):
        super().__init__(conversation, journal, http, phone_id="", owner_number=owner_number, app_secret=secret, verify_token="",
                         owner_id=owner_id, **more)
        self._base, self._channel, self._number = base(auth_id), channel_id, digits(number)
        self._typed = TypedAnswers(self._clock)
        self._typed_by = {self._owner: self._typed}       # each person's own waiting questions: one person's word never answers another's
        self.number = self._number
        self._line_id = ""          # Meta's id for the number, as the last delivery gave it; needed to fetch a file

    def chat(self, to: str = "") -> VobizChat:
        to = to or self._owner
        if to not in self._typed_by:
            self._typed_by[to] = TypedAnswers(self._clock)
        return VobizChat(self._http, f"{self._base}/Message/", self._channel, to, self._journal, self._typed_by[to])

    async def _handle(self, m: dict[str, Any]) -> None:
        # One exact word from the owner, while a question asked here is waiting, answers it (typed.py). The checks the
        # direct route makes for every message are made here first for this one: once, the owner, and not late.
        if m.get("group_id"):
            return          # said in a group, not to the number: never an answer to a question asked of the owner alone
        body = (m.get("text") or {}).get("body", "") if m.get("type") == "text" else ""
        person = self._person(m) if body and m.get("id") not in self._seen and not self._late(m) else None
        if person is not None:
            who, number, conv = person
            found = self._typed_by[number].answer(body) if number in self._typed_by else None
            if found is not None:
                self._seen.append(m.get("id"))
                self._came(m, True)
                approval_id, answer, first = found
                try:
                    line = (await conv.handle_decision(who, approval_id, True, thread=True) if answer == "t"
                            else await conv.handle_decision(who, approval_id, answer == "y"))
                    await self.chat(number).send(f'{line or "Noted."} ("{first}")')
                except Exception as e:
                    self._journal.write("error", where="vobiz.answer", error=type(e).__name__, message=str(e)[:300])
                return
        await super()._handle(m)

    def _late(self, m: dict[str, Any]) -> bool:
        try:
            return bool(m.get("timestamp")) and self._clock() - int(m["timestamp"]) > STALE_AFTER_S
        except (TypeError, ValueError):
            return False

    def verify(self, query: dict[str, str]) -> tuple[int, str]:
        return 403, ""          # there is no handshake on this route: the address is registered with a secret instead

    def receive(self, raw: bytes, signature: str) -> int:
        if not signature_ok(self._secret, raw, "sha256=" + signature.strip().lower() if signature else ""):
            self._unsigned += 1
            if self._clock() - self._unsigned_logged >= 60:
                self._journal.write("dropped", what="vobiz deliveries with a bad signature", count=self._unsigned)
                self._unsigned, self._unsigned_logged = 0, self._clock()
            return 403
        try:
            event = json.loads(raw)
        except ValueError:
            return 200
        if not isinstance(event, dict) or event.get("event_type") != "message.inbound" or not isinstance(event.get("payload"), dict):
            self._reported(event)
            return 200          # a status report, or something this program does not use
        for entry in event["payload"].get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                line = value.get("metadata") or {}
                if digits(line.get("display_phone_number", "")) != self._number:
                    continue        # for another number of the account
                self._line_id = str(line.get("phone_number_id") or self._line_id)
                for message in value.get("messages") or []:
                    self._name(value, message)
                    task = asyncio.get_running_loop().create_task(self._handle(message))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
        return 200

    def _reported(self, event: Any) -> None:
        """A report on a message this program sent. Its form on this route is not documented well enough to rely on, so
        it is read in the platform's own form where that is there, and otherwise only its kind and outcome are kept."""
        if not isinstance(event, dict):
            return
        payload, found = event.get("payload") if isinstance(event.get("payload"), dict) else {}, False
        for entry in payload.get("entry") or []:
            for change in (entry.get("changes") or []) if isinstance(entry, dict) else []:
                statuses = ((change.get("value") or {}).get("statuses")) if isinstance(change, dict) else None
                if statuses:
                    found = True
                    reports(self._journal, "vobiz", statuses)
        outcome = str(payload.get("status") or event.get("status") or "")[:20]
        if not found and outcome not in ("sent", "read"):
            self._journal.write("wa_status", route="vobiz", event=str(event.get("event_type") or "")[:40], status=outcome,
                                id=str(payload.get("message_id") or payload.get("id") or "")[-16:])

    async def _download(self, media_id: str) -> tuple[bytes | None, int]:
        """A file arrives as an id. Vobiz hands over its bytes for that id and the number it came to."""
        if not media_id or not self._line_id:
            return None, 0
        status, data = await self._http("GET", f"{self._base}/Media/{media_id}/?phone_number_id={self._line_id}", None)
        if status >= 300 or not isinstance(data, bytes):
            return None, 0
        return (data, len(data)) if len(data) <= MAX_BYTES else (None, len(data))


def make_http(auth_id: str, auth_token: str) -> tuple[Http, Callable[[], Awaitable[None]]]:
    """The real connection to Vobiz. The id and token go in headers and never appear in an address or a log."""
    import httpx
    client = httpx.AsyncClient(timeout=60, headers={"X-Auth-ID": auth_id, "X-Auth-Token": auth_token})

    async def http(method: str, url: str, body: Any) -> tuple[int, Any]:
        r = await client.request(method, url, json=body) if body is not None else await client.request(method, url)
        if "application/json" in r.headers.get("content-type", ""):
            return r.status_code, r.json()
        return r.status_code, r.content
    return http, client.aclose


def attach(vobiz: Vobiz, door: Any) -> Callable[[], None]:
    """Answer /vobiz at the program's one door (door.py). Returns what takes it away again."""
    from aiohttp import web

    async def post(request):
        return web.Response(status=vobiz.receive(await request.read(), request.headers.get("X-Webhook-Signature", "")))
    return door.route("POST", "/vobiz", post)
