"""More than one person on one running copy: who may speak, how a new person joins, and which assistant hears them.

One person, the host, runs the program and holds the keys. Everyone else arrives by a link the host made:

  the invite     the host sends /invite. Code makes a random code, keeps only its hash, and replies with a link that
                 opens the bot with the code attached. A code works once and for seven days.
  joining        the new person opens the link and taps Start. Telegram sends "/start CODE" from their account. Code
                 checks the hash, records their Telegram id, builds an assistant for them and sends a welcome with
                 the sign-in links for their own mail and calendar. No model is involved in any of this.
  the separation each person gets everything the host has, built again from nothing: a folder of their own for
                 memory, the log, the schedule and connections; their own agents, gate and daily budget; their own
                 sign-ins, named by their Telegram id. Nothing is shared but the keys and the bot. A message is
                 routed by the id Telegram attaches to it, which the sender cannot choose.
  the host       /people lists who is here and what each has spent today. /remove and an id stops a person's
                 assistant. Their folder is left where it is, for the host to keep or delete by hand.

Anyone not on the list, sending anything but a valid code, gets no reply at all, as before.

What this does not give: privacy from the host. The host's machine holds every person's folder in the clear, and the
welcome says so. It is an arrangement for a firm, where the person who runs it is trusted, and not for strangers.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from .journal import Journal

INVITE_DAYS = 7
OPEN_INVITES = 20            # codes not yet used, at most
TRIES = 5                    # wrong codes one stranger may send in an hour before nothing they send is looked at
ALL_TRIES = 60               # and from every stranger together
NAME_MAX = 40

WELCOME = ("You're in. I'm an assistant, and this chat is yours alone: nobody else who uses me can see it or what I "
           "remember about you.\n\n"
           "Message me as you would a colleague. I can search, remember, remind, read the files and decks you send, and, "
           "once you sign in below, read your mail and calendar. Anything that sends, shares, deletes or overwrites waits "
           "for your tap.\n\n"
           "One thing to know: I run on a machine kept by the person who invited you. What you send me and what I read "
           "from your accounts is stored there, and they could read it.\n\n"
           "Send /help for the commands.")
CONNECT = "To let me read your mail and calendar, sign in to each. Then just send me a message."
NO_ACCOUNTS = "Mail and calendar are not set up on this copy, so there is nothing to sign in to yet."
HOST_HELP = ("Hosting: /invite makes a link for one new person (add a note to remember who it was for). /people lists who is here. "
             "/remove and an id stops that person's assistant.")


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def clean_name(name: str) -> str:
    """A stranger chose this name. It is shown to the host in lines code writes, and never to a model."""
    return " ".join(re.sub(r"[^\w .'\-]", " ", str(name or "")).split())[:NAME_MAX]


class People:
    """The list of members and unused invites, in one file in the host's folder."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self._path, self._clock = Path(path), clock
        self.members: dict[str, dict[str, Any]] = {}
        self.invites: dict[str, dict[str, Any]] = {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self.members = {str(k): v for k, v in (raw.get("members") or {}).items() if str(k).isdigit() and isinstance(v, dict)}
            self.invites = {str(k): v for k, v in (raw.get("invites") or {}).items() if isinstance(v, dict)}
        except (OSError, ValueError, AttributeError):
            pass

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"members": self.members, "invites": self.invites}, indent=1, ensure_ascii=False), encoding="utf-8")

    def _prune(self) -> None:
        cutoff = self._clock() - INVITE_DAYS * 86400
        self.invites = {h: v for h, v in self.invites.items() if v.get("made", 0) > cutoff}

    def ids(self) -> list[int]:
        return [int(k) for k in self.members]

    def has(self, user_id: int) -> bool:
        return str(user_id) in self.members

    def invite(self, note: str = "") -> str | None:
        """A new code, or None when too many are already waiting. Only its hash is kept."""
        self._prune()
        if len(self.invites) >= OPEN_INVITES:
            return None
        code = secrets.token_urlsafe(16)
        self.invites[_hash(code)] = {"made": round(self._clock()), "note": clean_name(note)}
        self._save()
        return code

    def redeem(self, code: str, user_id: int, name: str = "") -> bool:
        """Use a code once. True if it was a live one; the person is then a member."""
        self._prune()
        found = self.invites.pop(_hash(str(code or "")), None)
        if found is None:
            return False
        self.members[str(user_id)] = {"name": clean_name(name), "note": found.get("note", ""), "joined": round(self._clock())}
        self._save()
        return True

    def remove(self, user_id: int) -> dict[str, Any] | None:
        gone = self.members.pop(str(user_id), None)
        if gone is not None:
            self._save()
        return gone


class House:
    """Stands where the one conversation stood. Each message goes to the assistant of the person who sent it.

    `assemble(user_id)` builds and starts an assistant for a new member and returns it; `link(code)` turns a code into
    the address to send. An assistant is anything with `.conversation`, `.ledger`, `.apps` and `close()`.
    """

    def __init__(self, host_id: int, people: People, journal: Journal, assemble: Callable[[int], Awaitable[Any]],
                 link: Callable[[str], Awaitable[str]], most: int = 10, clock: Callable[[], float] = time.time):
        self._host, self._people, self._journal, self._assemble, self._link = host_id, people, journal, assemble, link
        self._most, self._clock = most, clock
        self._at: dict[int, Any] = {}             # the assistant of each person who may speak
        self._tries: dict[int, list[float]] = {}
        self._joining: set[int] = set()

    def add(self, user_id: int, assistant: Any) -> None:
        self._at[user_id] = assistant

    def is_owner(self, user_id: int) -> bool:
        """Someone whose files may be fetched and whose late messages are reported: anyone with an assistant here."""
        return user_id in self._at

    # --- the host's own commands --------------------------------------------------------------------------------
    async def _hosting(self, text: str, chat: Any) -> bool:
        words = text.split()
        command = words[0].lower() if words else ""
        if command == "/invite":
            if len(self._at) >= self._most:
                await chat.send(f"This copy is set to serve at most {self._most} people, and it has that many. Remove someone first, or raise ODYSSEUS_PEOPLE_MAX.")
                return True
            code = self._people.invite(" ".join(words[1:]))
            if code is None:
                await chat.send(f"There are already {OPEN_INVITES} unused invitations. They lapse after {INVITE_DAYS} days.")
                return True
            self._journal.write("invite", outcome="made")
            await chat.send("Send this link to one person. It works once, for the first person who opens it, and for "
                            f"{INVITE_DAYS} days. They open it and tap Start.\n\n{await self._link(code)}")
            return True
        if command == "/people":
            lines = []
            for uid, a in self._at.items():
                who = "you" if uid == self._host else (self._people.members.get(str(uid), {}).get("name") or "no name given")
                note = self._people.members.get(str(uid), {}).get("note", "")
                lines.append(f"- {who}{f' ({note})' if note else ''}, id {uid}: ${a.ledger.spent_today():.2f} today")
            waiting = len(self._people.invites)
            await chat.send("Here now:\n" + "\n".join(lines) + (f"\n\n{waiting} invitation{'s' if waiting != 1 else ''} not yet used." if waiting else "")
                            + "\n\nTo invite someone, send /invite. To stop someone's assistant, send /remove and their id.")
            return True
        if command == "/remove":
            uid = int(words[1]) if len(words) > 1 and words[1].isdigit() else 0
            if uid == self._host or not self._people.has(uid):
                await chat.send("Send /remove followed by an id from /people. You cannot remove yourself.")
                return True
            self._people.remove(uid)
            gone = self._at.pop(uid, None)
            if gone is not None:
                try:
                    await gone.close()
                except Exception as e:
                    self._journal.write("error", where="people.remove", error=type(e).__name__)
            self._journal.write("member", outcome="removed", user_id=uid)
            await chat.send(f"Removed. Nothing they send will be answered from now on. Their folder (people/{uid} in the data folder) "
                            "and their sign-ins with the connector are still there; delete those yourself if you want them gone.")
            return True
        return False

    # --- someone who is not on the list -------------------------------------------------------------------------
    def _may_try(self, user_id: int) -> bool:
        cutoff = self._clock() - 3600
        self._tries = {u: [t for t in ts if t > cutoff] for u, ts in self._tries.items()}
        self._tries = {u: ts for u, ts in self._tries.items() if ts}
        return len(self._tries.get(user_id, [])) < TRIES and sum(len(ts) for ts in self._tries.values()) < ALL_TRIES

    async def _stranger(self, user_id: int, text: str, chat: Any) -> None:
        words = (text or "").split()
        if len(words) != 2 or words[0].lower() != "/start" or user_id in self._joining or user_id <= 0:
            self._journal.write("dropped", user_id=user_id)
            return
        if not self._may_try(user_id) or len(self._at) >= self._most or not self._people.redeem(words[1], user_id, getattr(chat, "who", "")):
            self._tries.setdefault(user_id, []).append(self._clock())
            self._journal.write("invite", outcome="refused", user_id=user_id)
            return                                  # no reply: a wrong code learns nothing, not even that this is such a bot
        self._joining.add(user_id)
        self._journal.write("member", outcome="joined", user_id=user_id)
        try:
            assistant = await self._assemble(user_id)
        except Exception as e:
            self._journal.write("error", where="people.assemble", error=type(e).__name__, message=str(e)[:200])
            await chat.send("You're on the list, but I could not finish setting up. The person who invited you has been told.")
            await self._tell_host(f"Someone used an invitation (id {user_id}) and their assistant could not be set up. The details are in the log. A restart will try again.")
            return
        finally:
            self._joining.discard(user_id)
        self._at[user_id] = assistant
        await chat.send(WELCOME)
        await self._offer_accounts(assistant, chat)
        name = self._people.members.get(str(user_id), {}).get("name") or "Someone"
        await self._tell_host(f"{name} joined with one of your invitations (id {user_id}). /people lists everyone.")

    async def _offer_accounts(self, assistant: Any, chat: Any) -> None:
        apps = getattr(assistant, "apps", None)
        if apps is None:
            await chat.send(NO_ACCOUNTS)
            return
        try:
            import asyncio
            links = await asyncio.to_thread(apps.connect_links)
        except Exception as e:
            self._journal.write("error", where="people.links", error=type(e).__name__)
            await chat.send("I could not make the sign-in links for mail and calendar just now. Send /apps to try again.")
            return
        if links and hasattr(chat, "links"):
            await chat.links(CONNECT, links)
        elif links:
            await chat.send(CONNECT + "\n\n" + "\n\n".join(f"{label}:\n{url}" for label, url in links))

    async def _tell_host(self, text: str) -> None:
        host = self._at.get(self._host)
        if host is None:
            return
        try:
            await host.notify.send(text)
        except Exception as e:
            self._journal.write("error", where="people.tell_host", error=type(e).__name__)

    # --- what the channel calls: the same names as one conversation has -----------------------------------------
    async def handle(self, user_id: int, text: str, chat: Any, **more: Any) -> None:
        a = self._at.get(user_id)
        if a is None:
            await self._stranger(user_id, text, chat)
            return
        stripped = (text or "").strip()
        if user_id == self._host and stripped.startswith("/") and await self._hosting(stripped, chat):
            return
        await a.conversation.handle(user_id, text, chat, **more)
        if user_id == self._host and stripped.lower() in ("/start", "/help"):
            await chat.send(HOST_HELP)

    async def handle_file(self, user_id: int, *args: Any, **more: Any) -> None:
        a = self._at.get(user_id)
        if a is None:
            self._journal.write("dropped", user_id=user_id)
            return
        await a.conversation.handle_file(user_id, *args, **more)

    async def handle_voice(self, user_id: int, *args: Any, **more: Any) -> None:
        a = self._at.get(user_id)
        if a is None:
            self._journal.write("dropped", user_id=user_id)
            return
        await a.conversation.handle_voice(user_id, *args, **more)

    async def handle_decision(self, user_id: int, *args: Any, **more: Any) -> str:
        a = self._at.get(user_id)
        return await a.conversation.handle_decision(user_id, *args, **more) if a is not None else ""

    async def handle_choice(self, user_id: int, *args: Any, **more: Any) -> str:
        a = self._at.get(user_id)
        return await a.conversation.handle_choice(user_id, *args, **more) if a is not None else ""

    async def close(self) -> None:
        for a in list(self._at.values()):
            await a.close()
