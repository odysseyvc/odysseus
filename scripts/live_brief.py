"""Live check of the brief before a meeting, with the real calendar, the real model and without Telegram.

Looks ahead in the owner's calendar (seven days unless told otherwise), takes the first meeting with a guest from
outside the firm, and prepares its brief exactly as the program would half an hour before. The brief is printed, not
sent, and nothing is recorded as briefed. It makes real web searches for the guest's name and company, and real reads
of mail, the firm's database and the meeting notes; it can change nothing.

    .venv/Scripts/python scripts/live_brief.py            # the next meeting in seven days
    .venv/Scripts/python scripts/live_brief.py 14         # look fourteen days ahead
    .venv/Scripts/python scripts/live_brief.py "Jo Rao <jo@orbitlabs.example>" "Intro call"     # a made-up meeting with this guest
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
import warnings
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

warnings.simplefilter("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exa_py import Exa  # noqa: E402

from odysseus.apps import Apps, ComposioBackend  # noqa: E402
from odysseus.attio import Attio, FileTokens, McpBackend  # noqa: E402
from odysseus.brief import Briefer, meetings  # noqa: E402
from odysseus.config import load, make_clock  # noqa: E402
from odysseus.journal import Journal, Ledger, cost_of  # noqa: E402
from odysseus.mcpapp import connected  # noqa: E402
from odysseus.memory import MemoryStore  # noqa: E402
from odysseus.routines import make_routine_runner  # noqa: E402
from odysseus.search import make_search_handler  # noqa: E402
from odysseus.tools import make_gate, make_tool_server  # noqa: E402


async def main():
    person = sys.argv[1] if len(sys.argv) > 1 and "@" in sys.argv[1] else ""
    days = int(sys.argv[1]) if len(sys.argv) > 1 and not person else 7
    real = load()
    if not real.composio_api_key:
        print("Mail and calendar are not connected.")
        return 1
    settings = replace(real, data_dir=Path(tempfile.mkdtemp(prefix="odysseus-brief-")))
    journal, now = Journal(settings.data_dir / "journal.jsonl"), make_clock(real.tz)
    apps = Apps(ComposioBackend(real.composio_api_key, f"odysseus-{real.owner_id}"), journal, now, real.tz)
    services = connected(Path(real.data_dir), journal)
    tokens = FileTokens(Path(real.data_dir) / "attio.json")
    attio = Attio(McpBackend(tokens), journal) if tokens.signed_in() else None
    gate = make_gate(journal, now, apps=apps, attio=attio, services=services)
    memory = MemoryStore(Path(real.data_dir) / "memory", now=now)       # the owner's real notes, read only
    search = Exa(api_key=settings.exa_api_key).tools.web_search()
    run = make_routine_runner(settings, journal, make_tool_server(search, journal, memory, read_only=True, apps=apps, attio=attio, services=services),
                              memory, now, with_apps=True, services=services, attio=attio, model=settings.model, program_tasks=True)
    lead = timedelta(days=days)
    if person:      # a made-up meeting with a named guest, for when the calendar holds none: "Name <address>" "Title"
        name, _, address = person.rpartition("<")
        found = [{"id": "made-up", "start": now() + timedelta(minutes=30), "title": sys.argv[2] if len(sys.argv) > 2 else "Catch-up",
                  "description": "", "location": "", "guests": [{"email": address.strip(" >").lower(), "name": name.strip()}], "others": 0}]
    else:
        found = meetings(await apps.events_between(now(), now() + lead), now(), lead)
    print(f"meetings with someone from outside in the next {days} days: {len(found)}")
    if not found:
        return 0
    out: list[str] = []

    async def events_between(start, end):
        return []

    async def keep(text):
        out.append(text)

    briefer = Briefer(events_between, run, keep, journal, Ledger(journal), settings.daily_usd, now=now, search=make_search_handler(search, journal),
                      taint=gate.taint, lead=lead)
    t = time.monotonic()
    await briefer._brief(found[0])
    print(f"\n=== {time.monotonic() - t:.0f}s ===\n" + ("\n".join(out) or "(nothing was sent: the brief was quiet)") + "\n")
    total = 0.0
    for r in journal.read():
        total += cost_of(r)
        if r.get("kind") == "tool":
            print("tool", r.get("name"), r.get("tool", ""), "ok" if r.get("ok") else f"REFUSED/FAILED: {str(r.get('error', ''))[:110]}")
        elif r.get("kind") in ("brief", "task_end"):
            print(r.get("kind"), {k: v for k, v in r.items() if k not in ("ts", "kind")})
    print(f"\ncost: ${total:.4f}")
    for s in services:
        await s._b.close()
    if attio is not None:
        await attio._b.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
