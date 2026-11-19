"""Connecting a service from the chat: /connect.

Until now a service with a server of its own (Attio, Granola, X) was connected by running a script at the machine. The
owner asked to do it from the chat, for any such server:

    /connect                       what is connected, and what is known and is not
    /connect granola               a service this program already knows
    /connect https://mcp.site/mcp  any server that speaks the protocol, by its address
    /disconnect granola            forget a sign-in

What happens: the program asks the server how to sign in, sends the owner the sign-in link in the chat, and waits. The
owner opens it and approves on the service's own page; no password or key passes through the chat. The program then
lists the server's tools, saves the connection, and restarts itself so that the assistant has the new tools.

What is decided here and by whom.

  Only the owner, by a typed command. The assistant has no tool that connects anything. A new server brings new tools
  and, with them, descriptions the server wrote, which the model will read: adding one is the owner's decision, and a
  page or an email that says "connect this server" reaches nobody who can do it.

  What its tools may do is the standing rule for a vendor's own server (Chapter 21): a tool runs unasked only if the
  server labels it read-only and its name begins with a reading word. Everything else is put to the owner each time,
  with its inputs. For a server this program has never seen, that rule is all there is: nobody has read its tools.

  The address must be https and resolve to a public address. The sign-in itself is the service's; this program cannot
  see what the owner is shown there, only the permissions the link asks for, which it repeats in the chat.

One limit, stated plainly: the service sends the browser back to the machine this program runs on. So the link has to
be opened in a browser on that machine. From a phone, with the program on a laptop or a server, it will not complete.
"""
from __future__ import annotations

import asyncio
import json
import re
import socket
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

from . import mcpapp
from .attio import FileTokens, session
from .journal import Journal
from .links import _public

SIGN_IN_TIMEOUT_S = 600
RESERVED = {"odysseus", "attio", "mail", "calendar", "app", "web", "browse", "memory", "schedule", "watch"}
_STRIP = ("mcp", "api", "www", "app", "server", "connect")


def slug_for(url: str) -> str:
    """A short name for a server from its address: mcp.linear.app -> linear, docsend.com -> docsend."""
    host = (urlsplit(url).hostname or "").lower()
    labels = [x for x in host.split(".") if x]
    if len(labels) >= 2:
        labels = labels[:-1]                # the last label is the ending (.com, .app, .ai)
    names = [x for x in labels if x not in _STRIP] or labels
    return re.sub(r"[^a-z0-9]", "", names[-1] if names else "")[:20]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def public_host(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    try:
        found = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    return bool(found) and all(_public(str(a[4][0])) for a in found)


class Connector:
    def __init__(self, data_dir: Path, journal: Journal, restart: Callable[[], None], opener: Any = session,
                 port: Callable[[], int] = free_port, public: Callable[[str], Awaitable[bool]] = public_host,
                 timeout_s: float = SIGN_IN_TIMEOUT_S):
        self._dir, self._journal, self._restart = Path(data_dir), journal, restart
        self._open, self._port, self._public, self._timeout = opener, port, public, timeout_s
        self._task: asyncio.Task | None = None

    def _tokens(self, slug: str) -> FileTokens:
        return FileTokens(self._dir / f"{slug}.json")

    def listing(self) -> str:
        on = [f"{spec['title']} ({slug})" for slug, spec in mcpapp.APPS.items() if self._tokens(slug).signed_in()]
        off = [f"{spec['title']} ({slug})" for slug, spec in mcpapp.APPS.items() if not self._tokens(slug).signed_in()]
        return ("Connected: " + (", ".join(on) or "none") + "."
                + ("\nKnown and not connected: " + ", ".join(off) + "." if off else "")
                + "\n\nTo connect one, send /connect and its name, or /connect and the address of any server that speaks the "
                  "Model Context Protocol, for example /connect https://mcp.example.com/mcp. To forget one, send /disconnect and its name.")

    async def _spec(self, arg: str) -> tuple[str, dict[str, Any]] | str:
        """(name, how to reach it) for what the owner typed, or a sentence saying why not."""
        words = arg.split()
        first = words[0].rstrip(".,") if words else ""
        if first.lower() in mcpapp.APPS:
            return first.lower(), mcpapp.APPS[first.lower()]
        u = urlsplit(first)
        if u.scheme != "https" or not u.hostname:
            return "Send /connect with a name I know or an https address, for example /connect https://mcp.example.com/mcp."
        for slug, spec in mcpapp.APPS.items():          # a known service given by its address keeps what is known of it
            if urlsplit(spec["url"]).hostname == u.hostname and urlsplit(spec["url"]).path.rstrip("/") == u.path.rstrip("/"):
                return slug, spec
        if not await self._public(first):
            return "That address does not lead to a public server, so I have not opened it."
        slug = re.sub(r"[^a-z0-9]", "", words[1].lower())[:20] if len(words) > 1 else slug_for(first)
        if not slug or slug in RESERVED or slug in mcpapp.APPS:
            return (f"I need a short name for it that is not already taken. Send it after the address, for example "
                    f"/connect {first} mytool.")
        return slug, mcpapp.custom_spec(slug, first)

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    async def connect(self, arg: str, send: Callable[[str], Awaitable[None]]) -> None:
        """Start a sign-in. Returns at once; the rest happens when the owner approves, or the wait runs out."""
        if not arg.strip():
            await send(self.listing())
            return
        if self.busy:
            await send("I am already waiting for one sign-in to be approved. Finish that one, or wait ten minutes for it to lapse.")
            return
        found = await self._spec(arg.strip())
        if isinstance(found, str):
            await send(found)
            return
        slug, spec = found
        if spec.get("client_id_env") and await self._tokens(slug).get_client_info() is None:
            await send(f"{spec['title']} does not let a program register itself: it needs an app of your own on its developer site first. "
                       f"That one step is done at the machine, with scripts/connect_mcp.py {slug}, which says what to create.")
            return
        self._task = asyncio.create_task(self._sign_in(slug, spec, send))

    async def _sign_in(self, slug: str, spec: dict[str, Any], send: Callable[[str], Awaitable[None]]) -> None:
        tokens, title = self._tokens(slug), spec["title"]

        async def show(link: str) -> None:
            asked = (parse_qs(urlsplit(link).query).get("scope") or [""])[0]
            await send(f"To connect {title}, open this link in a browser on the computer I am running on, sign in there and approve. "
                       + (f"It asks for these permissions: {asked}. " if asked else "The service chooses which permissions to ask for; read its page. ")
                       + f"The link is good for ten minutes.\n\n{link}")
        try:
            async def go() -> list[Any]:
                async with self._open(tokens, show, spec["url"], spec.get("scope"), self._port()) as s:
                    return list((await s.list_tools()).tools)
            tools = await asyncio.wait_for(go(), self._timeout)
        except asyncio.TimeoutError:
            self._journal.write("connect", service=slug, ok=False, error="not approved in time")
            await send(f"Nobody approved the {title} sign-in in ten minutes, so nothing was connected. Send /connect again to try once more.")
            return
        except BaseException as e:
            if isinstance(e, (KeyboardInterrupt, asyncio.CancelledError)):
                raise
            self._journal.write("connect", service=slug, ok=False, error=type(e).__name__)
            await send(f"{title} could not be connected: its server did not complete the sign-in ({type(e).__name__}). Nothing was saved."
                       + ("" if tokens.signed_in() else " If the address is right, the server may not offer the standard sign-in."))
            return
        rows = [{"name": t.name, "labels": (t.annotations.model_dump(exclude_none=True) if t.annotations else {})} for t in tools]
        hidden = tuple(spec.get("hide") or ())
        shown = [r for r in rows if r["name"] not in hidden]
        reads = sum(1 for r in shown if spec.get("read_only_connection") or mcpapp.kind(r["name"], r["labels"]) == "read")
        if spec.get("custom"):
            mcpapp.save_custom(self._dir, slug, spec)
        self._journal.write("connect", service=slug, ok=True, tools=len(shown), reads=reads, custom=bool(spec.get("custom")))
        await send(f"{title} is connected: {len(shown)} tools. {reads} only read and will run when I need them; "
                   f"{len(shown) - reads} add or change something and will be put to you first, each time."
                   + (" Nobody has read this server's tools before: they are sorted by the server's own labels and their names."
                      if spec.get("custom") else "")
                   + " I am restarting to pick it up, which takes a few seconds.")
        self._restart()

    async def disconnect(self, arg: str, send: Callable[[str], Awaitable[None]]) -> None:
        slug = arg.strip().lower()
        if slug not in mcpapp.APPS:
            await send("Send /disconnect and the name of a connected service. /connect lists them.")
            return
        path, title, was = self._dir / f"{slug}.json", mcpapp.APPS[slug]["title"], self._tokens(slug).signed_in()
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            self._journal.write("error", where="connect.forget", error=type(e).__name__)
            await send(f"I could not remove the {title} sign-in from my files.")
            return
        mcpapp.remove_custom(self._dir, slug)
        self._journal.write("connect", service=slug, ok=True, removed=True)
        if not was:
            await send(f"{title} was not connected.")
            return
        await send(f"{title} is forgotten here: I have deleted its sign-in. To withdraw the permission at {title} itself, remove this "
                   "program in that service's own settings. I am restarting, which takes a few seconds.")
        self._restart()
