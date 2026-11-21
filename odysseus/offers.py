"""What the program has put to the owner and is waiting on, kept in a file.

A question with buttons stays in the chat for as long as the chat does. The program used to remember what each
button meant only while it ran, so a restart turned every waiting question into buttons that did nothing: mail
from a stranger offered for reading, a fact offered for saving, a verdict asked for on a deck. Now each is written
to a file as it is put, and removed when it is answered.

Three kinds are kept, as the conversation holds them:

  a piece of work   (prompt, material, chat)   the chat is not kept; after a restart the owner's own chat is used
  a fact            a string
  a choice          a dict, for a question with its own buttons

An offer nobody answered in two weeks is dropped, and so is the oldest once there are too many: a button that old
says so when tapped, which is what every button did after a restart before.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

KEEP_DAYS = 14
MOST = 200


class Offers(dict):
    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time):
        super().__init__()
        self._path, self._clock, self._when = path, clock, {}
        if path is None or not path.exists():
            return
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return          # an unreadable file loses the waiting questions, as a restart used to; it must not stop the program
        oldest = clock() - KEEP_DAYS * 86400
        for key, row in saved.items() if isinstance(saved, dict) else ():
            when, kind, value = float(row.get("when") or 0), row.get("kind"), row.get("value")
            if when < oldest:
                continue
            if kind == "task" and isinstance(value, list) and len(value) == 2:
                value = (str(value[0]), str(value[1]), None)
            elif not ((kind == "fact" and isinstance(value, str)) or (kind == "choice" and isinstance(value, dict))):
                continue
            super().__setitem__(key, value)
            self._when[key] = when

    def __setitem__(self, key: str, value: Any) -> None:
        super().__setitem__(key, value)
        self._when[key] = self._clock()
        for old in sorted(self._when, key=self._when.get)[:max(0, len(self) - MOST)]:
            super().pop(old, None)
            self._when.pop(old, None)
        self._save()

    def __delitem__(self, key: str) -> None:
        super().__delitem__(key)
        self._when.pop(key, None)
        self._save()

    def pop(self, key: str, *default: Any) -> Any:
        had = key in self
        value = super().pop(key, *default)
        if had:
            self._when.pop(key, None)
            self._save()
        return value

    def _save(self) -> None:
        if self._path is None:
            return
        rows = {}
        for key, value in self.items():
            if isinstance(value, tuple):
                row = {"kind": "task", "value": [value[0], value[1]]}
            else:
                row = {"kind": "fact" if isinstance(value, str) else "choice", "value": value}
            rows[key] = {"when": self._when.get(key, 0.0), **row}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(rows, ensure_ascii=False, default=str), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            pass            # the question is still answerable while the program runs
