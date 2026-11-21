"""What went wrong in real use, read from the program's own logs, so that each kind of failure can become a test.

    python scripts/review_log.py            every person's log in the data folder
    python scripts/review_log.py --all      also the kinds already listed by an earlier run

The log holds how each task ended, every error with where it happened, every tool that failed, and every question the
owner declined. This groups them into kinds (where and what, never the words), counts them, and remembers which
kinds it has shown before, so a later run lists only what is new. A kind is new work: a test that reproduces it, then
the fix. Nothing from a message, an email or a name is printed: only the program's own labels.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus.config import load  # noqa: E402
from odysseus.journal import Journal  # noqa: E402


def label(text: object) -> str:
    """An error's text as a kind: its first words, with numbers, addresses and anything quoted taken out."""
    s = re.sub(r"\"[^\"]*\"|'[^']*'|\S+@\S+|https?://\S+", "…", str(text or ""))
    return " ".join(re.sub(r"\d+", "N", s).split()[:8])[:80]


def kinds_in(records) -> tuple[Counter, dict, Counter]:
    found, last, ended = Counter(), {}, Counter()
    for r in records:
        kind, key = r.get("kind"), None
        if kind == "task_end":
            ended[str(r.get("status"))] += 1
            if r.get("status") not in ("done", "stopped"):
                key = ("a task ended as", str(r.get("status")), label(r.get("detail")))
        elif kind == "error":
            key = ("error", str(r.get("where")), str(r.get("error")))
        elif kind == "tool" and r.get("ok") is False:
            key = ("a tool failed", str(r.get("name")), label(r.get("error")))
        elif kind == "gate" and r.get("verdict") in ("declined", "expired", "refused", "unasked"):
            key = ("a question was " + str(r.get("verdict")), str(r.get("tool", "")).replace("mcp__odysseus__", ""), label(r.get("reason")))
        elif kind == "limit":
            key = ("a limit ended a task", str(r.get("which")), "")
        elif kind in ("speech", "voice", "file") and r.get("ok") is False:
            key = (f"{kind} failed", label(r.get("error")), "")
        if key:
            found[key] += 1
            last[key] = r.get("ts", 0)
    return found, last, ended


def test_name(key: tuple[str, str, str]) -> str:
    return "test_" + re.sub(r"[^a-z0-9]+", "_", " ".join(k for k in key if k).lower()).strip("_")[:70]


def main() -> int:
    root = Path(load().data_dir)
    logs = [root / "journal.jsonl"] + sorted((root / "people").glob("*/journal.jsonl"))
    found, last, ended = Counter(), {}, Counter()
    for path in logs:
        if path.exists():
            f, l, e = kinds_in(Journal(path).read())
            found += f
            ended += e
            for k, ts in l.items():
                last[k] = max(ts, last.get(k, 0))
    seen_file = root / "reviewed.json"
    try:
        seen = {tuple(k) for k in json.loads(seen_file.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        seen = set()
    show_all = "--all" in sys.argv
    print(f"{len([p for p in logs if p.exists()])} log(s). Tasks by how they ended: {dict(ended.most_common())}")
    shown = [k for k, _ in found.most_common() if show_all or k not in seen]
    print(f"{len(found)} kinds of failure in all; {len(shown)} {'shown' if show_all else 'not listed by an earlier run'}.\n")
    by_group = defaultdict(list)
    for k in shown:
        by_group[k[0]].append(k)
    for group, keys in by_group.items():
        print(group)
        for k in keys:
            when = datetime.fromtimestamp(last[k]).strftime("%d %b") if last.get(k) else "?"
            print(f"  {found[k]:>4} x  {k[1]}{' : ' + k[2] if k[2] else ''}   (last {when})   -> {test_name(k)}")
    seen_file.write_text(json.dumps(sorted(seen | set(found))), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
