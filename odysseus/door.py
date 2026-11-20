"""The one port this program listens on, when it is on a server with a public address.

Telegram needs no door: the program fetches its messages. Two things do need one:

  /callback     where a person's browser lands after they approve a sign-in at a service with a server of its own
                (connect.py). Without a public address the browser lands on a page that does not load and the person
                copies its address into the chat; with one, they see "Connected" and nothing more is asked of them.
  /whatsapp     Meta's deliveries, if WhatsApp is switched on (whatsapp.py).

What /callback will accept. Every sign-in begins with a long random mark (the standard's `state`) that the service
returns in the address. The connector tells the door which mark it is waiting for; an address carrying any other mark
gets a refusal and is otherwise ignored. A mark is good once. The page shown says the same thing whatever was sent,
and nothing from the address is put into it. The one-time code is handed to the waiting sign-in and is not logged.

What it cannot stop: a person who starts a sign-in and gives the link to someone else. If that someone approves, their
account is connected to the first person's assistant. Every program that signs in through a browser has this limit;
the service's own approval page, which names the program and the permissions, is the defence.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

DONE = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Connected</title>"
        "<body style='font:17px system-ui;margin:15vh auto;max-width:26em;padding:0 1em'><p>Connected.</p>"
        "<p>You can close this page and go back to the chat.</p>")
NOT_WAITING = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Not connected</title>"
               "<body style='font:17px system-ui;margin:15vh auto;max-width:26em;padding:0 1em'><p>Nothing was connected.</p>"
               "<p>This sign-in is not waiting any more, or was not approved. Go back to the chat and send /connect again.</p>")
MOST_WAITING = 50      # sign-ins in progress at once; each lasts ten minutes at most


class Door:
    def __init__(self) -> None:
        self._waiting: dict[str, asyncio.Future] = {}
        self._routes: dict[tuple[str, str], Callable[[Any], Awaitable[Any]]] = {}
        self._runner: Any = None

    @property
    def listening(self) -> bool:
        return self._runner is not None

    # --- sign-ins -----------------------------------------------------------------------------------------
    def expect(self, state: str) -> asyncio.Future:
        """The (code, state, issuer) a browser will bring back for the sign-in that began with this mark."""
        if len(self._waiting) >= MOST_WAITING:
            raise RuntimeError("too many sign-ins are waiting")
        waiting = self._waiting[state] = asyncio.get_running_loop().create_future()
        return waiting

    def forget(self, state: str | None) -> None:
        self._waiting.pop(state or "", None)

    def came_back(self, query: dict[str, str]) -> bool:
        """A browser arrived at /callback with these values. Whether a sign-in was waiting for it."""
        waiting = self._waiting.pop(query.get("state") or "", None)
        if waiting is None or waiting.done():
            return False
        if not query.get("code"):
            waiting.set_exception(RuntimeError("the service sent no code: the sign-in was not approved"))
            return False
        waiting.set_result((query["code"], query.get("state"), query.get("iss")))
        return True

    # --- other things behind the same port ------------------------------------------------------------------
    def route(self, method: str, path: str, handler: Callable[[Any], Awaitable[Any]]) -> Callable[[], None]:
        """Answer `method path` with this handler from now on. Returns what takes it away again."""
        self._routes[(method.upper(), path)] = handler
        return lambda: self._routes.pop((method.upper(), path), None)

    async def _handle(self, request: Any) -> Any:
        from aiohttp import web
        if request.method == "GET" and request.path == "/callback":
            ok = self.came_back({k: v for k, v in request.query.items() if k in ("code", "state", "iss")})
            return web.Response(status=200 if ok else 400, text=DONE if ok else NOT_WAITING, content_type="text/html",
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})
        handler = self._routes.get((request.method, request.path))
        return await handler(request) if handler is not None else web.Response(status=404)

    async def open(self, port: int) -> None:
        from aiohttp import web
        app = web.Application(client_max_size=1024 * 1024)
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app, access_log=None)      # no access log: the addresses carry one-time codes
        await self._runner.setup()
        await web.TCPSite(self._runner, "0.0.0.0", port).start()

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
