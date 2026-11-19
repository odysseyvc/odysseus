"""Attio, live and read-only: who is signed in, and how the workspace is laid out. Changes nothing.

    python scripts/live_attio.py

Goes through the same class and gate the assistant uses, so a tool the gate would ask about is not run here.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus.attio import ATTIO_RUN, Attio, FileTokens, McpBackend
from odysseus.config import load
from odysseus.journal import Journal
from odysseus.tools import make_gate


async def main() -> int:
    settings = load()
    tokens = FileTokens(Path(settings.data_dir) / "attio.json")
    if not tokens.signed_in():
        print("Attio is not connected. Run scripts/connect_attio.py first.")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        attio = Attio(McpBackend(tokens), Journal(Path(tmp) / "j.jsonl"))
        gate = make_gate(Journal(Path(tmp) / "g.jsonl"), attio=attio)
        h = attio.handlers()
        t = time.time()
        listing = (await h["attio_find"]({}))["content"][0]["text"]
        print(f"attio_find: {len(listing.splitlines()) - 2} tools in {time.time() - t:.1f}s; "
              f"{listing.count('(runs at once)')} read, {listing.count('(asks the owner first)')} ask first")
        for name in sys.argv[1:] or ["whoami", "list-objects", "list-lists"]:
            call = {"tool": name, "arguments": {}}
            if gate.decide(ATTIO_RUN, call) != "allow":
                print(f"\n== {name}: the gate would ask; not run here")
                continue
            t = time.time()
            out = await h["attio_run"](call)
            text = out["content"][0]["text"].split("\n\n", 1)[-1]
            print(f"\n== {name} ({time.time() - t:.1f}s, {len(text)} chars{', ERROR' if out.get('is_error') else ''})\n{text[:2500]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
