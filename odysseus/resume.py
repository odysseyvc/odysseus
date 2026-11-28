"""Picking a message up again after the program was stopped in the middle of it.

A new version goes out by stopping the program and starting it again. Whatever it was doing at that moment is lost.
For a long time the owner was told so at the next start ("I was restarted while working on ...; send it again"), which
is honest and still makes the owner do the work twice. With several versions going out in a day, and the owner using
the assistant meanwhile, it happened to a real request.

So the message being worked on is kept in one small file until its task ends, and at the next start it is run again,
by code, when all of these hold:

  it changed nothing   every tool the interrupted task had used was one that only reads. A task that had already sent,
                       filed, saved or scheduled something is never run again by code: the second run would do it
                       twice. That task is announced, as before, and the owner decides.
  it is recent         the program was down for minutes, not hours. An old instruction is not run as if just given.
  it was words         a message the owner typed. A file, a picture or a voice note is not kept here.
  once                 a message picked up once is not picked up again. If it stopped the program twice, running it
                       a third time is how a program gets stuck restarting.

Whether a tool only reads is decided here from the journal's own records, by name and by what the tool recorded of
itself, and anything not recognised counts as a change.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

RECENT_S = 15 * 60
NOTICE = "I was restarted in the middle of your last message. Nothing had been changed yet, so I'm picking it up again now."
# Tools that only read, by the name their journal record carries. Anything else is a change unless its record says "read".
READS = frozenset({
    "web_search", "recall", "mail_search", "mail_read", "mail_each", "mail_attachment", "calendar_list", "calendar_busy", "mail_profile", "mail_for_code",
    "attio_find", "app_find", "files_find", "list_schedule", "list_watches", "checkin_list", "skill_read", "template", "see_pages", "page_check",
    "times_find", "inbox_list", "inbox_read", "portfolio_show", "who_to_ask_about", "pipeline_review", "round_maths", "review", "close"})


def keep(path: Path, text: str, via: str, again: bool = False) -> bool:
    """Write down the message now being worked on. True if it was written."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"text": text, "via": via, "at": time.time(), "again": bool(again)}, ensure_ascii=False), encoding="utf-8")
        return True
    except OSError:
        return False


def done(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def only_read(records: list[dict[str, Any]]) -> bool:
    """Did the task that was running when these records end use nothing but tools that read?"""
    start = max((i for i, r in enumerate(records) if r.get("kind") == "task_start" and not r.get("background")), default=None)
    if start is None:
        return False
    for r in records[start + 1:]:
        kind = r.get("kind")
        if kind == "gate" and r.get("verdict") in ("approved", "unasked", "standing", "widened to the thread"):
            return False                            # something was let through that is not a plain read
        if kind != "tool":
            continue
        if r.get("ok") is False:
            continue                                # it did not happen
        name = str(r.get("name", ""))
        if name in READS or r.get("action") == "read" or r.get("by") == "code":
            continue
        if name == "manage":
            from . import said
            if said.kind(str(r.get("what", ""))) == said.SHOW:
                continue
        if name.endswith("_find"):
            continue
        return False
    return True


def message(path: Path, records: list[dict[str, Any]], now: float) -> dict[str, Any] | None:
    """The message to pick up again, or None. `records` is the journal as the stopped program left it."""
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(kept, dict) or not str(kept.get("text") or "").strip() or kept.get("again"):
        return None
    try:
        age = now - float(kept.get("at") or 0)
    except (TypeError, ValueError):
        return None
    if not 0 <= age <= RECENT_S or not only_read(records):
        return None
    return {"text": str(kept["text"]), "via": str(kept.get("via") or "telegram"), "at": float(kept["at"])}
