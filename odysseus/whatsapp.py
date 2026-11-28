"""WhatsApp as a second channel, through Meta's own WhatsApp Cloud API.

Telegram is asked for messages (the program calls out). WhatsApp is the other way
round: Meta calls a web address when a message arrives. So this channel needs
something Telegram did not: a door. The program listens on a port, and that port
has to be reachable from the internet over HTTPS at an address registered with Meta.

Because there is now a door, three checks stand at it:

  the handshake   when the address is registered, Meta sends a token it was given; it must match
  the signature   every delivery is signed with the app's secret; one that is not is dropped unread
  the sender      only the owner's own number is listened to; anyone else gets no reply at all

Meta no longer promises the number. A delivery always names its sender by an id Meta makes for that person and this
business (`from_user_id`), and leaves the phone number out for a person who has taken a username and has not written
to this number in thirty days. So the first time the owner's number arrives, the id that came with it is kept in the
data folder, and a later delivery with no number is the owner's only if it carries that id. Both values are inside
the signed delivery. A person whose id is not known and whose number is not given gets no reply, as before.

All decisions are still made in conversation.py. This file only carries messages,
as telegram.py does.

Two rules of WhatsApp's shape what is possible:

  the 24-hour window   free-form messages can be sent only for 24 hours after the owner last
                       wrote. A reminder that falls outside the window is refused by WhatsApp.
                       Sending outside it needs message templates approved by Meta, which are
                       not built here. The failure is logged, and a reminder or routine that
                       WhatsApp refuses is sent on Telegram instead (schedule.py).
  late deliveries      Meta retries a delivery for up to 36 hours if the program cannot be
                       reached. A message that arrives more than ten minutes after it was sent
                       is not acted on: the owner is told, and can send it again.
  voice notes          are fetched and handed to the same code that handles Telegram's (voice.py).
  buttons              an approval question carries Approve and Decline as reply buttons. A
                       button message holds 1,024 characters, so a longer question is sent as
                       ordinary messages first, with the buttons on the last part.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections import deque
from typing import Any, Awaitable, Callable

from .conversation import Conversation, split_message
from . import fmt
from .journal import Journal
from .ocr import MAX_BYTES

GRAPH = "https://graph.facebook.com/v27.0"
TEXT_LIMIT, BUTTON_BODY_LIMIT = 4096, 1024
CAPTION_LIMIT = 1000
FILE_LIMIT = 100 * 1024 * 1024       # the largest document this route takes
PICTURES = ("image/jpeg", "image/png")


def file_kind(name: str, kind: str = "document") -> tuple[str, str]:
    """What a file is sent as (image or document) and its type, from its name. A picture goes as a picture only when
    it was asked for as one and WhatsApp shows that type; everything else goes as a document, which keeps its name."""
    import mimetypes
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return ("image" if kind == "photo" and mime in PICTURES else "document"), mime
STALE_AFTER_S = 600       # a message older than this when it arrives is reported, not acted on
OTHER_NOTE = "I can only read text, photos and documents on WhatsApp for now."

# (method, url, json body or None) -> (status, parsed json or raw bytes)
Http = Callable[[str, str, Any], Awaitable[tuple[int, Any]]]


def digits(number: str) -> str:
    return "".join(c for c in str(number) if c.isdigit())


def sent_id(data: Any) -> str:
    """The end of the id a route gave a message it accepted, so that a later report on its delivery can be matched to it."""
    if not isinstance(data, dict):
        return ""
    first = (data.get("messages") or [{}])[0] if isinstance(data.get("messages"), list) else {}
    found = (first.get("id") if isinstance(first, dict) else "") or data.get("meta_message_id") or data.get("message_id") or data.get("message_uuid") or data.get("id") or ""
    return str(found)[-16:]


def size_of(body: dict[str, Any]) -> int:
    """How many characters of text a message being sent carries. Its words are never written down here."""
    text = (body.get("text") or {}).get("body") or ((body.get("interactive") or {}).get("body") or {}).get("text") or ""
    return len(str(text))


def reports(journal: Journal, route: str, statuses: Any) -> None:
    """What the platform says became of messages this program sent: that one reached the phone, or why one did not.
    "sent" and "read" are left out, since they say nothing a delivery does not."""
    for s in statuses if isinstance(statuses, list) else []:
        if not isinstance(s, dict) or s.get("status") not in ("delivered", "failed"):
            continue
        error = (s.get("errors") or [{}])[0] if isinstance(s.get("errors"), list) and s.get("errors") else {}
        journal.write("wa_status", route=route, status=s["status"], id=str(s.get("id") or "")[-16:],
                      **({"code": error.get("code"), "why": str(error.get("title") or error.get("message") or "")[:120]} if error else {}))


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
        self.who = ""       # the sender's name as WhatsApp gives it; used only when someone joins (people.py)
        self.channel = "whatsapp"
        self.route = "whatsapp"     # which of the two ways to WhatsApp this is, for the record of what came and went
        self.heard_id = ""          # the id of the message this chat object was made for, so that it can be reacted to

    async def react(self, emoji: str) -> bool:
        """Put a reaction on the message that was just received (react.py). The other route to WhatsApp, through the
        telephone company's number, has no such call, so its chat has no `react` and nothing is set there."""
        if not self.heard_id:
            return False
        await self._post({"type": "reaction", "reaction": {"message_id": self.heard_id, "emoji": emoji}})
        return True

    async def _post(self, body: dict[str, Any]) -> None:
        status, data = await self._http("POST", self._url, {"messaging_product": "whatsapp", "recipient_type": "individual", "to": self._to, **body})
        if status < 300:
            self._journal.write("wa_out", route=self.route, type=str(body.get("type", ""))[:20], chars=size_of(body), id=sent_id(data))
        if status >= 300:
            # Most often: the 24-hour window has closed. Raised so that the caller records the reply as not delivered.
            detail = json.dumps(data)[:300] if not isinstance(data, (bytes, str)) else str(data)[:300]
            self._journal.write("error", where="whatsapp.send", status=status, message=detail)
            raise RuntimeError(f"WhatsApp refused the message ({status})")

    async def send(self, text: str) -> None:
        for part in split_message(text, TEXT_LIMIT):
            await self._post({"type": "text", "text": {"preview_url": False, "body": fmt.whatsapp(part) or part}})

    async def draft(self, draft_id: int, text: str) -> None:
        pass      # WhatsApp has no way to show a reply as it is being written

    async def send_file(self, name: str, data: bytes | None = None, handle: str = "", kind: str = "document", caption: str = "") -> None:
        """A file the assistant made or kept, sent into this thread. It is handed to WhatsApp first and then named in a
        message, so no address of the file exists anywhere for a moment. `handle` is another chat's own name for a
        file and means nothing here: without the bytes there is nothing to send, and the caller falls back to its copy."""
        if not data:
            raise ValueError("no file to send")
        if len(data) > FILE_LIMIT:
            raise ValueError("the file is too large for WhatsApp")
        upload = getattr(self._http, "upload", None)
        if upload is None:
            raise RuntimeError("files cannot be sent on this route")
        sent_as, mime = file_kind(name, kind)
        status, got = await upload(self._url.rsplit("/", 1)[0] + "/media", {"messaging_product": "whatsapp"}, name, data, mime)
        media_id = str(got.get("id") or "") if isinstance(got, dict) else ""
        if status >= 300 or not media_id:
            self._journal.write("error", where="whatsapp.upload", status=status)
            raise RuntimeError(f"WhatsApp did not take the file ({status})")
        about = fmt.whatsapp(caption)[:CAPTION_LIMIT]
        await self._post({"type": sent_as, sent_as: {"id": media_id, **({"filename": name[:200]} if sent_as == "document" else {}),
                                                     **({"caption": about} if about else {})}})

    async def status(self, line: str) -> None:
        """A few fixed words on what is being done, sent once when a task turns out to need a step (conversation.py)."""
        await self.send(line)

    async def ask(self, text: str, approval_id: str, thread: bool = False) -> None:
        # The question is sent whole. The buttons go on the last part, so they are reached only after
        # everything they cover has been shown. The buttons carry only the approval's id.
        parts = split_message(text, BUTTON_BODY_LIMIT)
        for part in parts[:-1]:
            await self._post({"type": "text", "text": {"preview_url": False, "body": part}})
        await self._post({"type": "interactive", "interactive": {
            "type": "button", "body": {"text": parts[-1]},
            "action": {"buttons": [{"type": "reply", "reply": {"id": f"ap:{approval_id}:y", "title": "Approve"}},
                                   {"type": "reply", "reply": {"id": f"ap:{approval_id}:n", "title": "Decline"}},
                                   *([{"type": "reply", "reply": {"id": f"ap:{approval_id}:t", "title": "Approve thread today"}}] if thread else [])]}}})


class WhatsApp:
    """Receives what Meta delivers, checks it, and hands the owner's messages to the conversation."""

    def __init__(self, conversation: Conversation, journal: Journal, http: Http, *, phone_id: str, owner_number: str,
                 app_secret: str, verify_token: str, owner_id: int, clock: Callable[[], float] = time.time,
                 ids_file: Any = None, groups: Callable[[], Any] | None = None, people: Callable[[], Any] | None = None):
        self._conv, self._journal, self._http = conversation, journal, http
        # Everyone who has an assistant here (people.py), once that exists; asked for at each delivery. With it, a person
        # the host invited is heard on this number too, each by their own assistant. Without it, only the owner is.
        self._people = people
        self.number = ""            # the number itself, where the route knows it: what an invitation link opens
        self._groups = groups       # what serves a WhatsApp group, if anything does yet (whatsapp_group.py); asked for at each delivery
        self._clock = clock
        self._unsigned, self._unsigned_logged = 0, 0.0      # unsigned posts since the last log line, and when that was
        self._late_told = 0.0
        self._phone_id, self._owner, self._secret, self._verify, self._owner_id = phone_id, digits(owner_number), app_secret, verify_token, owner_id
        self._seen: deque[str] = deque(maxlen=500)      # Meta retries for 36 hours, so the same message can arrive twice
        self._tasks: set[asyncio.Task] = set()
        self._ids_file, self._owner_user = ids_file, ""       # the id Meta gives the owner, learned from a delivery that also had the number
        try:
            if ids_file is not None and ids_file.exists():
                self._owner_user = str(json.loads(ids_file.read_text(encoding="utf-8")).get("owner_user_id") or "")
        except (OSError, ValueError, AttributeError):
            self._owner_user = ""

    def _is_owner(self, m: dict[str, Any]) -> bool:
        number, user = digits(m.get("from", "")), str(m.get("from_user_id") or "")
        if number:
            if number != self._owner:
                return False
            if user and user != self._owner_user:
                self._owner_user = user
                try:
                    if self._ids_file is not None:
                        self._ids_file.write_text(json.dumps({"owner_user_id": user}), encoding="utf-8")
                except OSError as e:
                    self._journal.write("error", where="whatsapp.ids", error=type(e).__name__)
            return True
        return bool(user) and user == self._owner_user

    def chat(self, to: str = "") -> WhatsAppChat:
        return WhatsAppChat(self._http, self._phone_id, to or self._owner, self._journal)

    def _person(self, m: dict[str, Any]) -> tuple[int, str, Any] | None:
        """Who sent this, if they may be heard: their id, their number, and what hears them. The number comes from the
        signed delivery, which the sender cannot choose."""
        house = self._people() if self._people is not None else None
        if self._is_owner(m):
            return self._owner_id, self._owner, house or self._conv
        number = digits(m.get("from", ""))
        if house is None or not number:
            return None
        from .people import wa_id
        uid = wa_id(number)
        return (uid, number, house) if uid and house.is_owner(uid) else None

    async def _joining(self, m: dict[str, Any]) -> bool:
        """A stranger sending exactly the message an invitation link writes. The code is checked where every code is
        (people.py): a wrong one gets no reply, and a few wrong ones in an hour and nothing more is looked at."""
        from .people import JOIN, wa_id
        house = self._people() if self._people is not None else None
        number = digits(m.get("from", ""))
        found = JOIN.match((m.get("text") or {}).get("body", "")) if m.get("type") == "text" else None
        if house is None or found is None or not wa_id(number):
            return False
        chat = self.chat(number)
        chat.who = str(m.get("_name") or "")
        await house.handle(wa_id(number), "/start " + found.group(1), chat)
        return True

    @staticmethod
    def _name(value: dict[str, Any], m: dict[str, Any]) -> None:
        """Put the sender's own name, which comes beside the message and not in it, where a joining can find it."""
        for c in value.get("contacts") or []:
            if digits(c.get("wa_id", "")) == digits(m.get("from", "")) and digits(m.get("from", "")):
                m["_name"] = str((c.get("profile") or {}).get("name") or "")[:80]

    # --- the handshake -----------------------------------------------------------------
    def verify(self, query: dict[str, str]) -> tuple[int, str]:
        if query.get("hub.mode") == "subscribe" and self._verify and hmac.compare_digest(query.get("hub.verify_token", ""), self._verify):
            return 200, query.get("hub.challenge", "")
        return 403, ""

    # --- a delivery ----------------------------------------------------------------------
    def receive(self, raw: bytes, signature: str) -> int:
        """Check a delivery and start handling it. Returns the status to answer with, at once: Meta must not be kept waiting."""
        if not signature_ok(self._secret, raw, signature):
            # Anyone on the internet can post here. Each such post used to add a line to the journal, which is read whole
            # at start-up: a way for a stranger to fill the disk. Now they are counted, and one line a minute is written.
            self._unsigned += 1
            if self._clock() - self._unsigned_logged >= 60:
                self._journal.write("dropped", what="whatsapp deliveries with a bad signature", count=self._unsigned)
                self._unsigned, self._unsigned_logged = 0, self._clock()
            return 403
        try:
            payload = json.loads(raw)
        except ValueError:
            return 200
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                groups = self._groups() if self._groups is not None else None
                if groups is not None and groups.take(change):
                    continue        # about a group: dealt with there, and never as something the owner said to the number
                line = str((value.get("metadata") or {}).get("phone_number_id") or "")
                if line and self._phone_id and line != self._phone_id:
                    continue        # for another number of the same account, which another route may be serving (vobiz.py)
                reports(self._journal, "whatsapp", value.get("statuses"))
                for message in value.get("messages") or []:
                    self._name(value, message)
                    task = asyncio.get_running_loop().create_task(self._handle(message))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
        return 200

    route = "whatsapp"

    def _came(self, m: dict[str, Any], heard: bool) -> None:
        """One line for each message that arrived: what kind, how long, how old, and whether its sender is heard here."""
        try:
            age = round(self._clock() - int(m.get("timestamp"))) if m.get("timestamp") else None
        except (TypeError, ValueError):
            age = None
        self._journal.write("wa_in", route=self.route, type=str(m.get("type", ""))[:20], chars=len((m.get("text") or {}).get("body", "") or ""),
                            heard=heard, **({"age_s": age} if age is not None else {}))

    async def wait(self) -> None:
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _handle(self, m: dict[str, Any]) -> None:
        try:
            if m.get("id") in self._seen or m.get("group_id"):
                # Seen before, or said in a group. What the owner says in a group is said to everyone there; it is never
                # taken as an instruction to their own assistant, whether or not anything serves that group.
                return
            self._seen.append(m.get("id"))
            person = self._person(m)
            self._came(m, person is not None)
            if person is None:
                if not await self._joining(m):
                    self._journal.write("dropped", what="whatsapp message from a number that is not the owner's")
                return
            who, number, conv = person
            chat, kind = self.chat(number), m.get("type")
            try:
                age = self._clock() - int(m.get("timestamp") or 0) if m.get("timestamp") else 0
            except (TypeError, ValueError):
                age = 0
            if age > STALE_AFTER_S:
                # Delivered long after it was sent, most likely because the program could not be reached. An hours-old
                # instruction is not run as if it had just been given.
                self._journal.write("dropped", what="whatsapp message that arrived late", minutes=int(age // 60))
                if self._clock() - self._late_told >= 60:
                    self._late_told = self._clock()
                    await chat.send("Something you sent earlier has only just reached me, because I could not be reached at the time. "
                                    "I have not acted on it. Send it again if you still want it.")
                return
            if kind == "text":
                chat.heard_id = str(m.get("id") or "")
                await conv.handle(who, (m.get("text") or {}).get("body", ""), chat)
            elif kind == "interactive":
                reply = ((m.get("interactive") or {}).get("button_reply") or {}).get("id", "")
                if reply.startswith("ap:") and reply.count(":") == 2:
                    _, approval_id, answer = reply.split(":")
                    line = (await conv.handle_decision(who, approval_id, True, thread=True) if answer == "t"
                            else await conv.handle_decision(who, approval_id, answer == "y"))
                    if line == "This request is no longer waiting.":
                        await chat.send(line)
            elif kind in ("image", "document"):
                item = m.get(kind) or {}
                name = item.get("filename") or ("photo.jpg" if kind == "image" else "file")
                data, size = await self._download(item.get("id", ""))
                await conv.handle_file(who, data, name, item.get("mime_type"), size, item.get("caption") or "", chat)
            elif kind == "audio":
                data, size = await self._download((m.get("audio") or {}).get("id", ""))
                # WhatsApp does not say how long a recording is, so only its size is checked here.
                await conv.handle_voice(who, data, "voice.ogg", size, 0, chat)
            elif kind == "system":
                # Meta's own notice (the person changed their number or their id), not something the owner wrote.
                self._journal.write("whatsapp", notice=str((m.get("system") or {}).get("type", ""))[:40])
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

    async def upload(url: str, fields: dict[str, str], name: str, data: bytes, mime: str) -> tuple[int, Any]:
        """Hand a file over as a form. Only for the address that takes files: a file is never put in a message's own body."""
        r = await client.post(url, data=fields, files={"file": (name, data, mime)}, timeout=120)
        return r.status_code, (r.json() if "application/json" in r.headers.get("content-type", "") else r.content)
    http.upload = upload
    return http, client.aclose


def attach(whatsapp: WhatsApp, door: Any) -> Callable[[], None]:
    """Answer /whatsapp at the program's one door (door.py). Returns what takes it away again."""
    from aiohttp import web

    async def get(request):
        status, body = whatsapp.verify(dict(request.query))
        return web.Response(status=status, text=body)

    async def post(request):
        return web.Response(status=whatsapp.receive(await request.read(), request.headers.get("X-Hub-Signature-256", "")))
    off = [door.route("GET", "/whatsapp", get), door.route("POST", "/whatsapp", post)]
    return lambda: [f() for f in off] and None


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
