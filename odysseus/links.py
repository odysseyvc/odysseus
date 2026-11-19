"""Fetching a file from a link the owner sends.

Many documents arrive as a link and not as a file: a PDF on a company's site, a deck in Google Drive or Slides. When
the owner's own message holds such a link, code fetches the file and it is then read exactly as if it had been sent
(conversation.handle_file). No model decides whether to fetch, or what.

Which links: an address ending in .pdf, and Google Drive, Slides and Docs links, which have a public address that
returns the file as a PDF when the link is shared with "anyone with the link". Dropbox links likewise. A link to a
viewing site (DocSend and the like) has no such address; that is left to the browser, by the model.

What is guarded, because the program now connects to an address somebody typed:

  https only       and at most five redirects, each checked like the first address.
  public only      a name that resolves to this machine or to a private network is refused, at the moment of
                   connecting and not before it, so a name cannot answer one thing when checked and another when used.
                   A server that the program runs beside must not be reachable by sending the assistant a link.
  size and time    20 MB and 60 seconds, counted while reading.
  it is a PDF      judged by the file's first bytes, not by what the server says it is. A sign-in page is not a PDF.

Only a link in the owner's own typed message is fetched. A link inside mail, a file or a web page is never fetched
this way: the model has the browser and web search for those, behind their own rules.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver
from aiohttp.resolver import ThreadedResolver

from .ocr import MAX_BYTES

TIMEOUT_S = 60
MAX_REDIRECTS = 5
_URL = re.compile(r"https?://[^\s<>\"'\]]+")
_ID = r"([A-Za-z0-9_-]{20,})"
# A viewing site shows a document page by page in the browser and gives no file. Named so the owner can be told why.
VIEWERS = ("docsend.com", "pitch.com", "canva.com", "papermark.io", "papermark.com", "brieflink.com", "notion.site", "gamma.app", "beautiful.ai")


class LinkError(Exception):
    """Why a file could not be fetched, in words the owner and the model can both read."""


def _public(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip.split("%")[0]).is_global
    except ValueError:
        return False


class PublicOnly(AbstractResolver):
    """Resolves a name and keeps only public addresses. Used for the connection itself, so the check cannot be raced."""

    def __init__(self, allow: Callable[[str], bool] = _public):
        self._inner, self._allow, self.refused = ThreadedResolver(), allow, False

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET) -> list[dict[str, Any]]:
        found = [a for a in await self._inner.resolve(host, port, family) if self._allow(str(a["host"]))]
        if not found:
            self.refused = True         # the library wraps this error and loses its words, so the refusal is kept here
            raise OSError(f"{host} is not a public address")
        return found

    async def close(self) -> None:
        await self._inner.close()


def target(url: str) -> str | None:
    """The address that returns this link's file as a PDF, or None if the link is not one code fetches."""
    url = url.rstrip(".,;:!?)")
    u = urlsplit(url)
    host, path = (u.hostname or "").lower(), u.path
    if u.scheme != "https" or not host:
        return None
    if host == "drive.google.com":
        m = re.search(r"/file/d/" + _ID, path)
        fid = m.group(1) if m else (parse_qs(u.query).get("id") or [""])[0]
        return f"https://drive.google.com/uc?export=download&id={fid}" if re.fullmatch(_ID, fid or "") else None
    if host == "docs.google.com":
        m = re.search(r"/(presentation|document)/d/" + _ID, path)
        if not m:
            return None
        return f"https://docs.google.com/{m.group(1)}/d/{m.group(2)}/export" + ("/pdf" if m.group(1) == "presentation" else "?format=pdf")
    if host in ("www.dropbox.com", "dropbox.com") and path.lower().endswith(".pdf"):
        return f"https://www.dropbox.com{path}?" + "&".join([p for p in u.query.split("&") if p and not p.startswith("dl=")] + ["dl=1"])
    return url if path.lower().endswith(".pdf") else None


def find(text: str) -> tuple[str, str] | None:
    """The first link in the text that code fetches: (the link as written, the address to fetch)."""
    for m in _URL.finditer(text or ""):
        where = target(m.group(0))
        if where:
            return m.group(0).rstrip(".,;:!?)"), where
    return None


def viewer(text: str) -> str | None:
    """The viewing site a link in the text points at, if any."""
    for m in _URL.finditer(text or ""):
        host = (urlsplit(m.group(0)).hostname or "").lower()
        hit = next((v for v in VIEWERS if host == v or host.endswith("." + v)), None)
        if hit:
            return hit
    return None


def _name(url: str, disposition: str) -> str:
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", disposition or "", re.I)
    name = unquote(m.group(1)).strip() if m else unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    name = re.sub(r"[\\/\x00-\x1f]", "_", name)[:120] or "file"
    return name if name.lower().endswith(".pdf") else name + ".pdf"


class Fetcher:
    def __init__(self, allow: Callable[[str], bool] = _public, schemes: tuple[str, ...] = ("https",), max_bytes: int = MAX_BYTES,
                 timeout_s: float = TIMEOUT_S):
        self._allow, self._schemes, self._max, self._timeout = allow, schemes, max_bytes, timeout_s

    def _check(self, url: str) -> None:
        u = urlsplit(url)
        if u.scheme not in self._schemes or not u.hostname:
            raise LinkError("the link does not lead to a secure web address")
        try:
            literal = ipaddress.ip_address(u.hostname)
        except ValueError:
            return                      # a name: checked when it is resolved, by PublicOnly
        if not self._allow(str(literal)):
            raise LinkError("the link leads to a private address, which I do not open")

    async def fetch(self, url: str) -> tuple[bytes, str]:
        """The PDF at this address and a name for it. Raises LinkError with the reason otherwise."""
        try:
            return await asyncio.wait_for(self._fetch(url), self._timeout)
        except asyncio.TimeoutError:
            raise LinkError("the site did not answer in time") from None

    async def _fetch(self, url: str) -> tuple[bytes, str]:
        resolver = PublicOnly(self._allow)
        connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
        try:
            async with aiohttp.ClientSession(connector=connector, headers={"User-Agent": "Mozilla/5.0 (compatible; document-fetch)"}) as session:
                for _ in range(MAX_REDIRECTS + 1):
                    self._check(url)
                    async with session.get(url, allow_redirects=False) as r:
                        if r.status in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                            url = urljoin(url, r.headers["Location"])
                            continue
                        if r.status in (401, 403):
                            raise LinkError("the site says I am not allowed to see it; it probably needs a sign-in or is not shared by link")
                        if r.status != 200:
                            raise LinkError(f"the site answered {r.status}")
                        data = bytearray()
                        async for chunk in r.content.iter_chunked(64 * 1024):
                            data += chunk
                            if len(data) > self._max:
                                raise LinkError(f"the file is larger than {self._max // (1024 * 1024)} MB")
                            if len(data) >= 1024 and b"%PDF" not in data[:1024]:
                                break
                        if b"%PDF" not in bytes(data[:1024]):
                            raise LinkError("what is at that link is not a PDF but a web page; it may need a sign-in, or not be shared with anyone who has the link")
                        return bytes(data), _name(url, r.headers.get("Content-Disposition", ""))
                raise LinkError("the link redirects too many times")
        except LinkError:
            raise
        except (aiohttp.ClientError, OSError) as e:
            if resolver.refused:
                raise LinkError("the link leads to a private address, which I do not open") from None
            raise LinkError(f"the site could not be reached ({type(e).__name__})") from None
