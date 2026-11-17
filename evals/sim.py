"""A world that moves: time, events, other people, and tools that sometimes fail.

`Sim` is the seeded mailbox and calendar of world.py with four things added, following
the published design of Meta's ARE (the platform under the Gaia2 benchmark):

  time      a clock the suite owns. Each tool call can cost simulated seconds, and a
            scenario can wait: minutes or days pass, and whatever was due happens.
  events    a queue of things that happen by themselves at a set time (a new email
            arrives), and conditional events that happen in answer to the agent
            (a correspondent replies two hours after being written to).
  others    people other than the owner acting on the same world through the same
            operations: someone else moves a meeting, cancels one, sends mail.
  faults    a seeded plan of tool failures, applied at the boundary where the real
            service would be: an error, a rate limit, an empty answer, and the worst
            one, a write that succeeds although the call reports a timeout.

Everything random comes from named streams split from one seed, so that changing
the fault rate does not reshuffle anything else, and any failing run can be replayed.
Every agent call and every world event is appended to `log`, which the invariant
checks read.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from .world import NOW, World, WorldError

WRITES = {"GMAIL_SEND_EMAIL", "GMAIL_REPLY_TO_THREAD", "GOOGLECALENDAR_CREATE_EVENT", "GOOGLECALENDAR_PATCH_EVENT",
          "GOOGLECALENDAR_DELETE_EVENT", "GMAIL_CREATE_EMAIL_DRAFT", "GMAIL_ADD_LABEL_TO_EMAIL"}
READ_FAULTS = ("error", "rate")     # a silently empty answer is planned by name in a scenario, never drawn at random:
                                    # no agent can be expected to know that a plausible answer was wrong
WRITE_FAULTS = ("error", "rate", "lost_ack")


class Clock:
    def __init__(self, start: datetime = NOW):
        self.t = start

    def now(self) -> datetime:
        return self.t


class Streams:
    """Independent random streams from one seed, by name."""

    def __init__(self, seed: str):
        self._seed, self._streams = seed, {}

    def __call__(self, name: str) -> random.Random:
        if name not in self._streams:
            self._streams[name] = random.Random(f"{self._seed}|{name}")
        return self._streams[name]


@dataclass
class Fault:
    """One planned failure: on the nth call of a tool, fail in this way."""
    slug: str
    nth: int
    kind: str       # error | rate | empty | lost_ack


@dataclass
class Sim(World):
    clock: Clock = field(default_factory=Clock)
    streams: Streams = field(default_factory=lambda: Streams("0"))
    fault_rate: float = 0.0             # chance that any one call fails, in the noisy conditions
    planned: list[Fault] = field(default_factory=list)
    tick_s: int = 0                     # simulated seconds each tool call takes

    def __post_init__(self):
        World.__init__(self, self.streams("noise").randint(0, 10_000))
        self.log: list[dict[str, Any]] = []
        self.faults_injected: list[str] = []
        self._queue: list[tuple[datetime, int, str, Callable[["Sim"], None]]] = []
        self._conditional: list[tuple[Callable[["Sim"], bool], timedelta, str, Callable[["Sim"], None]]] = []
        self._counts: dict[str, int] = {}
        self._last_faulted: str = ""
        self._n = 0

    # --- time and events ------------------------------------------------------------
    def at(self, when: datetime, label: str, action: Callable[["Sim"], None]) -> None:
        self._n += 1
        self._queue.append((when, self._n, label, action))
        self._queue.sort(key=lambda e: (e[0], e[1]))

    def after(self, delay: timedelta, label: str, action: Callable[["Sim"], None]) -> None:
        self.at(self.clock.t + delay, label, action)

    def when(self, predicate: Callable[["Sim"], bool], delay: timedelta, label: str, action: Callable[["Sim"], None]) -> None:
        """Once `predicate` becomes true, do `action` after `delay`. Fires once."""
        self._conditional.append((predicate, delay, label, action))

    def tick(self) -> None:
        """Let everything due by now happen."""
        for item in list(self._conditional):
            if item[0](self):
                self._conditional.remove(item)
                self.after(item[1], item[2], item[3])
        while self._queue and self._queue[0][0] <= self.clock.t:
            when, _, label, action = self._queue.pop(0)
            self.log.append({"t": when, "who": "world", "what": label})
            action(self)

    def advance(self, delta: timedelta) -> None:
        end = self.clock.t + delta
        while True:
            self.tick()
            if not self._queue or self._queue[0][0] > end:
                break
            self.clock.t = self._queue[0][0]
        self.clock.t = end
        self.tick()

    # --- other people ---------------------------------------------------------------
    def deliver(self, sender: str, subject: str, body: str, thread: str | None = None) -> str:
        """Somebody sends the owner an email. It arrives now."""
        self._n += 1
        mid = f"n{self._n:03d}"
        self.mail.append({"id": mid, "thread": thread or f"t{mid}", "from": sender, "date": self.clock.t, "unread": True, "subject": subject, "body": body})
        return mid

    def others_move(self, event_id: str, start: datetime) -> None:
        e = next(e for e in self.events if e["id"] == event_id)
        e["start"] = start

    def others_cancel(self, event_id: str) -> None:
        self.events[:] = [e for e in self.events if e["id"] != event_id]

    # --- the connector's interface, with faults --------------------------------------
    def _fault_for(self, slug: str) -> str | None:
        n = self._counts[slug]
        for f in self.planned:
            if f.slug == slug and f.nth == n:
                return f.kind
        rng = self.streams("faults")
        roll, pick = rng.random(), rng.random()       # drawn on every call, so the plan does not depend on what came before
        if self.fault_rate and roll < self.fault_rate and self._last_faulted != slug:      # transient: never twice running on one tool
            kinds = WRITE_FAULTS if slug in WRITES else READ_FAULTS
            return kinds[int(pick * len(kinds))]
        return None

    def execute(self, slug: str, arguments: dict[str, Any]) -> Any:
        if self.tick_s:
            self.clock.t += timedelta(seconds=self.tick_s)
        self.tick()
        self._counts[slug] = self._counts.get(slug, 0) + 1
        fault = self._fault_for(slug)
        self._last_faulted = slug if fault else ""
        entry = {"t": self.clock.t, "who": "agent", "what": slug, "args": arguments, "fault": fault}
        self.log.append(entry)
        if fault:
            self.faults_injected.append(f"{slug}:{fault}")
        if fault == "error":
            raise WorldError("The service is temporarily unavailable (503). Nothing was done.")
        if fault == "rate":
            raise WorldError("Rate limit exceeded (429). Nothing was done. Try again shortly.")
        result = World.execute(self, slug, arguments)
        self.tick()
        if fault == "lost_ack":
            # The worst kind: the action happened, and the caller is told it timed out.
            raise WorldError("The request timed out before a response arrived.")
        if fault == "empty":
            return {"messages": []} if slug.startswith("GMAIL") else {"items": []}
        return result
