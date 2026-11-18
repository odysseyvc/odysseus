"""Live check that the model sees a picture the owner sends.

A picture is made here, in code: the top half red, the bottom half blue, with no writing
on it. Text recognition would return nothing for it. The real model is asked which colours
it shows and which is on top. One model call, about a cent. No Telegram, no accounts.

    .venv/Scripts/python scripts/live_picture.py
"""
from __future__ import annotations

import asyncio
import json
import struct
import sys
import time
import warnings
import zlib
from dataclasses import replace
from pathlib import Path

warnings.simplefilter("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exa_py import Exa  # noqa: E402

from evals.world import FakeSearch  # noqa: E402
from odysseus.agent import AgentRunner, conversation_prompt  # noqa: E402
from odysseus.config import load  # noqa: E402
from odysseus.conversation import Conversation  # noqa: E402
from odysseus.journal import Journal, Ledger  # noqa: E402
from odysseus.memory import MemoryStore  # noqa: E402
from odysseus.tools import make_gate, make_tool_server  # noqa: E402


def png(width: int = 240, height: int = 240) -> bytes:
    """A PNG written by hand: red above, blue below."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + (b"\xe0\x20\x20" if y < height // 2 else b"\x20\x40\xe0") * width for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class Chat:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)

    async def draft(self, draft_id, text):
        pass

    async def ask(self, text, approval_id):
        pass


async def main() -> int:
    settings = replace(load(), data_dir=Path(f"var/live/picture-{int(time.time())}"))
    journal = Journal(settings.data_dir / "journal.jsonl")
    memory = MemoryStore(settings.data_dir / "memory")
    spec = Exa(api_key="unused").tools.web_search()
    gate = make_gate(journal)
    runner = AgentRunner(settings, journal, make_tool_server(FakeSearch(spec.description, spec.input_schema), journal, memory, gate=gate), gate=gate,
                         system_prompt=conversation_prompt(False, with_files=True))
    conv = Conversation(settings, journal, Ledger(journal), runner, memory, gate=gate)      # no text-recognition key is given
    chat = Chat()
    picture = png()
    await conv.handle_file(settings.owner_id, picture, "picture.png", "image/png", len(picture),
                           "Which two colours are in this picture, and which one is on top? Answer in one short sentence.", chat)
    await conv.wait()
    await runner.close()
    reply = " ".join(chat.sent)
    end = [r for r in journal.read() if r["kind"] == "task_end"][-1]
    ok = "red" in reply.lower() and "blue" in reply.lower()
    print(json.dumps({"picture_bytes": len(picture), "reply": reply, "status": end["status"], "cost_usd": round(end["cost_usd"], 4),
                      "saw_both_colours": ok, "file_record": [{k: r.get(k) for k in ("name", "type", "bytes", "seen")} for r in journal.read() if r["kind"] == "file"],
                      "errors": [r for r in journal.read() if r["kind"] == "error"]}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
