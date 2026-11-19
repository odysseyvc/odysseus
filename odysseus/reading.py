"""Reading one file, wherever it came from.

A file reaches the assistant in three ways: the owner sends it in the chat, the owner sends a link to it, or it is
attached to an email the owner asks about. What happens next is the same each time and lives here: work out what kind
of file it is, turn it into text, have the pages of a short PDF looked at for what the text leaves out, and lay the
result out between the markers the model knows as a file.

Whoever sent it, a file was written by someone else. The block this returns says so, and the caller records it as
material that other people wrote.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .journal import Journal, Ledger
from .ocr import MAX_BYTES, MAX_CHARS, OcrBackend, OcrError, file_block, resolve_type, shown_whole

FILE_TIMEOUT_S = 180     # how long the text-recognition service gets before the owner is told it failed


class Reader:
    def __init__(self, ocr: OcrBackend | None, pages: Any, journal: Journal, ledger: Ledger, daily_usd: float):
        self._ocr, self._pages, self._journal, self._ledger, self._daily_usd = ocr, pages, journal, ledger, daily_usd

    @property
    def ready(self) -> bool:
        return self._ocr is not None

    async def read(self, data: bytes | None, name: str, mime: str | None, size: int | None = None, source: str = "chat") -> str:
        """The file as a block of text for the model. Raises OcrError, in words for the owner, if it cannot be read."""
        if self._ocr is None:
            raise OcrError("I can't read files yet: no text-recognition key is in my settings.")
        kind = resolve_type(mime, name)
        if data is None or (size or 0) > MAX_BYTES or len(data) > MAX_BYTES:
            raise OcrError("That file is too big for me to fetch. The limit is 20 MB.")
        try:
            result = await asyncio.wait_for(asyncio.to_thread(self._ocr.read, data, kind), FILE_TIMEOUT_S)
        except OcrError:
            raise
        except (Exception, asyncio.TimeoutError) as e:
            self._journal.write("file", name=name, ok=False, source=source, error=f"{type(e).__name__}: {str(e)[:200]}")
            raise OcrError("I could not read that file. The details are in the log.") from None
        # The pages of a short PDF are looked at in a run of their own, and what they show comes back as text (pages.py).
        # The file itself is never put into the conversation, where it would be carried forward with every later message.
        sights = None
        if self._pages is not None and shown_whole(kind, len(data), result.pages) and self._ledger.spent_today() < self._daily_usd:
            try:
                sights, _ = await self._pages(data, result.text[:MAX_CHARS])
            except Exception as e:
                self._journal.write("error", where="reading.pages", error=type(e).__name__)
        self._journal.write("file", name=name, ok=True, type=kind, bytes=len(data), pages=result.pages, chars=len(result.text),
                            seen=sights is not None, sights=len(sights or ""), source=source)
        return file_block(name, result, sights)
