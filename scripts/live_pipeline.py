"""The pipeline review and the portfolio's domains against the real database and mailbox. Read-only, counts only.

    python scripts/live_pipeline.py            the review's counts, how long it took, and whether a PDF of it can be printed
    python scripts/live_pipeline.py --keep DIR also writes the message and the PDF into DIR, for a look. They hold the firm's
                                               own records: DIR must be a private folder.

No model is called. Nothing is written to the database, sent to anyone or printed here by name.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus import pipeline  # noqa: E402
from odysseus.apps import Apps, ComposioBackend  # noqa: E402
from odysseus.attio import Attio, FileTokens, McpBackend  # noqa: E402
from odysseus.config import load, make_clock  # noqa: E402
from odysseus.journal import Journal  # noqa: E402
from odysseus.portfolio import Portfolio, UpdateLoop, portfolio_domains  # noqa: E402


async def main() -> int:
    s = load()
    keep = Path(sys.argv[sys.argv.index("--keep") + 1]) if "--keep" in sys.argv else None
    now = make_clock(s.tz)
    from zoneinfo import ZoneInfo
    zone = ZoneInfo(s.tz) if s.tz else None
    with tempfile.TemporaryDirectory() as tmp:
        journal = Journal(Path(tmp) / "j.jsonl")
        apps = Apps(ComposioBackend(s.composio_api_key, f"odysseus-{s.owner_id}"), journal, now, s.tz) if s.composio_api_key else None
        attio = Attio(McpBackend(FileTokens(Path(s.data_dir) / "attio.json")), journal)
        printer = None
        if s.e2b_api_key and s.sandbox_template:
            from odysseus.sandbox import E2B
            printer = E2B(s.e2b_api_key, s.sandbox_template)
        searches = []

        async def messages(query: str, limit: int):
            searches.append(query.split(" from:")[0].split(" to:")[0])
            return await apps.messages_for_code(query, limit)
        reviewer = pipeline.Reviewer(attio, journal, now=now, messages=messages if apps is not None else None, zone=zone, printer=printer)
        t = time.monotonic()
        review = await reviewer.make()
        took = time.monotonic() - t
        found = pipeline.pipelines(attio.shape)
        print(f"pipelines found in the layout: {len(found)}; stages: {[len(p['stages']) for p in found]}; deadline fields: {[len(p['dates']) for p in found]}; "
              f"a next-step field: {[bool(p['step']) for p in found]}")
        print(f"review: {pipeline.counts(review)}; overdue tasks {len(review['tasks'])}; stages taken as closed {[len(p['closed']) for p in review['pipelines']]}; "
              f"notes {len(review['notes'])}; mail searches {len(searches)}; {took:.0f}s, no model call")
        message = pipeline.text(review)
        print(f"the message: {len(message)} characters, {message.count(chr(10)) + 1} lines")
        t = time.monotonic()
        made = await reviewer.pdf(review)
        print(f"the PDF: {'not made' if made is None else f'{len(made[1])} bytes'}; {time.monotonic() - t:.0f}s on the rented machine")
        if keep is not None:
            keep.mkdir(parents=True, exist_ok=True)
            (keep / "review.txt").write_text(message, encoding="utf-8")
            if made:
                (keep / made[0]).write_bytes(made[1])
        domains = await portfolio_domains(attio)
        loop = UpdateLoop(Portfolio(Path(tmp) / "p.json"), apps.messages_for_code, lambda: portfolio_domains(attio), None, journal, now=now, zone=zone) if apps else None
        updates = await loop.find(days=30) if loop else []
        print(f"portfolio: {len(domains)} company domains on lists named portfolio; messages of the last 30 days that read like an update from one: {len(updates)}")
        await attio._b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
