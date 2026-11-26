"""Is a task getting anywhere? Decided by code from the calls it makes, and used to end one that is not.

A task used to end the moment its estimated cost reached a fixed sum, whatever it was doing: a long piece of work nearly
done was cut off at fifty cents, and the owner was left with half of it. And nothing ended a task that was going nowhere
cheaply: the same call made again and again, or call after call failing, could run on until the same sum was reached.
Those are two different things, and money measures neither.

So the sum is now a mark and not a wall (agent.py): a task that passes it carries on, up to a ceiling several times
higher, for as long as it is making progress. And a task that is not making progress is ended at once, at any cost, by the
two signs here. Both are read from what the task does, not from what the model says about how it is going.

    repeating   the same tool has been asked for with the same inputs three times. Nothing has changed between them
                that the task itself did, so a fourth will not differ.
    failing     four tool calls in a row have come back as errors. Something the task depends on is not there.

A task ended this way says so and gives what it has, so the owner can say what to do differently. What this cannot see:
a task that makes different calls each time and still goes round in circles. The ceiling is what ends that.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SAME_CALL = 3           # times the same call may be asked for
FAILS_IN_A_ROW = 4      # tool results in a row that are errors
WHY = {"repeating": "it was asking for the same thing over and over and getting nowhere",
       "failing": "one step after another was failing"}


class Progress:
    def __init__(self) -> None:
        self._asked: dict[str, int] = {}
        self._fails = 0
        self.steps = 0

    def asked(self, tool: str, inputs: Any) -> None:
        """A tool was asked for. Reading the same thing twice is ordinary; a third time is a loop."""
        key = hashlib.sha256((str(tool) + json.dumps(inputs, sort_keys=True, default=str, ensure_ascii=False)).encode("utf-8")).hexdigest()
        self._asked[key] = self._asked.get(key, 0) + 1
        self.steps += 1

    def came_back(self, failed: bool) -> None:
        self._fails = self._fails + 1 if failed else 0

    def stuck(self) -> str:
        """"" while the task is getting somewhere; otherwise which sign says it is not."""
        if any(n >= SAME_CALL for n in self._asked.values()):
            return "repeating"
        if self._fails >= FAILS_IN_A_ROW:
            return "failing"
        return ""
