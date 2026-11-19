"""Capture, live and read-only: what tomorrow morning's question would hold. Nothing is asked, filed or changed.

    python scripts/live_capture.py [days back, default 3]

Reads the real calendar, the real inbox and the real database through the classes the program uses, and prints counts
and each person's mail domain and how they came up, without names or addresses.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus.apps import Apps, ComposioBackend  # noqa: E402
from odysseus.attio import Attio, FileTokens, McpBackend  # noqa: E402
from odysseus.capture import Capture, met, wrote  # noqa: E402
from odysseus.config import load, make_clock  # noqa: E402
from odysseus.journal import Journal  # noqa: E402


async def main() -> int:
    s = load()
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    now = make_clock(s.tz)
    with tempfile.TemporaryDirectory() as tmp:
        journal = Journal(Path(tmp) / "j.jsonl")
        apps = Apps(ComposioBackend(s.composio_api_key, f"odysseus-{s.owner_id}"), journal, now, s.tz)
        attio = Attio(McpBackend(FileTokens(Path(s.data_dir) / "attio.json")), journal)
        await attio.layout()
        looked = []

        async def in_database(email: str) -> bool:
            found = (await attio.intake._existing("people", {"email_addresses": [email]}))[0] is not None
            looked.append(found)
            return found

        async def nobody(question: str, prompt: str) -> None:
            raise AssertionError("this script asks nothing")
        from zoneinfo import ZoneInfo
        capture = Capture(apps.events_between, apps.messages_for_code, apps.my_address, in_database, nobody, journal, now=now,
                          zone=ZoneInfo(s.tz) if s.tz else None)
        until, t = now(), time.monotonic()
        since = until - timedelta(days=days)
        events = await apps.events_between(since, until)
        mails = await apps.messages_for_code(f"newer_than:{days}d in:inbox -from:me", 60)
        mine = await apps.my_address()
        print(f"last {days} days: {len(events)} calendar events, {len(met(events, since, until))} outside guests at meetings held; "
              f"{len(mails)} inbox messages, {len(wrote(mails, since, until, mine, capture._zone))} from people outside")
        people = await capture.gather(since, until)
        print(f"looked up in the database: {len(looked)} addresses, {sum(looked)} already there; would be proposed: {len(people)}  ({time.monotonic() - t:.0f}s, no model call)")
        for i, p in enumerate(people, 1):
            kinds = ", ".join(sorted({h.split(":")[0] for h in p["how"]}))
            print(f"  {i}. someone at {p['email'].split('@')[1]} ({'named' if p['name'] else 'no name'}): {kinds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
