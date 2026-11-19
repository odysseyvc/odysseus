"""Connecting a service from the chat: /connect.

Until now a service with a server of its own (Attio, Granola, X) was connected by running a script at the machine. The
owner asked to do it from the chat, for any such server:

    /connect                       what is connected, and what is known and is not
    /connect granola               a service this program already knows
    /connect https://mcp.site/mcp  any server that speaks the protocol, by its address
    /connect exa   or   exa.ai     a company by name or website: its server is looked up in the public registry
    /disconnect granola            forget a sign-in

What happens: the program asks the server how to sign in, sends the owner the sign-in link in the chat, and waits. The
owner opens it and approves on the service's own page; no password or key passes through the chat. The program then
lists the server's tools, saves the connection, and restarts itself so that the assistant has the new tools.

What is decided here and by whom.

  Only the owner, by a typed command. The assistant has no tool that connects anything. A new server brings new tools
  and, with them, descriptions the server wrote, which the model will read: adding one is the owner's decision, and a
  page or an email that says "connect this server" reaches nobody who can do it.

  What its tools may do is the standing rule for a vendor's own server (the book's chapter on services with their own server): a tool runs unasked only if the
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


REGISTRY = "https://registry.modelcontextprotocol.io/v0/servers"


def _published_by(name: str) -> str:
    """The domain a registry entry was published under. Names are the domain reversed: ai.exa/exa -> exa.ai."""
    return ".".join(reversed(name.split("/", 1)[0].lower().split(".")))


async def registry_lookup(label: str, site: str | None) -> list[str]:
    """Addresses of servers in the public registry that belong to a company, by its name ("exa") or its site ("exa.ai").

    Anyone can publish to the registry, but only under a domain they have proved they own. So an entry counts only if
    it is published under a domain whose own name is the one asked for (or is the site given), is active, and its
    server's address is on that same domain. A server that merely has the word in its name is not the company's.
    """
    import aiohttp
    out: list[str] = []
    cursor = ""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as http:
            for _ in range(4):
                async with http.get(REGISTRY, params={"search": label, "limit": "100", **({"cursor": cursor} if cursor else {})}) as r:
                    if r.status != 200:
                        break
                    data = await r.json()
                for row in data.get("servers") or []:
                    entry = row.get("server") or row
                    status = ((row.get("_meta") or {}).get("io.modelcontextprotocol.registry/official") or {}).get("status", "active")
                    domain = _published_by(str(entry.get("name", "")))
                    if status != "active" or (site and domain != site) or (not site and domain.split(".")[0] != label):
                        continue
                    for remote in entry.get("remotes") or []:
                        url, host = str(remote.get("url", "")), (urlsplit(str(remote.get("url", ""))).hostname or "").lower()
                        if str(remote.get("type", "")) != "streamable-http":
                            continue            # the older kind of connection, which this program does not speak
                        if url.startswith("https://") and "{" not in url and (host == domain or host.endswith("." + domain)) and url not in out:
                            out.append(url)
                cursor = (data.get("metadata") or {}).get("nextCursor") or ""
                if not cursor:
                    break
    except Exception:
        pass
    # Several addresses that are one server and its variants (/mcp and /mcp/readonly): the plain one is meant.
    base = min(out, key=len) if out else ""
    return [base] if len(out) > 1 and all(u.startswith(base) for u in out) else out


async def is_server(url: str) -> bool:
    """Whether an address answers as a server of this kind: it accepts the opening message, or asks for a sign-in."""
    import aiohttp

    from .links import PublicOnly
    hello = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "Odysseus", "version": "1"}}}
    try:
        connector = aiohttp.TCPConnector(resolver=PublicOnly(), use_dns_cache=False)
        async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=15)) as http:
            async with http.post(url, json=hello, headers={"Accept": "application/json, text/event-stream"}, allow_redirects=False) as r:
                if r.status == 401:
                    return "bearer" in r.headers.get("WWW-Authenticate", "").lower()
                if r.status != 200:
                    return False
                kind = r.headers.get("Content-Type", "")
                return "text/event-stream" in kind or ("application/json" in kind and "jsonrpc" in (await r.text())[:2000])
    except Exception:
        return False


class Connector:
    def __init__(self, data_dir: Path, journal: Journal, restart: Callable[[], None], opener: Any = session,
                 port: Callable[[], int] = free_port, public: Callable[[str], Awaitable[bool]] = public_host,
                 timeout_s: float = SIGN_IN_TIMEOUT_S, also: tuple[str, ...] = (), lookup: Any = None, probe: Any = None):
        self._lookup, self._probe = lookup or registry_lookup, probe or is_server
        self._dir, self._journal, self._restart = Path(data_dir), journal, restart
        self._also = also       # connected by other means (Attio has its own script), named in the list so it is complete
        self._open, self._port, self._public, self._timeout = opener, port, public, timeout_s
        self._task: asyncio.Task | None = None

    def _tokens(self, slug: str) -> FileTokens:
        return FileTokens(self._dir / f"{slug}.json")

    def _on(self, slug: str) -> bool:
        return self._tokens(slug).signed_in() or bool(mcpapp.APPS.get(slug, {}).get("open"))

    def listing(self) -> str:
        on = list(self._also) + [f"{spec['title']} ({slug})" for slug, spec in mcpapp.APPS.items() if self._on(slug)]
        off = [f"{spec['title']} ({slug})" for slug, spec in mcpapp.APPS.items() if not self._on(slug)]
        return ("Connected: " + (", ".join(on) or "none") + "."
                + ("\nKnown and not connected: " + ", ".join(off) + "." if off else "")
                + "\n\nTo connect one, send /connect and its name (for example /connect exa), its website, or the address of its "
                  "server. To forget one, send /disconnect and its name.")

    async def _spec(self, arg: str) -> tuple[str, dict[str, Any], str] | str:
        """(name, how to reach it, what to tell the owner first) for what was typed, or a sentence saying why not.

        The owner may type a name this program knows, a server's address, a company's website, or just a company's name.
        A person asked to "connect Exa" would go and find Exa's server; so does this, in the public registry of servers,
        and it accepts only an entry published under the company's own domain whose server is on that same domain.
        """
        words = arg.split()
        first = words[0].strip(".,<>") if words else ""
        if first.lower() in mcpapp.APPS:
            return first.lower(), mcpapp.APPS[first.lower()], ""
        if first.lower().startswith("http://"):
            return "I only connect to https addresses."
        note = ""
        if "." not in first:                                    # a bare name: "exa", "linear"
            name = re.sub(r"[^a-z0-9-]", "", first.lower())
            found = await self._lookup(name, None) if name else []
            if len(found) != 1:
                return self._not_found(first, found)
            first, note = found[0], f"I found {name.capitalize()}'s server in the public registry of servers, published under its own domain: {found[0]}"
        elif not first.lower().startswith("https://"):
            first = "https://" + first                          # "exa.ai", "mcp.exa.ai/mcp"
        u = urlsplit(first)
        if u.scheme != "https" or not u.hostname or "." not in u.hostname:
            return "Send /connect with a service's name, its website, or its server's address, for example /connect https://mcp.example.com/mcp."
        for slug, spec in mcpapp.APPS.items():          # a known service given by its address keeps what is known of it
            known = urlsplit(spec["url"])
            if known.hostname == u.hostname and (known.path.rstrip("/") == u.path.rstrip("/") or u.path in ("", "/")):
                return slug, spec, ""
        if not await self._public(first):
            return "That address does not lead to a public server, so I have not opened it."
        if not note and not await self._probe(first):
            # Not a server itself: most likely the company's website. Look for the server that belongs to that site.
            site = u.hostname.removeprefix("www.")
            found = await self._lookup(site.split(".")[0], site)
            if not found:
                for guess in (f"https://mcp.{site}/mcp", f"https://{site}/mcp", f"https://api.{site}/mcp"):
                    if await self._public(guess) and await self._probe(guess):
                        found = [guess]
                        break
            if len(found) != 1:
                return self._not_found(site, found)
            first, note = found[0], f"{u.hostname} is a website, not a server. The server that belongs to it is {found[0]}"
        slug = re.sub(r"[^a-z0-9]", "", words[1].lower())[:20] if len(words) > 1 else slug_for(first)
        if not slug or slug in RESERVED or slug in mcpapp.APPS:
            return (f"I need a short name for it that is not already taken. Send it after the address, for example "
                    f"/connect {first} mytool.")
        return slug, mcpapp.custom_spec(slug, first), note

    @staticmethod
    def _not_found(what: str, found: list[str]) -> str:
        if found:
            return (f"I found more than one server that could be {what}'s, and will not pick for you:\n" + "\n".join(found[:6])
                    + "\nSend /connect with the address of the one you mean.")
        return (f"I could not find a server for {what}: nothing is published under its own domain in the public registry of servers, and "
                "the usual addresses did not answer. If it has one, send /connect with the server's address, which its documentation "
                "will give, usually ending in /mcp.")

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
        slug, spec, note = found
        if note:
            await send(note + ". Connecting to it now.")
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
            asked: list[bool] = []

            async def shown(link: str) -> None:
                asked.append(True)
                await show(link)

            async def go() -> list[Any]:
                async with self._open(tokens, shown, spec["url"], spec.get("scope"), self._port()) as s:
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
        if not tools and not tokens.signed_in():
            self._journal.write("connect", service=slug, ok=False, error="no tools")
            await send(f"{title}'s server answered but offers no tools, so there is nothing to connect.")
            return
        rows = [{"name": t.name, "labels": (t.annotations.model_dump(exclude_none=True) if t.annotations else {})} for t in tools]
        hidden = tuple(spec.get("hide") or ())
        shown = [r for r in rows if r["name"] not in hidden]
        reads = sum(1 for r in shown if spec.get("read_only_connection") or mcpapp.kind(r["name"], r["labels"]) == "read")
        open_ = not tokens.signed_in()       # the server answered without any sign-in: a public one
        if open_:
            spec = {**spec, "open": True}
            mcpapp.APPS[slug] = spec
        if spec.get("custom"):
            mcpapp.save_custom(self._dir, slug, spec)
        self._journal.write("connect", service=slug, ok=True, tools=len(shown), reads=reads, custom=bool(spec.get("custom")))
        await send(f"{title} is connected" + (" (it asked for no sign-in: it is a public server)" if open_ else "") + f": {len(shown)} tools. {reads} only read and will run when I need them; "
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
        path, title, was = self._dir / f"{slug}.json", mcpapp.APPS[slug]["title"], self._on(slug)
        public = bool(mcpapp.APPS[slug].get("open"))
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
        await send((f"{title} is forgotten here. It was a public server with no sign-in, so there is nothing to withdraw. " if public else
                    f"{title} is forgotten here: I have deleted its sign-in. To withdraw the permission at {title} itself, remove this "
                    "program in that service's own settings. ") + "I am restarting, which takes a few seconds.")
        self._restart()
