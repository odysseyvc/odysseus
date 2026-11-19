"""Connect a service that runs its own server, once. Prints a link; the owner opens it, signs in and approves.

Run on the machine whose browser will open the link, because the service sends the browser back to this machine:

    python scripts/connect_mcp.py granola

It then lists the tools the service offers, with the service's own label for each, and reads nothing else.
With --tools PATH the list is also written to a file. Attio has its own script, connect_attio.py.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus.attio import FileTokens, session
from odysseus.config import load

from odysseus.mcpapp import APPS as SERVERS  # noqa: E402


async def main() -> int:
    name = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    if name not in SERVERS:
        print(f"Which service? One of: {', '.join(SERVERS)}")
        return 1
    tokens = FileTokens(Path(load().data_dir) / f"{name}.json")

    async def show(link: str) -> None:
        print(f"OPEN THIS LINK AND APPROVE:\n{link}\n", flush=True)

    async with session(tokens, show, SERVERS[name]["url"], SERVERS[name]["scope"]) as s:
        tools = (await s.list_tools()).tools
        rows = [{"name": t.name, "labels": (t.annotations.model_dump(exclude_none=True) if t.annotations else {}),
                 "description": " ".join((t.description or "").split())[:600], "inputs": t.input_schema} for t in tools]
    print(f"CONNECTED. {name} offers {len(rows)} tools.", flush=True)
    for r in rows:
        print(f"  {r['name']:36} {r['labels']}")
    if "--tools" in sys.argv:
        Path(sys.argv[sys.argv.index("--tools") + 1]).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
