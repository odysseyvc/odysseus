"""Connect Attio, once. Prints a link; the owner opens it, signs in at Attio and approves.

Run on the machine whose browser will open the link, because Attio sends the browser back to this machine:

    python scripts/connect_attio.py

It then lists the tools Attio offers, with Attio's own label for each, and reads nothing else.
With --tools PATH the list is also written to a file.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus.attio import FileTokens, session
from odysseus.config import load


async def main() -> int:
    settings = load()
    tokens = FileTokens(Path(settings.data_dir) / "attio.json")

    async def show(link: str) -> None:
        print(f"OPEN THIS LINK AND APPROVE:\n{link}\n", flush=True)

    async with session(tokens, show) as s:
        tools = (await s.list_tools()).tools
        rows = [{"name": t.name, "labels": (t.annotations.model_dump(exclude_none=True) if t.annotations else {}),
                 "description": " ".join((t.description or "").split())[:300], "inputs": t.input_schema} for t in tools]
    print(f"CONNECTED. Attio offers {len(rows)} tools.", flush=True)
    for r in rows:
        print(f"  {r['name']:42} {r['labels']}")
    if "--tools" in sys.argv:
        Path(sys.argv[sys.argv.index("--tools") + 1]).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
