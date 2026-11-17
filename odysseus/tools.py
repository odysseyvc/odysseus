"""Every tool the model can call, gathered into one server.

A tool is added to the model's reach in two steps, both here:
  1. it is built into the server (make_tool_server)
  2. its name is listed in ALLOWED_TOOLS, the gate's list of tools that run without asking

Everything on that list either reads public information or writes only to
Odysseus's own files (its memory and its schedule). Those writes are allowed
without a question, and every one of them is reported to the owner by code after
the task (conversation.py).

A routine runs with nobody watching, so it gets a smaller set, ROUTINE_TOOLS:
it can search the web and search memory, and it can write nothing.
"""
from __future__ import annotations

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import apps as apps_mod
from .apps import Apps
from . import anyapp as anyapp_mod
from .anyapp import AnyApp
from . import browser as browser_mod
from .browser import Browser
from .gate import ALLOW, ASK, Gate
from .journal import Journal
from .memory import MEMORY_NOTE, MemoryError_, MemoryStore
from .schedule import REPEATS, ScheduleError, ScheduleStore, parse_when
from .search import SERVER, SearchSpec, make_search_handler


def _name(n: str) -> str:
    return f"mcp__{SERVER}__{n}"


SEARCH_TOOL = _name("web_search")
MEMORY_TOOLS = tuple(_name(n) for n in ("remember", "forget", "note", "recall"))
SCHEDULE_TOOLS = tuple(_name(n) for n in ("schedule", "list_schedule", "cancel_schedule"))
ALLOWED_TOOLS = frozenset({SEARCH_TOOL, *MEMORY_TOOLS, *SCHEDULE_TOOLS})
ROUTINE_TOOLS = frozenset({SEARCH_TOOL, _name("recall")})
SCHEDULE_TOOL = _name("schedule")


def _schedule_decision(args: dict[str, Any]) -> str:
    # A reminder is a fixed message to the owner: allowed. A routine commits the owner to
    # repeated unattended runs that cost money: the owner is asked first.
    return ASK if str(args.get("kind", "")).strip().lower() == "routine" else ALLOW


def describe_routine(args: dict[str, Any], now=None, with_apps: bool = False) -> str:
    """The question shown to the owner. Built from the tool's real inputs; the model does not write it."""
    from datetime import datetime
    when = parse_when(args.get("when", ""), (now or datetime.now)())
    repeat = (args.get("repeat") or "once").strip().lower()
    if repeat not in REPEATS:
        raise ScheduleError("unknown repeat")
    rhythm = {"once": "Once", "hourly": "Every hour", "daily": "Every day", "weekdays": "Every weekday", "weekly": "Every week"}[repeat]
    first = when.strftime("%a %d %b %Y at %H:%M")
    text = " ".join(str(args.get("text", "")).split())
    return (f"Set up this routine?\n\n{rhythm}, {'on' if repeat == 'once' else 'starting'} {first}.\n"
            "Each run uses the small model, can only read ("
            + ("your mail and calendar, the web and my notes" if with_apps else "the web and my notes")
            + "), and sends you the result.\n\n"
            f"Instructions it will follow:\n{text}")


def routine_tools(with_apps: bool) -> frozenset[str]:
    """What an unattended routine may use: reading only, including the owner's mail and calendar once connected."""
    return ROUTINE_TOOLS | (frozenset(apps_mod.READ_TOOLS) if with_apps else frozenset())


def make_gate(journal: Journal, now=None, timeout_s: float = 600.0, with_apps: bool = False, with_browser: bool = False,
              browser_ask_signed_in: bool = False, anyapp: AnyApp | None = None) -> Gate:
    """The gate for conversations, with a rule for every tool that exists."""
    gate = Gate(journal, timeout_s)
    if with_browser:
        browser_mod.add_rules(gate, browser_ask_signed_in)
    if anyapp is not None:
        anyapp.add_rules(gate)
    if with_apps:
        from datetime import datetime
        apps_mod.add_rules(gate, now or datetime.now)
    for name in (SEARCH_TOOL, *MEMORY_TOOLS, _name("list_schedule"), _name("cancel_schedule")):
        gate.rule(name, ALLOW)
    gate.rule(SCHEDULE_TOOL, _schedule_decision, lambda args: describe_routine(args, now, with_apps))
    return gate


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def make_memory_handlers(memory: MemoryStore, journal: Journal) -> dict[str, Any]:
    async def remember(args: dict[str, Any]) -> dict[str, Any]:
        try:
            entry = memory.remember(args.get("text", ""), args.get("replaces") or None)
        except MemoryError_ as e:
            journal.write("tool", name="remember", ok=False, error=str(e))
            return _err(str(e))
        journal.write("tool", name="remember", ok=True, id=entry.id, replaces=args.get("replaces") or None)
        return _ok(f"Saved as [{entry.id}].")

    async def forget(args: dict[str, Any]) -> dict[str, Any]:
        old = memory.forget(str(args.get("id", "")))
        journal.write("tool", name="forget", ok=old is not None, id=args.get("id"))
        return _ok(f'Forgot "{old.text}".') if old else _err(f"There is no entry with id {args.get('id')}.")

    async def note(args: dict[str, Any]) -> dict[str, Any]:
        try:
            memory.note(args.get("text", ""))
        except MemoryError_ as e:
            journal.write("tool", name="note", ok=False, error=str(e))
            return _err(str(e))
        journal.write("tool", name="note", ok=True)
        return _ok("Noted.")

    async def recall(args: dict[str, Any]) -> dict[str, Any]:
        hits = memory.search(str(args.get("query", "")))
        journal.write("tool", name="recall", ok=True, results=len(hits))
        if not hits:
            return _ok("Nothing in memory matches.")
        return _ok(MEMORY_NOTE + "\n\n" + "\n".join(f"({source}) {body}" for source, body in hits))

    return {"remember": remember, "forget": forget, "note": note, "recall": recall}


def make_schedule_handlers(schedule: ScheduleStore, journal: Journal) -> dict[str, Any]:
    async def add(args: dict[str, Any]) -> dict[str, Any]:
        try:
            job = schedule.add(str(args.get("kind", "")), args.get("text", ""), args.get("when", ""), args.get("repeat") or "once")
        except ScheduleError as e:
            journal.write("tool", name="schedule", ok=False, error=str(e))
            return _err(str(e))
        journal.write("tool", name="schedule", ok=True, id=job.id, job_kind=job.kind, when=job.next_run.isoformat(), repeat=job.repeat)
        return _ok("Scheduled: " + job.describe())

    async def listing(args: dict[str, Any]) -> dict[str, Any]:
        journal.write("tool", name="list_schedule", ok=True)
        return _ok(schedule.listing())

    async def cancel(args: dict[str, Any]) -> dict[str, Any]:
        job = schedule.cancel(str(args.get("id", "")))
        journal.write("tool", name="cancel_schedule", ok=job is not None, id=args.get("id"))
        return _ok("Cancelled: " + job.describe()) if job else _err(f"There is no scheduled job with id {args.get('id')}.")

    return {"schedule": add, "list_schedule": listing, "cancel_schedule": cancel}


def make_tool_server(spec: SearchSpec, journal: Journal, memory: MemoryStore, schedule: ScheduleStore | None = None,
                     read_only: bool = False, gate: Gate | None = None, apps: Apps | None = None,
                     browser: Browser | None = None, anyapp: AnyApp | None = None):
    """The full server for conversations, or (read_only) the small one for unattended routines."""
    m = make_memory_handlers(memory, journal)
    search = tool("web_search", spec.description, spec.input_schema)(make_search_handler(spec, journal))
    recall = tool("recall", "Search your saved notes and entries by keyword, for when the owner refers to something that is not in view.",
                  {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]})(m["recall"])
    app_tools = {}
    if apps is not None:
        h = apps.handlers()
        for name in (apps_mod.MAIL_SEND, apps_mod.MAIL_REPLY, apps_mod.CALENDAR_CREATE):
            short = name.rsplit("__", 1)[1]
            if gate is not None:
                h[short] = gate.guarded(name, h[short])   # lock 2
        app_tools = {n: tool(n, apps_mod.SCHEMAS[n][0], apps_mod.SCHEMAS[n][1])(h[n]) for n in apps_mod.SCHEMAS}
    if read_only:
        return create_sdk_mcp_server(name=SERVER, tools=[search, recall, *[app_tools[n] for n in ("mail_search", "mail_read", "calendar_list") if n in app_tools]])
    tools = [
        search,
        tool(
            "remember",
            "Save one lasting fact or preference about the owner, such as a dietary rule, a home city or how they like "
            "replies written. One fact per call, as a short sentence. If it changes an entry that is already saved, "
            "pass that entry's id in `replaces` so the old one is replaced and not kept alongside.",
            {"type": "object",
             "properties": {"text": {"type": "string", "description": "The fact, as one short sentence."},
                            "replaces": {"type": "string", "description": "Id of the saved entry this one replaces, e.g. a1b2."}},
             "required": ["text"]},
        )(m["remember"]),
        tool("forget", "Remove one saved entry by its id, when the owner says it is no longer true or asks you to forget it.",
             {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]})(m["forget"]),
        tool(
            "note",
            "Write a dated note about something that happened or is in progress, such as a decision made or a task left "
            "half done. Use `remember` instead for lasting facts about the owner.",
            {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        )(m["note"]),
        recall,
    ]
    if schedule is not None:
        s = make_schedule_handlers(schedule, journal)
        if gate is not None:
            s["schedule"] = gate.guarded(SCHEDULE_TOOL, s["schedule"])   # lock 2: runs only what was approved
        tools += [
            tool(
                "schedule",
                "Schedule something for later. kind=reminder sends the owner a fixed message at that time, exactly as "
                "written in `text`; use it for 'remind me to ...'. kind=routine runs you at that time with nobody "
                "watching, able only to read (the web, your memory, and the owner's mail and calendar if those are connected), and sends the owner what you produce; `text` is "
                "then the instructions for that run, written so they make sense with no other context. If a routine "
                "should speak only when something is true, say so in its instructions. Work out `when` from the time "
                "stamped on the owner's message.",
                {"type": "object",
                 "properties": {
                     "kind": {"type": "string", "enum": ["reminder", "routine"]},
                     "text": {"type": "string", "description": "The reminder's message, or the routine's instructions."},
                     "when": {"type": "string", "description": "Local date and time of the first run, like 2026-11-18T09:00. No zone."},
                     "repeat": {"type": "string", "enum": list(REPEATS), "description": "Default once."}},
                 "required": ["kind", "text", "when"]},
            )(s["schedule"]),
            tool("list_schedule", "List everything currently scheduled, with ids.", {"type": "object", "properties": {}})(s["list_schedule"]),
            tool("cancel_schedule", "Cancel one scheduled job by its id.",
                 {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]})(s["cancel_schedule"]),
        ]
    tools += list(app_tools.values())
    if anyapp is not None:
        h = anyapp.handlers()
        if gate is not None:
            h["app_run"] = gate.guarded(anyapp_mod.APP_RUN, h["app_run"])   # lock 2
        tools += [tool(n, anyapp_mod.SCHEMAS[n][0], anyapp_mod.SCHEMAS[n][1])(h[n]) for n in anyapp_mod.SCHEMAS]
    if browser is not None:
        handler = gate.guarded(browser_mod.BROWSE_TOOL, browser.browse) if gate is not None else browser.browse   # lock 2
        tools.append(tool("browse", browser_mod.SCHEMA[0], browser_mod.SCHEMA[1])(handler))
    return create_sdk_mcp_server(name=SERVER, tools=tools)
