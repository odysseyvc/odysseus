"""A link the owner is sent goes out under this program's own address, not under a supplier's.

To connect an app the owner must open a sign-in link, and the link is made by the service this program reaches apps
through. Sent as it comes, it shows the owner (and anyone they forward the message to) the name and address of a supplier
they never chose and need not know. So where the program has a public address (door.py), the link is kept here under a
short random name, and what is sent is this program's address with that name. Opening it sends the browser on to the real
link, once it is asked for within the hour.

What this hides and what it does not. The message in the chat names no supplier. The browser, once it follows the link,
shows whatever pages the sign-in passes through; that cannot be hidden from here and should not be, since the owner is
entitled to see what they are approving. With no public address there is nothing to send the link under, and it goes out
as it came.
"""
from __future__ import annotations

import secrets
import time
from typing import Any, Callable

KEEP_S = 3600
MOST = 200


class Links:
    def __init__(self, clock: Callable[[], float] = time.time):
        self._base = ""
        self._held: dict[str, tuple[str, float]] = {}
        self._clock = clock

    def open(self, door: Any, public_url: str) -> None:
        """Answer /go at the program's one door. Without a public address this does nothing and links go out as they came."""
        if not public_url or door is None:
            return
        self._base = public_url.rstrip("/")
        door.route("GET", "/go", self._go)

    def wrap(self, link: str) -> str:
        if not self._base or not str(link).startswith("https://"):
            return link
        now = self._clock()
        self._held = {k: v for k, v in list(self._held.items())[-MOST:] if now - v[1] < KEEP_S}
        name = secrets.token_urlsafe(9)
        self._held[name] = (link, now)
        return f"{self._base}/go?k={name}"

    def find(self, name: str) -> str:
        link, at = self._held.get(str(name), ("", 0.0))
        return link if link and self._clock() - at < KEEP_S else ""

    async def _go(self, request: Any) -> Any:
        from aiohttp import web
        link = self.find(request.query.get("k", ""))
        headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
        if not link:
            return web.Response(status=404, text="This link is no longer good. Ask for a new one in the chat.", headers=headers)
        return web.Response(status=302, headers={**headers, "Location": link})


LINKS = Links()         # one for the program: opened in __main__, used wherever a supplier's link is about to be sent
