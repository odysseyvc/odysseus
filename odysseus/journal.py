"""The log and the spending ledger.

Every step the agent takes is appended to one file as a line of JSON. The
ledger is derived from the same file, so what was spent can always be checked
against what was done.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable


class Journal:
    # Fields safe to copy to a hosting service's log: statuses and numbers, never contents.
    ECHO_FIELDS = ("status", "ok", "name", "verdict", "outcome", "where", "error", "cost_usd", "seconds", "turns", "parts", "chars", "pages")

    def __init__(self, path: Path, clock: Callable[[], float] = time.time, echo: bool = False):
        self.path = path
        self._clock = clock
        self._echo = echo
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, kind: str, **fields: Any) -> None:
        record = {"ts": round(self._clock(), 3), "kind": kind, **fields}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        if self._echo:
            shown = " ".join(f"{k}={fields[k]}" for k in self.ECHO_FIELDS if k in fields)
            print(f"odysseus {kind} {shown}".rstrip(), flush=True)

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a half-written last line after a crash
        return out


class Ledger:
    """Money spent per calendar day, rebuilt from the journal at start-up."""

    def __init__(self, journal: Journal, today: Callable[[], date] = date.today):
        self._journal = journal
        self._today = today
        self._by_day: dict[str, float] = {}
        for rec in journal.read():
            if rec.get("kind") == "task_end" and rec.get("cost_usd"):
                day = datetime.fromtimestamp(rec["ts"]).date().isoformat()
                self._by_day[day] = self._by_day.get(day, 0.0) + float(rec["cost_usd"])

    def add(self, usd: float) -> None:
        day = self._today().isoformat()
        self._by_day[day] = self._by_day.get(day, 0.0) + usd

    def spent_today(self) -> float:
        return self._by_day.get(self._today().isoformat(), 0.0)
