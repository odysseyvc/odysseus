"""Does Attio already know who at the firm has met whom? Live, read-only, counts only.

    python scripts/live_attio_met.py

Attio works out, from the mail and calendars its members have synced, when the firm first and last dealt with a person
or a company and which colleague knows them best. This counts how many records carry those fields, how many colleagues
they point to, and whether the meetings Attio holds name more than one colleague. No name, address, date or title is
printed. Changes nothing.
"""
from __future__ import annotations

import asyncio
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus.attio import FileTokens, McpBackend  # noqa: E402
from odysseus.attio_layout import parse  # noqa: E402
from odysseus.config import load  # noqa: E402

CAP = 3000      # records read per object at most


def member(value) -> str:
    return str((value or {}).get("workspace_membership_id") or (value or {}).get("actor_id") or "") if isinstance(value, dict) else ""


async def records(b, obj: str) -> list[dict]:
    out, offset = [], 0
    while offset < CAP:
        page = parse(await b.call("list-records", {"object": obj, "limit": 50, "offset": offset}))
        rows = page.get("records") or []
        out += [r.get("attributes") or {} for r in rows]
        offset += len(rows)
        if not rows or page.get("has_more") is not True:
            break
    return out


async def main() -> int:
    tokens = FileTokens(Path(load().data_dir) / "attio.json")
    if not tokens.signed_in():
        print("Attio is not connected. Run scripts/connect_attio.py first.")
        return 1
    b = McpBackend(tokens)
    try:
        listed = await b.call("list-workspace-members", {})      # a table in text: "[2]{id,name,email,level}:" and a line each
        head = re.match(r"\s*\[(\d+)\]", listed)
        own = {a.lower() for a in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", listed)}
        print(f"workspace members: {head.group(1) if head else len(own)}")
        for obj in ("people", "companies"):
            rows = await records(b, obj)
            n = len(rows)
            met = sum(bool(r.get("first_interaction") or r.get("last_interaction")) for r in rows)
            mail = sum(bool(r.get("last_email_interaction")) for r in rows)
            cal = sum(bool(r.get("last_calendar_interaction")) for r in rows)
            who = Counter(member(r.get("strongest_connection_user")) for r in rows if member(r.get("strongest_connection_user")))
            strength = Counter(str(r.get("strongest_connection_strength") or "(empty)") for r in rows)
            print(f"\n{obj}: {n} records{' (cap reached)' if n >= CAP else ''}")
            print(f"  with any interaction on record: {met}; by mail: {mail}; by calendar: {cal}")
            print(f"  naming the colleague who knows them best: {sum(who.values())}, pointing to {len(who)} different colleague(s)")
            print(f"  strength: {dict(strength.most_common())}")
        now = datetime.now(timezone.utc)
        seen, several, offset = 0, 0, 0
        while offset < 500:
            page = parse(await b.call("search-meetings", {"starts_after": (now - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                                                          "starts_before": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "timezone": "UTC",
                                                          "limit": 100, "offset": offset}))
            rows = page.get("past_meetings") or []
            for m in rows:
                there = {str(p.get("email_address") or "").lower() for p in m.get("participants") or [] if isinstance(p, dict)}
                seen += 1
                several += len(there & own) > 1
            offset += len(rows)
            if not rows or page.get("has_more") is not True:
                break
        print(f"\nmeetings in the last year that Attio holds: {seen}; with more than one colleague present: {several}")
    finally:
        await b.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
