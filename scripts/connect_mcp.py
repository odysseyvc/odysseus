"""Connect a service that runs its own server, once. Prints a link; the owner opens it, signs in and approves.

Run on the machine whose browser will open the link, because the service sends the browser back to this machine:

    python scripts/connect_mcp.py granola

It then lists the tools the service offers, with the service's own label for each, and reads nothing else.
With --tools PATH the list is also written to a file. Attio has its own script, connect_attio.py.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus.attio import CALLBACK, FileTokens, session
from odysseus.config import load

from odysseus.mcpapp import APPS as SERVERS  # noqa: E402


def _from_env_file(key: str) -> str:
    """One value from the .env file beside the program, without printing or keeping anything else in it."""
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


async def main() -> int:
    name = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    if name not in SERVERS:
        print(f"Which service? One of: {', '.join(SERVERS)}")
        return 1
    tokens = FileTokens(Path(load().data_dir) / f"{name}.json")
    env_name = SERVERS[name].get("client_id_env")
    if env_name and await tokens.get_client_info() is None:
        # This service does not let a program register itself: the owner makes an app on the service's site and gives its client id.
        client_id = os.environ.get(env_name) or _from_env_file(env_name)
        if not client_id:
            print(f"{SERVERS[name]['title']} needs an app of your own first. Create it on their developer site with the return address\n"
                  f"  {CALLBACK}\nas a public (native) app with read permission, then put its client id in .env as {env_name}=... and run this again.")
            return 1
        from mcp.shared.auth import OAuthClientInformationFull
        await tokens.set_client_info(OAuthClientInformationFull(client_id=client_id, redirect_uris=[CALLBACK], token_endpoint_auth_method="none",
                                                                grant_types=["authorization_code", "refresh_token"], response_types=["code"],
                                                                scope=SERVERS[name]["scope"], client_name="Odysseus"))

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
