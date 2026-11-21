"""Common free times between two assistants (peers.py), against the real calendar service. No model is called.

  python scripts/live_peers.py

One side is the owner's real calendar, standing in as the colleague who is asked. The other is a stand-in asker with a
made-up calendar. It prints counts and verdicts only: no time, title or name from the real calendar is printed.

Checks what the unit tests cannot: that the service's free/busy answer has the shape the code expects, that what
reaches the asking model holds no title of any real event, that no stretch offered as free overlaps a real meeting,
that turning it off means the calendar is not read, and that none of it costs a model call.
"""
from __future__ import annotations

import asyncio
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus import config  # noqa: E402
from odysseus.apps import Apps, ComposioBackend  # noqa: E402
from odysseus.config import make_clock  # noqa: E402
from odysseus.journal import Journal, Ledger  # noqa: E402
from odysseus.peers import Desk, Switchboard, Trust  # noqa: E402

RESULTS: list[tuple[str, bool]] = []
LINE = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d\d [A-Z][a-z]{2}: (\d\d:\d\d to \d\d:\d\d)(; \d\d:\d\d to \d\d:\d\d)*$")


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


async def main() -> int:
    settings = config.load()
    if not settings.composio_api_key:
        print("No connector key in the settings.")
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="odysseus-peers-"))
    now = make_clock(settings.tz)
    real_journal, asker_journal = Journal(tmp / "real.jsonl"), Journal(tmp / "asker.jsonl")
    apps = Apps(ComposioBackend(settings.composio_api_key, f"odysseus-{settings.owner_id}"), real_journal, now, settings.tz)
    reads, told = [], []

    async def real_busy(start, end):
        reads.append(1)
        return await apps.busy_between(start, end)

    async def asker_busy(start, end):
        return []          # the stand-in asker is free all day, so what comes back is the real calendar's free time

    async def tell(text):
        told.append(text)
    board = Switchboard()
    board.register(Desk(1, "Stand-in asker", Trust(tmp / "t1.json"), asker_journal, busy=asker_busy))
    board.register(Desk(2, "Colleague", Trust(tmp / "t2.json"), real_journal, busy=real_busy, tell=tell))
    run = board.handlers(1, now, settings.tz)["times_find"]

    began = time.monotonic()
    out = await run({"people": ["Colleague"], "minutes": 30, "days": 7})
    took = time.monotonic() - began
    text = out["content"][0]["text"]
    check("the real calendar answers with busy blocks, through the switchboard", not out.get("is_error"), f"{took:.1f} s")
    row = next((r for r in real_journal.read() if r.get("kind") == "exchange"), {})
    check("the exchange is logged on the answering side with a count and no content", row.get("state") == "TASK_STATE_COMPLETED" and "blocks" in row,
          f"{row.get('blocks')} busy blocks in 7 days")

    events = await apps.events_between(datetime.now().astimezone(), datetime.now().astimezone() + timedelta(days=7))
    titles = {str(e.get("summary") or "").strip() for e in events if len(str(e.get("summary") or "").strip()) >= 4}
    guests = {a.get("email", "") for e in events for a in (e.get("attendees") or []) if isinstance(a, dict) and a.get("email")}
    check("no title of a real event is in what the asking model is given", not any(t.lower() in text.lower() for t in titles), f"{len(titles)} titles checked")
    check("no guest's address is in it", not any(g.lower() in text.lower() for g in guests if g), f"{len(guests)} addresses checked")
    day_lines = [line for line in text.splitlines() if re.match(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d\d ", line)]
    check("every line of times has the fixed shape and nothing else", bool(day_lines) and all(LINE.match(line) for line in day_lines), f"{len(day_lines)} days")

    # No stretch offered as free may overlap a meeting the owner is in. Checked against the list of events, read separately.
    zone = datetime.now().astimezone().tzinfo if not settings.tz else __import__("zoneinfo").ZoneInfo(settings.tz)
    meetings = []
    for e in events:
        s, en = (e.get("start") or {}).get("dateTime"), (e.get("end") or {}).get("dateTime")
        declined = any(isinstance(a, dict) and a.get("self") and a.get("responseStatus") == "declined" for a in e.get("attendees") or [])
        if s and en and e.get("transparency") != "transparent" and e.get("status") != "cancelled" and not declined:
            meetings.append((datetime.fromisoformat(s), datetime.fromisoformat(en)))
    year = now().year
    clashes = 0
    for line in day_lines:
        day = datetime.strptime(f"{line[4:10]} {year}", "%d %b %Y").date()
        for a, b in re.findall(r"(\d\d:\d\d) to (\d\d:\d\d)", line):
            fs = datetime.combine(day, datetime.strptime(a, "%H:%M").time(), zone)
            fe = datetime.combine(day, datetime.strptime(b, "%H:%M").time(), zone)
            clashes += sum(1 for ms, me in meetings if ms < fe and me > fs)
    check("no time offered as free overlaps a real meeting", clashes == 0, f"{len(meetings)} meetings checked, {clashes} clashes")

    check("the answering person is sent one line saying who looked and what was given", len(told) == 1 and "busy and free times only" in told[0])
    board.desk(2).trust.set(1, False)
    before = len(reads)
    out = await run({"people": ["Colleague"]})
    check("turned off by the answering side: refused, and the calendar is not read", bool(out.get("is_error")) and len(reads) == before)
    spent = Ledger(real_journal).spent_today() + Ledger(asker_journal).spent_today()
    check("no model call and no cost on either side", spent == 0, f"${spent:.4f}")

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
