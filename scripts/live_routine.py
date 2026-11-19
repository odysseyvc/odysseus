"""Live check of an unattended routine that reads the connected services, with the real model and without Telegram.

Runs one routine's instructions once, exactly as the scheduler would, and prints what it would have sent, every
tool it used, what was refused and what it cost. Nothing is sent anywhere and nothing can be changed: a routine
has reading tools only. Reads of X cost money on the owner's X account.

    .venv/Scripts/python scripts/live_routine.py "Find founders who announced a new company today."
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
import warnings
from dataclasses import replace
from datetime import datetime
from pathlib import Path

warnings.simplefilter("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exa_py import Exa  # noqa: E402

from odysseus.attio import Attio, FileTokens, McpBackend  # noqa: E402
from odysseus.config import load  # noqa: E402
from odysseus.journal import Journal, cost_of  # noqa: E402
from odysseus.mcpapp import connected  # noqa: E402
from odysseus.memory import MemoryStore  # noqa: E402
from odysseus.routines import make_routine_runner  # noqa: E402
from odysseus.tools import make_gate, make_tool_server  # noqa: E402


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    real = load()
    settings = replace(real, data_dir=Path(tempfile.mkdtemp(prefix="odysseus-routine-")))
    journal = Journal(settings.data_dir / "journal.jsonl")
    services = connected(Path(real.data_dir), journal)
    tokens = FileTokens(Path(real.data_dir) / "attio.json")
    attio = Attio(McpBackend(tokens), journal) if tokens.signed_in() else None
    make_gate(journal, attio=attio, services=services)      # gives every reader the one record of what was read
    memory = MemoryStore(settings.data_dir / "memory")
    search = Exa(api_key=settings.exa_api_key).tools.web_search()
    run = make_routine_runner(settings, journal, make_tool_server(search, journal, memory, read_only=True, attio=attio, services=services),
                              memory, datetime.now, services=services, attio=attio)
    print("reads:", ", ".join(s.title for s in services) + (", Attio" if attio else ""))
    t = time.monotonic()
    status, text, cost = await run(sys.argv[1], tuple(sys.argv[2:]))
    print(f"\n=== {status} in {time.monotonic() - t:.0f}s, model ${cost:.4f} ===\n{text}\n")
    paid = 0.0
    for r in journal.read():
        if r.get("kind") == "tool":
            paid += cost_of(r)
            print("tool", r.get("name"), r.get("tool", ""), "ok" if r.get("ok") else f"REFUSED/FAILED: {r.get('error', '')}",
                  f"${r['cost_usd']}" if r.get("cost_usd") else "")
    print(f"\nreads and searches: ${paid:.4f} (estimated for X)")
    for s in services:
        await s._b.close()
    if attio is not None:
        await attio._b.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
