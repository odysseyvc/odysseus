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

import json

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
from . import history as history_mod
from .journal import Journal
from .memory import MEMORY_NOTE, MemoryError_, MemoryStore
from .schedule import REPEATS, ScheduleError, ScheduleStore, parse_when
from .search import SERVER, SearchSpec, make_search_handler
from .taint import Taint
from .watch import WatchError, WatchStore, describe_watch
from . import checkins as checkins_mod


def _name(n: str) -> str:
    return f"mcp__{SERVER}__{n}"


SEARCH_TOOL = _name("web_search")
MEMORY_TOOLS = tuple(_name(n) for n in ("remember", "forget", "note", "recall"))
SCHEDULE_TOOLS = tuple(_name(n) for n in ("schedule", "list_schedule", "cancel_schedule"))
ALLOWED_TOOLS = frozenset({SEARCH_TOOL, *MEMORY_TOOLS, *SCHEDULE_TOOLS})
ROUTINE_TOOLS = frozenset({SEARCH_TOOL, _name("recall")})
SCHEDULE_TOOL = _name("schedule")
WATCH_TOOL = _name("watch")
CHECKIN_ADD, CHECKIN_DONE = _name("checkin_add"), _name("checkin_done")
LEADS_TOOL = _name("who_to_ask_about")


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


def describe_routine(args: dict[str, Any], now=None, with_apps: bool = False, reads: Any = ()) -> str:
    """The question shown to the owner. Built from the tool's real inputs; the model does not write it.

    Whatever would make the schedule refuse the request is checked here, before the owner is asked: an approval that
    is then refused wastes the owner's attention and teaches them that approving does nothing.
    """
    from datetime import datetime
    from .schedule import MAX_TEXT
    length = len(" ".join(str(args.get("text", "")).split()))
    if not length:
        raise ScheduleError("There is nothing to schedule.")
    if length > MAX_TEXT:
        raise ScheduleError(f"Too long: {length} characters, and the limit is {MAX_TEXT}. Shorten the instructions and ask once.")
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
            + ("".join(f", {r}" for r in reads))
            + "), and sends you the result.\n\n"
            f"Instructions it will follow:\n{text}")


def routine_tools(with_apps: bool, services: Any = (), attio: Any = None) -> frozenset[str]:
    """What an unattended routine may use: reading only, including the owner's mail and calendar once connected, and the
    reading tools of any other connected service."""
    extra = {f"mcp__odysseus__{s.slug}_{t}" for s in services for t in ("find", "run")}
    if attio is not None:
        extra |= {attio_mod.ATTIO_FIND, attio_mod.ATTIO_RUN}
    return ROUTINE_TOOLS | (frozenset(apps_mod.READ_TOOLS) if with_apps else frozenset()) | frozenset(extra)


def _sent(args: dict[str, Any]) -> str:
    """Everything of a search that goes to the search company."""
    return f"{args.get('query', '')} {args.get('objective', '')}"


def describe_search(args: dict[str, Any], taint: Taint) -> str:
    found = taint.leaked(_sent(args))
    shown = f"Query: {str(args.get('query', '')).strip()}\nPurpose: {str(args.get('objective', '')).strip()}"
    if found:
        return ("Search the web with this? It contains details that came from your mail, calendar or apps, and a search is "
                f"sent to a search company.\n\nDetails recognised: {', '.join(found)}\n\n{shown}")
    return ("Search the web with this? I have read your mail, calendar or apps, a search is sent to a search company, and "
            f"{taint.spill(_sent(args)) or 'this one is more than I send unasked'}.\n\n{shown}")


def empty_server() -> Any:
    """A tool server with nothing in it, for an agent that must be able to do nothing but write (pages.py)."""
    return create_sdk_mcp_server(name=SERVER, tools=[])


def make_gate(journal: Journal, now=None, timeout_s: float = 600.0, with_apps: bool = False, with_browser: bool = False,
              browser_ask_signed_in: bool = True, anyapp: AnyApp | None = None, apps: Apps | None = None,
              with_watches: bool = False, taint_file: Any = None, browser: Browser | None = None, attio: Any = None,
              services: Any = (), auto_small: bool = False, escalate: Any = None) -> Gate:
    """The gate for conversations, with a rule for every tool that exists.

    `auto_small` is the owner's choice (19 November 2026) to be asked as little as possible: small changes that can be
    put back run at once and are listed in the reply. That covers additions to Attio, additions and edits in the
    owner's own Google files, and a routine or watch set up in a task that has read nothing other people wrote.
    Sending, sharing, inviting, deleting, overwriting a value in Attio and a signed-in browser run still ask.

    Pass `apps` (the object, not just with_apps) so that the calendar rules can see which events have guests,
    and so that what is read from the accounts is remembered for the check on what leaves through search.
    `taint_file` is where that record is kept between runs of the program.
    """
    gate = Gate(journal, timeout_s)
    gate.now = now
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
        anyapp.own_apps = anyapp_mod.OWN_APPS if auto_small else frozenset()
        anyapp.add_rules(gate)
    if attio is not None:
        attio.taint = taint
        attio.auto = auto_small
        attio.add_rules(gate)
    for service in services:        # any other service with a server of its own: mcpapp.py
        service.taint = taint
        service.add_rules(gate)
    if escalate is not None:
        def changes_after_reading(tool: str, a: dict[str, Any]) -> bool:
            """A change to the owner's records or files that the rules let run unasked, in a task that has read what other
            people wrote, or that itself carries details recognised from their accounts."""
            if tool == attio_mod.ATTIO_RUN:
                change = attio is not None and not attio.reads(a)
            elif tool == attio_mod.GUARDED["attio_file"]:
                change = True
            elif tool == anyapp_mod.APP_RUN:
                change = anyapp is not None and anyapp.kind(str(a.get("tool", "")).strip().upper()) != "read"
            else:
                return False
            return change and bool(taint.sources or taint.leaked(json.dumps(a, ensure_ascii=False, default=str)))
        gate.set_escalator(escalate, changes_after_reading)
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
    # A search is allowed unless it carries details recognised from the owner's accounts, or more that is not the owner's
    # own words than may leave unasked (taint.py); then the owner is asked.
    gate.rule(SEARCH_TOOL, lambda a: ASK if taint.leaked(_sent(a)) or taint.spill(_sent(a)) else ALLOW,
              lambda a: describe_search(a, taint))

    def schedule_decision(a: dict[str, Any]) -> str:
        # A routine commits the owner to repeated unattended runs that cost money: always asked. A reminder is a fixed
        # message to the owner and to nobody else: allowed, and reported by code in the same reply. For one day it was
        # asked about once the task had read anything other people wrote, as memory still is. The owner withdrew that:
        # most reminders follow a look at the calendar, and unlike memory a reminder is not shown to the model again.
        # Since 19 November 2026, with auto_small: a routine set up from the owner's own words alone is not asked about
        # either. It can only read, it is cancelled with one command, the day's budget bounds it, and code reports it in
        # the reply. Once the task has read what other people wrote, the instructions may be theirs: the owner is asked.
        if str(a.get("kind", "")).strip().lower() != "routine":
            return ALLOW
        return ALLOW if auto_small and not _after_reading(taint, str(a.get("text", ""))) else ASK

    def schedule_question(a: dict[str, Any]) -> str:
        return describe_routine(a, now, with_apps, [s.title for s in services] + (["Attio"] if attio is not None else []))
    gate.rule(SCHEDULE_TOOL, schedule_decision, schedule_question)
    # People to keep in touch with (checkins.py). An entry that is the owner's own job only ever sends the owner a
    # message, as a reminder does: allowed. One that is a colleague's job sends that colleague email from the owner's
    # mailbox for as long as it stays on the list: asked once, with the email itself in the question.
    def checkin_question(a: dict[str, Any]) -> str:
        from datetime import datetime
        try:
            return checkins_mod.describe_add(a, (now or datetime.now)().date())
        except checkins_mod.CheckinError as e:
            return f"Set up a check-in that emails {a.get('assignee_email')}?\n\n(As written it will be refused: {e})"
    gate.rule(CHECKIN_ADD, lambda a: ASK if str(a.get("assignee_email") or "").strip() else ALLOW, checkin_question)
    gate.rule(CHECKIN_DONE, lambda a: ASK if _after_reading(taint, str(a.get("result", ""))) else ALLOW,
              lambda a: describe_memory({"text": a.get("result", "")}, taint, f"Record this against the check-in with {a.get('who', '')}?"))
    # Who to ask a contact about (leads.py): a web search for a name and a company, by an agent that sees nothing else.
    # Like any search, it is asked about if what would be searched for carries details read from the owner's accounts.
    gate.rule(LEADS_TOOL, lambda a: ASK if taint.leaked(f"{a.get('name', '')} {a.get('company', '')}") else ALLOW,
              lambda a: describe_search({"query": f"{a.get('name', '')} {a.get('company', '')}".strip(),
                                         "objective": "who this person knows that the firm would want to meet"}, taint))
    gate.rule(_name("checkin_list"), ALLOW)
    # The owner's own files, found in the owner's own index and sent to the owner's own chat (files.py): nothing leaves.
    gate.rule(_name("files_find"), ALLOW)
    # The assistant's own inbox (mailbox.py): reading only. What is read is recorded as other people's words.
    for n in ("inbox_list", "inbox_read", "inbox_attachment"):
        gate.rule(_name(n), ALLOW)
    # Sending from that address is always put to the owner, with the whole message in the question. No setting widens it.
    from .mailbox import INBOX_SEND, describe_own_send
    gate.rule(INBOX_SEND, ASK, describe_own_send)
    # Code run on a throwaway machine with no internet (sandbox.py): it can reach nothing of the owner's but the kept
    # files it is handed, and can send them nowhere. What it makes goes to the owner's own chat. So it runs unasked.
    gate.rule(_name("run_code"), ALLOW)
    # Common free times with colleagues (peers.py): code on both sides, busy blocks only, and only the intersection comes back.
    gate.rule(_name("times_find"), ALLOW)
    # A question to a colleague's assistant (peers.py) leaves the owner's side. Like a search, it goes unasked unless it
    # carries details recognised from the owner's accounts, or more that is not the owner's own words than may leave unasked.
    def ask_question(a: dict[str, Any]) -> str:
        found, q = taint.leaked(str(a.get("question", ""))), " ".join(str(a.get("question", "")).split())
        return (f"Put this question to {str(a.get('person', '')).strip()}'s assistant? "
                + (f"It contains details that came from your mail, calendar or apps: {', '.join(found)}." if found
                   else f"I have read your mail, calendar or apps, and {taint.spill(q) or 'this is more than I send unasked'}.")
                + f"\n\n{q}\n\nThey will see it exactly as shown.")
    gate.rule(_name("colleague_ask"), lambda a: ASK if taint.leaked(str(a.get("question", ""))) or taint.spill(str(a.get("question", ""))) else ALLOW,
              ask_question)
    # A file to a colleague's assistant (peers.py) leaves the owner's side for good: asked every time, by the file's name.
    # The assistant's start-up gives this rule the kept files, so the question can name the file (__main__.py).
    from .peers import COLLEAGUE_FILE, describe_file
    gate.rule(COLLEAGUE_FILE, ASK, describe_file)
    gate.rule(_name("files_send"), ALLOW)
    gate.rule(_name("checkin_remove"), ALLOW)
    if with_watches:
        # A watch commits the owner to unattended, paid checks of everything that arrives, so the owner is asked once.
        gate.rule(WATCH_TOOL, lambda a: ALLOW if auto_small and not _after_reading(taint, str(a.get("text", ""))) else ASK, describe_watch)
        gate.rule(_name("list_watches"), ALLOW)
        gate.rule(_name("cancel_watch"), ALLOW)
    return gate


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def make_memory_handlers(memory: MemoryStore, journal: Journal, history: Any = None, taint: Any = None) -> dict[str, Any]:
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
        # Earlier conversations are searched too, by plain word matching (history.py). What comes back from there is not
        # memory: replies may quote what other people wrote, so it is marked as such and recorded as outside material.
        said = history.search(str(args.get("query", ""))) if history is not None else []
        journal.write("tool", name="recall", ok=True, results=len(hits), **({"earlier": len(said)} if history is not None else {}))
        if not hits and not said:
            return _ok("Nothing in memory or in earlier conversations matches.")
        parts = [MEMORY_NOTE + "\n\n" + "\n".join(f"({source}) {body}" for source, body in hits)] if hits else []
        if said:
            if taint is not None:
                taint.saw("earlier conversations")
            parts.append(history_mod.NOTE + "\n\n" + history_mod.show(said))
        return _ok("\n\n".join(parts))

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
                     on_watch: Any = None, attio: Any = None, services: Any = (), mail_only: bool = False, history: Any = None,
                     checkins: Any = None, leads: Any = None, files: Any = None, send_file: Any = None, inbox: Any = None, code: Any = None, peers: Any = None):
    """The full server for conversations, or (read_only) the small one for unattended routines."""
    # Earlier conversations are searched only in a conversation: an unattended run's recall reads the saved notes alone.
    m = make_memory_handlers(memory, journal, None if read_only else history, getattr(gate, "taint", None))
    search_handler = make_search_handler(spec, journal)
    if gate is not None:
        taint = getattr(gate, "taint", None)
        if taint is not None:
            found = search_handler

            async def search_handler(args: dict[str, Any]) -> dict[str, Any]:
                if not taint.leaked(_sent(args)) and not taint.spill(_sent(args)):
                    taint.spent(_sent(args))          # it is leaving unasked: counted against the hour's allowance
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
    elif apps is not None or attio is not None or services:
        # An unattended routine has nobody to ask, so a search carrying details from the accounts is simply refused.
        # Whatever the routine can read (mail, the firm's database, another service) shares one record of what was read.
        plain = search_handler
        readers = [r for r in (apps, attio, *services) if r is not None]

        async def search_handler(args: dict[str, Any]) -> dict[str, Any]:
            taint = next((r.taint for r in readers if getattr(r, "taint", None) is not None), None)
            if taint is not None and taint.leaked(_sent(args)):
                journal.write("tool", name="web_search", ok=False, error="carries details from the accounts")
                return _err("Not searched: the query contains details from the owner's mail, calendar or other accounts, and nobody is here "
                            "to approve sending them.")
            if taint is not None and taint.spill(_sent(args)):
                journal.write("tool", name="web_search", ok=False, error="over the allowance for unasked searches")
                return _err("Not searched: this search, or the searches of the last hour, carry more than may be sent without the owner "
                            "seeing it, and nobody is here to approve it. Search with fewer words, or leave it for the owner.")
            if taint is not None:
                taint.spent(_sent(args))
            return await plain(args)
    search = tool("web_search", spec.description, spec.input_schema)(search_handler)
    recall = tool("recall", "Search your saved notes and entries, and what was said in earlier conversations, by keyword, for when the owner "
                  "refers to something that is not in view.",
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
        from .mcpapp import unattended
        reading = []
        for service in services:            # another connected service: its reads only, checked by code (mcpapp.unattended)
            h, schemas = service.handlers(), service.schemas()
            h[f"{service.slug}_run"] = unattended(service, h[f"{service.slug}_run"], journal)
            reading += [tool(n, schemas[n][0], schemas[n][1])(h[n]) for n in schemas]
        if attio is not None:               # the firm's database, to read; filing is never offered to a routine
            h = attio.handlers()
            h["attio_run"] = unattended(attio, h["attio_run"], journal)
            reading += [tool(n, attio_mod.SCHEMAS[n][0], attio_mod.SCHEMAS[n][1])(h[n]) for n in ("attio_find", "attio_run")]
        mail = [app_tools[n] for n in ("mail_search", "mail_read", "calendar_list") if n in app_tools]
        if mail_only:       # the agent that judges incoming mail: no search to carry anything out, no memory to write to
            return create_sdk_mcp_server(name=SERVER, tools=mail)
        return create_sdk_mcp_server(name=SERVER, tools=[search, recall, *mail, *reading])
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
                "watching, able only to read (the web, your memory, and whichever of the owner's mail, calendar, Attio and other services are connected), and sends the owner what you produce; `text` is "
                "then the instructions for that run, written so they make sense with no other context, in at most 600 characters. If a routine "
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
    if checkins is not None:
        async def checkin_add(args: dict[str, Any]) -> dict[str, Any]:
            try:
                item = checkins.add(args)
            except checkins_mod.CheckinError as e:
                journal.write("tool", name="checkin_add", ok=False, error=str(e))
                return _err(str(e))
            journal.write("tool", name="checkin_add", ok=True, id=item["id"], every=item["every"], colleague=bool(item["assignee_email"]))
            return _ok("On the list: " + checkins.line(item))

        async def checkin_list(args: dict[str, Any]) -> dict[str, Any]:
            journal.write("tool", name="checkin_list", ok=True)
            return _ok(checkins.listing())

        async def checkin_remove(args: dict[str, Any]) -> dict[str, Any]:
            item = checkins.remove(str(args.get("who", "")))
            journal.write("tool", name="checkin_remove", ok=item is not None)
            return _ok(f"Removed the check-in with {item['name']}.") if item else _err("No one entry on the list fits that. Use checkin_list to see the ids.")

        async def checkin_done(args: dict[str, Any]) -> dict[str, Any]:
            item = checkins.get(str(args.get("who", "")))
            if item is None:
                journal.write("tool", name="checkin_done", ok=False)
                return _err("No one entry on the list fits that. Use checkin_list to see the ids.")
            already = item.get("done") == checkins._today().isoformat()
            if already:
                checkins.result(item, str(args.get("result", "")), by="the owner")      # the owner tapped Done a moment ago: only what came of it is new
            else:
                checkins.done(item, str(args.get("result", "")), by="the owner")
            journal.write("tool", name="checkin_done", ok=True, id=item["id"], with_result=bool(str(args.get("result", "")).strip()))
            return _ok(f"Recorded. The next check-in with {item['name']} is due {item['due']}. If {item['name']} has a record in the firm's "
                       "database and something came of the conversation, add it there as a note on that record.")

        if gate is not None:
            checkin_add = gate.guarded(CHECKIN_ADD, checkin_add)       # lock 2
            checkin_done = gate.guarded(CHECKIN_DONE, checkin_done)
        tools += [
            tool("checkin_add",
                 "Put a person on the list of people to keep in touch with: someone the owner, or a named colleague, should speak to every so "
                 "often. Use it when the owner asks to be reminded to speak to someone regularly, or to have a colleague reminded until they "
                 "have done it. When it is due the owner gets a message with buttons; if it is a colleague's job (give assignee and "
                 "assignee_email), the colleague is emailed from the owner's mailbox every few days until they reply, their reply is recorded, "
                 "and the owner is told. The owner must approve a colleague's entry. Not for a one-off reminder: use schedule for that.",
                 {"type": "object",
                  "properties": {"name": {"type": "string", "description": "Who the check-in is with."},
                                 "email": {"type": "string", "description": "That person's own address, if known. Used to find their record."},
                                 "every_days": {"type": "integer", "description": "Days between check-ins, 7 to 365. Monthly is 30. Default 60."},
                                 "about": {"type": "string", "description": "The question to put to them, as the question itself, like: Who is the best founder you have met lately? Optional."},
                                 "assignee": {"type": "string", "description": "The colleague whose job it is. Leave out if it is the owner's."},
                                 "assignee_email": {"type": "string", "description": "That colleague's address. Leave out if it is the owner's job."},
                                 "first_due": {"type": "string", "description": "Date of the first one, like 2026-12-01. Default: one interval from today."}},
                  "required": ["name"]})(checkin_add),
            tool("checkin_list", "List the people to keep in touch with: ids, how often, whose job, when next, and what came of the last one.",
                 {"type": "object", "properties": {}})(checkin_list),
            tool("checkin_done",
                 "Record that a check-in happened, with what came of it in the owner's words. Use it when the owner says they have spoken to "
                 "someone on the list. The next one is then due a full interval from today.",
                 {"type": "object",
                  "properties": {"who": {"type": "string", "description": "The entry's id, or the person's name."},
                                 "result": {"type": "string", "description": "What came of it, as the owner told it. Empty if they did not say."}},
                  "required": ["who"]})(checkin_done),
            tool("checkin_remove", "Take a person off the list of people to keep in touch with, by id or name.",
                 {"type": "object", "properties": {"who": {"type": "string"}}, "required": ["who"]})(checkin_remove),
        ]
    if files is not None and send_file is not None:
        from . import files as files_mod

        async def files_find(args: dict[str, Any]) -> dict[str, Any]:
            found = files.find(str(args.get("query", "")), 5)
            journal.write("tool", name="files_find", ok=True, results=len(found))
            taint_ = getattr(gate, "taint", None)
            if found and taint_ is not None:
                taint_.saw("a file you sent")
                for e in found:
                    taint_.add(e.get("text", "")[:files_mod.SHOWN])
            if not found:
                return _ok("No kept file fits that. Kept files are the ones the owner sent you, and attachments and DocSend documents you have "
                           "read, since files began to be kept. If it may be an attachment in their mail that you have not read, say so and offer to look.")
            return _ok(files_mod.NOTE + "\n\n" + "\n".join(files.line(e, opening=True) for e in found)
                       + "\n\nIf one of these is plainly the file meant, send it with files_send. If more than one could be, ask the owner which.")

        async def files_send(args: dict[str, Any]) -> dict[str, Any]:
            entry = files.get(str(args.get("id", "")))
            if entry is None:
                journal.write("tool", name="files_send", ok=False, error="no such id")
                return _err("No kept file has that id. Use files_find to get the id.")
            problem = await send_file(entry)
            journal.write("tool", name="files_send", ok=not problem, id=entry["id"])
            return _err(problem) if problem else _ok(f"Sent {entry['name']} to the owner in this chat. Do not paste its contents; one line saying it is sent is enough.")
        tools += [
            tool("files_find",
                 "Search the files kept for the owner so that they can be handed back: decks, documents and pictures they sent you, attachments you "
                 "read in their mail, and documents you read from a DocSend link. "
                 "Use it when the owner asks for a file back, or asks which files you have. Search by the company, the file's name or what "
                 "it is about; an empty query lists the newest.",
                 {"type": "object", "properties": {"query": {"type": "string", "description": "A few words: a company, a name, a topic. Empty for the newest files."}}})(files_find),
            tool("files_send",
                 "Send one kept file back to the owner in this chat, by its id from files_find. It can only go to the owner. To send a file to "
                 "anyone else, say that this is not possible yet.",
                 {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]})(files_send),
        ]
    if inbox:
        from . import mailbox as mailbox_mod
        if gate is not None:
            inbox = {**inbox, "inbox_send": gate.guarded(mailbox_mod.INBOX_SEND, inbox["inbox_send"])}      # lock 2: only the message that was approved
        tools += [tool(n, mailbox_mod.SCHEMAS[n][0], mailbox_mod.SCHEMAS[n][1])(inbox[n]) for n in mailbox_mod.SCHEMAS]
    if code:
        from . import sandbox as sandbox_mod
        tools += [tool(n, sandbox_mod.SCHEMAS[n][0], sandbox_mod.SCHEMAS[n][1])(code[n]) for n in sandbox_mod.SCHEMAS]
    if peers:
        from . import peers as peers_mod
        if gate is not None and "colleague_file" in peers:
            peers = {**peers, "colleague_file": gate.guarded(peers_mod.COLLEAGUE_FILE, peers["colleague_file"])}     # lock 2: only the file that was approved
        tools += [tool(n, peers_mod.SCHEMAS[n][0], peers_mod.SCHEMAS[n][1])(peers[n]) for n in peers_mod.SCHEMAS if n in peers]
    if leads is not None:
        from . import leads as leads_mod

        async def who_to_ask_about(args: dict[str, Any]) -> dict[str, Any]:
            found = await leads.find(str(args.get("name", "")), str(args.get("company", "")), [str(x) for x in args.get("not_these") or []][:20])
            journal.write("tool", name="who_to_ask_about", ok=True, found=len(found))
            taint_ = getattr(gate, "taint", None)
            if taint_ is not None:
                taint_.saw("web search results")
            if not found:
                return _ok("Nobody was found with a public connection that can be pointed to, or the search could not be run. Say so plainly.")
            return _ok(leads_mod.MATERIAL + "\n\n" + leads_mod.lines(found) + "\n\nGive the owner these as people to ask about, with the page for "
                       "each, and say that a public connection is not proof they know each other. Offer to draft the message asking for an introduction.")
        if gate is not None:
            who_to_ask_about = gate.guarded(LEADS_TOOL, who_to_ask_about)      # lock 2
        tools.append(tool(
            "who_to_ask_about",
            "Find up to three people a named contact probably knows well whom the firm would want to meet, so the owner can ask the contact "
            "about them by name: 'do you know X, and would you introduce us?'. Use it when the owner asks who a founder or contact might "
            "know, who to ask them about, or who the smartest people around them are. It searches the public web for who the contact has "
            "built, worked, invested or appeared with; it takes a minute or two and leaves out people already in the firm's database. "
            "It does not read who follows whom on any site.",
            {"type": "object",
             "properties": {"name": {"type": "string", "description": "The contact's full name."},
                            "company": {"type": "string", "description": "Where they work or what they founded, to tell them from others of the same name."},
                            "not_these": {"type": "array", "items": {"type": "string"}, "description": "Names not to suggest, e.g. ones already given."}},
             "required": ["name"]})(who_to_ask_about))
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
