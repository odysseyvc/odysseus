"""The group's assistant in a WhatsApp group, through Meta's Groups API.

A WhatsApp business number cannot be added to a group that people already have. Meta's Groups API lets the number
make a group of its own, of at most eight people, who join by a link. Meta opens that API only to a number with
Official Business Account status; until it does, every call is refused and this file does nothing. It is written from
Meta's documentation and has not run against Meta.

What this file does is carry. The rules of a group chat are the ones Telegram's group already has (people.py): one
group, switched on by the host typing /firm on in it; an assistant of its own with nobody's mail or calendar; heard
only by people this copy knows, and only when spoken to.

Three things are different here, each because of what Meta gives:

  who is speaking    Telegram names every sender by an id this copy already knows. WhatsApp gives a phone number, or
                     only Meta's own id for the person. So the host is known by the owner's number, and anyone else
                     only if the host has listed their number against their assistant (WHATSAPP_GROUP_MEMBERS). The
                     id Meta sends beside a known number is kept, for the day the number is left out.
  spoken to          Meta's documentation shows no mention and no reply-to in a group message. So a message is to the
                     assistant when it begins with the assistant's name, or has @name in it. Code decides, from the
                     text alone.
  approving          a group message cannot carry buttons. A question ends by saying how to answer in a word, and one
                     exact word from a person who is heard answers the latest question waiting (typed.py).

Deliveries arrive at the same door and under the same signature as the direct route (whatsapp.py), which hands over
what belongs here: anything about a group, for this number.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections import deque
from typing import Any, Awaitable, Callable

from .conversation import split_message
from . import fmt
from .journal import Journal
from .typed import TypedAnswers, how
from .whatsapp import GRAPH, STALE_AFTER_S, TEXT_LIMIT, Http, digits

GROUP_FIELDS = ("group_lifecycle_update", "group_participants_update", "group_settings_update", "group_status_update")
CHAT_BASE = 10 ** 15        # a WhatsApp group's place among chat ids: far from any Telegram person (positive, smaller) or group (negative)


def chat_id_of(group_id: str) -> int:
    """A number for a WhatsApp group, the same every time, for the code that knows chats by number (people.py)."""
    return CHAT_BASE + int(hashlib.sha256(str(group_id).encode()).hexdigest()[:12], 16)


def spoken_to(text: str, name: str) -> tuple[str, bool]:
    """The message with the assistant's name taken out, and whether it was said to the assistant: it begins with the
    name, or has @name anywhere."""
    text = (text or "").strip()
    if not name:
        return text, False
    n = re.escape(name)
    start = re.match(rf"(?i)@?{n}\b[\s,:;.!?\-]*", text)
    if start:
        return text[start.end():].strip(), True
    at = re.compile(rf"(?i)(?<![\w@])@{n}\b")
    if at.search(text):
        return " ".join(at.sub(" ", text).split()), True
    return text, False


def members_from(setting: str) -> dict[str, int]:
    """WHATSAPP_GROUP_MEMBERS, "number=id, number=id": whose WhatsApp number is which person's assistant here."""
    out: dict[str, int] = {}
    for part in (setting or "").replace(";", ",").split(","):
        number, _, user = part.partition("=")
        if digits(number) and user.strip().lstrip("-").isdigit():
            out[digits(number)] = int(user.strip())
    return out


class WhatsAppGroupChat:
    """The group's thread, as conversation.py sees it."""

    def __init__(self, http: Http, phone_id: str, group_id: str, journal: Journal, typed: TypedAnswers):
        self._http, self._url, self._to, self._journal, self._typed = http, f"{GRAPH}/{phone_id}/messages", group_id, journal, typed

    async def send(self, text: str) -> None:
        for part in split_message(text, TEXT_LIMIT):
            status, data = await self._http("POST", self._url, {"messaging_product": "whatsapp", "recipient_type": "group", "to": self._to,
                                                                "type": "text", "text": {"preview_url": False, "body": fmt.whatsapp(part) or part}})
            if status >= 300:
                detail = json.dumps(data)[:300] if not isinstance(data, (bytes, str)) else str(data)[:300]
                self._journal.write("error", where="whatsapp_group.send", status=status, message=detail)
                raise RuntimeError(f"WhatsApp refused the group message ({status})")

    async def draft(self, draft_id: int, text: str) -> None:
        pass

    async def ask(self, text: str, approval_id: str, thread: bool = False) -> None:
        # No "the rest of this thread" here: a group's question is answered once, by whoever answers it.
        await self.send(text + "\n\n" + how(False))
        self._typed.asked(approval_id, False, text)


class WhatsAppGroups:
    """Takes what Meta delivers about groups for one number and hands what was said to the assistant to the house.

    `house` is anything with handle_group(user_id, chat_id, text, chat, addressed=) and handle_group_decision(user_id,
    chat_id, approval_id, approved), as people.House has. `tell` sends the host a line on their own channel.
    """

    def __init__(self, house: Any, journal: Journal, http: Http, *, phone_id: str, owner_number: str, owner_id: int,
                 members: dict[str, int] | None = None, name: str = "odysseus", tell: Callable[[str], Awaitable[None]] | None = None,
                 state_file: Any = None, clock: Callable[[], float] = time.time):
        self._house, self._journal, self._http, self._phone_id = house, journal, http, str(phone_id)
        self._numbers = {**(members or {}), digits(owner_number): owner_id}       # the host's own number always wins
        self._name, self._tell, self._file, self._clock = name, tell, state_file, clock
        self._typed = TypedAnswers(clock)
        self._seen: deque[str] = deque(maxlen=500)
        self._tasks: set[asyncio.Task] = set()
        self._state: dict[str, Any] = {"group_id": "", "subject": "", "users": {}}
        try:
            saved = json.loads(state_file.read_text(encoding="utf-8")) if state_file is not None and state_file.exists() else {}
            if isinstance(saved, dict):
                self._state.update({k: saved[k] for k in ("group_id", "subject") if isinstance(saved.get(k), str)})
                self._state["users"] = {str(k): int(v) for k, v in (saved.get("users") or {}).items() if isinstance(v, int)}
        except (OSError, ValueError, AttributeError):
            pass

    @property
    def group_id(self) -> str:
        return self._state["group_id"]

    def _save(self) -> None:
        try:
            if self._file is not None:
                self._file.write_text(json.dumps(self._state), encoding="utf-8")
        except OSError as e:
            self._journal.write("error", where="whatsapp_group.state", error=type(e).__name__)

    def chat(self) -> WhatsAppGroupChat:
        return WhatsAppGroupChat(self._http, self._phone_id, self.group_id, self._journal, self._typed)

    # --- what belongs here ----------------------------------------------------------------
    def take(self, change: dict[str, Any]) -> bool:
        """Called by the door for each signed change. True if it was about a group, in which case it is dealt with here
        and nowhere else."""
        field, value = change.get("field"), change.get("value") or {}
        if field in GROUP_FIELDS:
            for g in value.get("groups") or []:
                self._start(self._notice(str(field), g))
            return True
        if str((value.get("metadata") or {}).get("phone_number_id") or "") != self._phone_id:
            return False
        said = [m for m in value.get("messages") or [] if m.get("group_id")]
        for m in said:
            self._start(self._handle(m))
        # A delivery holds either what was said in a group or what was said to the number alone. The second is not ours.
        return bool(said)

    def _start(self, work: Awaitable[None]) -> None:
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait(self) -> None:
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # --- who is speaking ------------------------------------------------------------------
    def _who(self, m: dict[str, Any]) -> int | None:
        number, user = digits(m.get("from", "")), str(m.get("from_user_id") or "")
        if number:
            known = self._numbers.get(number)
            if known is not None and user and self._state["users"].get(user) != known:
                self._state["users"][user] = known
                self._save()
            return known
        return self._state["users"].get(user) if user else None

    # --- something said in the group ------------------------------------------------------
    async def _handle(self, m: dict[str, Any]) -> None:
        try:
            if m.get("id") in self._seen:
                return
            self._seen.append(m.get("id"))
            # Not this copy's group, not text, or from someone this copy does not know: never read further, and not logged.
            if not self.group_id or m.get("group_id") != self.group_id or m.get("type") != "text":
                return
            user_id = self._who(m)
            if user_id is None:
                return
            try:
                if m.get("timestamp") and self._clock() - int(m["timestamp"]) > STALE_AFTER_S:
                    self._journal.write("dropped", what="whatsapp group message that arrived late")
                    return
            except (TypeError, ValueError):
                pass
            body, chat, chat_id = (m.get("text") or {}).get("body", ""), self.chat(), chat_id_of(self.group_id)
            found = self._typed.answer(body)
            if found is not None:
                approval_id, answer, first = found
                line = await self._house.handle_group_decision(user_id, chat_id, approval_id, answer == "y")
                if line:
                    await chat.send(f'{line} ("{first}")')
                return
            text, addressed = spoken_to(body, self._name)
            await self._house.handle_group(user_id, chat_id, text, chat, addressed=addressed)
        except Exception as e:
            self._journal.write("error", where="whatsapp_group.handle", error=type(e).__name__, message=str(e)[:300])

    # --- what Meta says about the group itself --------------------------------------------
    async def _notice(self, field: str, g: dict[str, Any]) -> None:
        try:
            kind, group_id, failed = str(g.get("type") or ""), str(g.get("group_id") or ""), bool(g.get("errors"))
            self._journal.write("whatsapp_group", field=field, type=kind[:40], failed=failed,
                                people=len(g.get("added_participants") or g.get("removed_participants") or []))
            line = ""
            if kind == "group_create" and failed:
                line = "WhatsApp did not make the group. The reason is in the log."
            elif kind == "group_create" and group_id:
                if self.group_id and self.group_id != group_id:
                    line = ("WhatsApp made another group, and I already serve one there. I will not answer in the new one. "
                            "Delete one of them: there is one group assistant.")
                else:
                    self._state.update(group_id=group_id, subject=str(g.get("subject") or "")[:128])
                    self._save()
                    line = (f"The WhatsApp group \"{self._state['subject']}\" exists. People join with this link, and up to eight can: "
                            f"{g.get('invite_link') or '(no link came with it; ask for one with scripts/whatsapp_group.py link)'}\n"
                            f"Join it yourself and send /firm on there to switch me on. I answer a message that begins with my name, "
                            f"\"{self._name}\", from you and from the people whose numbers you have listed.")
            elif kind == "group_delete" and group_id and group_id == self.group_id and not failed:
                self._state.update(group_id="", subject="")
                self._save()
                line = "The WhatsApp group was deleted. I no longer serve a group there."
            elif kind == "group_suspend" and group_id == self.group_id:
                line = "WhatsApp has suspended the group I serve there. Nothing can be sent in it until WhatsApp lifts that."
            if line and self._tell is not None:
                await self._tell(line)
        except Exception as e:
            self._journal.write("error", where="whatsapp_group.notice", error=type(e).__name__, message=str(e)[:300])
