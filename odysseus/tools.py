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
from . import attio as attio_mod
from .anyapp import AnyApp
from . import browser as browser_mod
from .browser import Browser
from .gate import ALLOW, ASK, Gate
from .journal import Journal
from .memory import MEMORY_NOTE, MemoryError_, MemoryStore
from .schedule import REPEATS, ScheduleError, ScheduleStore, parse_when
from .search import SERVER, SearchSpec, make_search_handler
from .taint import Taint
from .watch import WatchError, WatchStore, describe_watch


def _name(n: str) -> str:
    return f"mcp__{SERVER}__{n}"


SEARCH_TOOL = _name("web_search")
MEMORY_TOOLS = tuple(_name(n) for n in ("remember", "forget", "note", "recall"))
SCHEDULE_TOOLS = tuple(_name(n) for n in ("schedule", "list_schedule", "cancel_schedule"))
ALLOWED_TOOLS = frozenset({SEARCH_TOOL, *MEMORY_TOOLS, *SCHEDULE_TOOLS})
ROUTINE_TOOLS = frozenset({SEARCH_TOOL, _name("recall")})
SCHEDULE_TOOL = _name("schedule")
WATCH_TOOL = _name("watch")


def _after_reading(taint: Taint, text: str) -> list[str]:
    """Why a write to Odysseus's own memory or schedule should be put to the owner: what was read in this task that
    other people wrote, plus anything in the text itself that is recognised as coming from the owner's accounts."""
    why = sorted(taint.sources)
    if not why and taint.leaked(text):
        why = ["your mail, calendar or apps (earlier in this conversation)"]
    return why


def _list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def describe_memory(args: dict[str, Any], taint: Taint, what: str = "Save this to what I remember about you?") -> str:
    text = " ".join(str(args.get("text", "")).split())
    if not text:
        raise MemoryError_("There is nothing to save.")
    replaces = f"\n\nIt replaces entry [{args['replaces']}]." if args.get("replaces") else ""
    return (f"{what}\n\n{text}{replaces}\n\nI am asking because during this task I read {_list(_after_reading(taint, text))}, "
            "which other people wrote. What I remember is shown to me in every later conversation.")


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


def describe_search(args: dict[str, Any], taint: Taint) -> str:
    found = taint.leaked(f"{args.get('query', '')} {args.get('objective', '')}")
    return ("Search the web with this? It contains details that came from your mail, calendar or apps, and a search is "
            f"sent to a search company.\n\nDetails recognised: {', '.join(found)}\n\nQuery: {str(args.get('query', '')).strip()}\n"
            f"Purpose: {str(args.get('objective', '')).strip()}")


def make_gate(journal: Journal, now=None, timeout_s: float = 600.0, with_apps: bool = False, with_browser: bool = False,
              browser_ask_signed_in: bool = True, anyapp: AnyApp | None = None, apps: Apps | None = None,
              with_watches: bool = False, taint_file: Any = None, browser: Browser | None = None, attio: Any = None,
              services: Any = ()) -> Gate:
    """The gate for conversations, with a rule for every tool that exists.

    Pass `apps` (the object, not just with_apps) so that the calendar rules can see which events have guests,
    and so that what is read from the accounts is remembered for the check on what leaves through search.
    `taint_file` is where that record is kept between runs of the program.
    """
    gate = Gate(journal, timeout_s)
    taint = gate.taint = Taint(taint_file, on_full=lambda: journal.write("taint", outcome="full", note="oldest fingerprints are now being dropped"))
    with_apps = with_apps or apps is not None
    if apps is not None:
        apps.taint = taint
    if browser is not None:
        browser.taint = taint
    if with_browser or browser is not None:
        browser_mod.add_rules(gate, browser_ask_signed_in, taint)
    if anyapp is not None:
        anyapp.taint = taint
        anyapp.add_rules(gate)
    if attio is not None:
        attio.taint = taint
        attio.add_rules(gate)
    for service in services:        # any other service with a server of its own: mcpapp.py
        service.taint = taint
        service.add_rules(gate)
    if with_apps:
        from datetime import datetime
        apps_mod.add_rules(gate, now or datetime.now, apps)
    for name in (_name("forget"), _name("recall"), _name("list_schedule"), _name("cancel_schedule")):
        gate.rule(name, ALLOW)
    # Saving to memory writes only to Odysseus's own files, so it needs no question when the task has read nothing
    # but the owner's own words. Once mail, a web page, a file or anything else other people wrote has been read in
    # the task, the model may be repeating what it read, and memory is shown in every later conversation: the owner is asked.
    gate.rule(_name("remember"), lambda a: ASK if _after_reading(taint, str(a.get("text", ""))) else ALLOW,
              lambda a: describe_memory(a, taint))
    gate.rule(_name("note"), lambda a: ASK if _after_reading(taint, str(a.get("text", ""))) else ALLOW,
              lambda a: describe_memory(a, taint, "Write this in my dated notes?"))
    # A search is allowed unless it carries details read from the owner's accounts (taint.py); then the owner is asked.
    gate.rule(SEARCH_TOOL, lambda a: ASK if taint.leaked(f"{a.get('query', '')} {a.get('objective', '')}") else ALLOW,
              lambda a: describe_search(a, taint))

    def schedule_decision(a: dict[str, Any]) -> str:
        # A routine commits the owner to repeated unattended runs that cost money: always asked. A reminder is a fixed
        # message to the owner and to nobody else: allowed, and reported by code in the same reply. For one day it was
        # asked about once the task had read anything other people wrote, as memory still is. The owner withdrew that:
        # most reminders follow a look at the calendar, and unlike memory a reminder is not shown to the model again.
        return ASK if str(a.get("kind", "")).strip().lower() == "routine" else ALLOW

    def schedule_question(a: dict[str, Any]) -> str:
        return describe_routine(a, now, with_apps)
    gate.rule(SCHEDULE_TOOL, schedule_decision, schedule_question)
    if with_watches:
        # A watch commits the owner to unattended, paid checks of everything that arrives, so the owner is asked once.
        gate.rule(WATCH_TOOL, ASK, describe_watch)
        gate.rule(_name("list_watches"), ALLOW)
        gate.rule(_name("cancel_watch"), ALLOW)
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
                     browser: Browser | None = None, anyapp: AnyApp | None = None, watches: WatchStore | None = None,
                     on_watch: Any = None, attio: Any = None, services: Any = ()):
    """The full server for conversations, or (read_only) the small one for unattended routines."""
    m = make_memory_handlers(memory, journal)
    search_handler = make_search_handler(spec, journal)
    if gate is not None:
        taint = getattr(gate, "taint", None)
        if taint is not None:
            found = search_handler

            async def search_handler(args: dict[str, Any]) -> dict[str, Any]:
                taint.saw("web search results")       # from here on in this task, the model has read what strangers wrote
                return await found(args)

            def with_source(name: str, handler):
                async def wrapped(args: dict[str, Any]) -> dict[str, Any]:
                    if taint.sources:       # where a saved fact may have come from is kept in the log
                        journal.write("provenance", name=name, after_reading=sorted(taint.sources))
                    return await handler(args)
                return wrapped
            m["remember"], m["note"] = with_source("remember", m["remember"]), with_source("note", m["note"])
        m["remember"] = gate.guarded(_name("remember"), m["remember"])      # lock 2
        m["note"] = gate.guarded(_name("note"), m["note"])
        search_handler = gate.guarded(SEARCH_TOOL, search_handler)     # lock 2, for a search that carries private details
    elif apps is not None:
        # An unattended routine has nobody to ask, so a search carrying details from the accounts is simply refused.
        plain = search_handler

        async def search_handler(args: dict[str, Any]) -> dict[str, Any]:
            if apps.taint is not None and apps.taint.leaked(f"{args.get('query', '')} {args.get('objective', '')}"):
                journal.write("tool", name="web_search", ok=False, error="carries details from the accounts")
                return _err("Not searched: the query contains details from the owner's mail or calendar, and nobody is here to approve sending them.")
            return await plain(args)
    search = tool("web_search", spec.description, spec.input_schema)(search_handler)
    recall = tool("recall", "Search your saved notes and entries by keyword, for when the owner refers to something that is not in view.",
                  {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]})(m["recall"])
    app_tools = {}
    if apps is not None:
        h = apps.handlers()
        for name in apps_mod.GUARDED:
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
    if watches is not None:
        async def watch(args: dict[str, Any]) -> dict[str, Any]:
            try:
                w = watches.add(args.get("text", ""), args.get("about", ""), args.get("repeat") or "once")
            except WatchError as e:
                journal.write("tool", name="watch", ok=False, error=str(e))
                return _err(str(e))
            journal.write("tool", name="watch", ok=True, id=w.id, about=w.about, repeat=w.repeat)
            if on_watch is not None:
                try:
                    await on_watch()          # ask the connector to start sending the events this watch needs
                except Exception as e:
                    journal.write("error", where="watch.activate", error=type(e).__name__, message=str(e)[:200])
                    return _ok("Watching: " + w.describe() + " But the connector could not be told to send events, so it may not fire. Tell the owner.")
            return _ok("Watching: " + w.describe())

        async def list_watches(args: dict[str, Any]) -> dict[str, Any]:
            journal.write("tool", name="list_watches", ok=True)
            return _ok(watches.listing())

        async def cancel_watch(args: dict[str, Any]) -> dict[str, Any]:
            w = watches.cancel(str(args.get("id", "")))
            journal.write("tool", name="cancel_watch", ok=w is not None, id=args.get("id"))
            return _ok("Cancelled: " + w.describe()) if w else _err(f"There is no watch with id {args.get('id')}.")

        if gate is not None:
            watch = gate.guarded(WATCH_TOOL, watch)       # lock 2
        tools += [
            tool("watch",
                 "Ask to be told when something happens in the owner's mail or calendar, such as a reply from a named person arriving or a "
                 "meeting being moved. `text` says what to tell the owner about, written to make sense with no other context. The owner must "
                 "approve. Use `schedule` instead for anything that should happen at a time.",
                 {"type": "object",
                  "properties": {"text": {"type": "string", "description": "What to tell the owner about, e.g. 'Priya Nair replies about lunch on Friday'."},
                                 "about": {"type": "string", "enum": ["mail", "calendar"]},
                                 "repeat": {"type": "string", "enum": ["once", "every"], "description": "Default once: tell the owner the first time, then stop."}},
                  "required": ["text", "about"]})(watch),
            tool("list_watches", "List what is currently being watched for, with ids.", {"type": "object", "properties": {}})(list_watches),
            tool("cancel_watch", "Stop watching for one thing, by its id.",
                 {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]})(cancel_watch),
        ]
    if anyapp is not None:
        h = anyapp.handlers()
        if gate is not None:
            h["app_run"] = gate.guarded(anyapp_mod.APP_RUN, h["app_run"])   # lock 2
        tools += [tool(n, anyapp_mod.SCHEMAS[n][0], anyapp_mod.SCHEMAS[n][1])(h[n]) for n in anyapp_mod.SCHEMAS]
    for service in services:
        h, schemas = service.handlers(), service.schemas()
        short = f"{service.slug}_run"
        if gate is not None:
            h[short] = gate.guarded(service.run_tool, h[short])   # lock 2
        tools += [tool(n, schemas[n][0], schemas[n][1])(h[n]) for n in schemas]
    if attio is not None:
        h = attio.handlers()
        if gate is not None:
            for short, full in attio_mod.GUARDED.items():
                h[short] = gate.guarded(full, h[short])   # lock 2
        tools += [tool(n, attio_mod.SCHEMAS[n][0], attio_mod.SCHEMAS[n][1])(h[n]) for n in attio_mod.SCHEMAS]
    if browser is not None:
        handler = gate.guarded(browser_mod.BROWSE_TOOL, browser.browse) if gate is not None else browser.browse   # lock 2
        tools.append(tool("browse", browser_mod.SCHEMA[0], browser_mod.SCHEMA[1])(handler))
    return create_sdk_mcp_server(name=SERVER, tools=tools)
