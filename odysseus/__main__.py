"""Start Odysseus:  python -m odysseus"""
from __future__ import annotations

import time

_BEGAN = time.monotonic()      # before the heavy imports below, so that start-up can be timed whole

import asyncio  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402

RESTART = 75      # the exit code by which the child asks to be started again


def supervise() -> int:
    """Run the program as a child process, and start it again whenever it ends asking to be restarted.

    Nothing in the program asks for this today: a newly connected service now rebuilds one person's assistant in
    place (people.py), where it once restarted the whole program. The exit code is kept for whatever next needs a
    clean process.
    """
    import subprocess
    env = {**os.environ, "ODYSSEUS_CHILD": "1"}
    while True:
        child = subprocess.Popen([sys.executable, "-W", "ignore", "-m", "odysseus"], env=env)
        try:
            code = child.wait()
        except KeyboardInterrupt:
            child.terminate()
            return 130
        if code != RESTART:
            return code


# The supervisor only starts the child and waits. It leaves here, before the libraries below are loaded: loading
# them took as long again as the child's own start.
if __name__ == "__main__" and not os.environ.get("ODYSSEUS_CHILD"):
    sys.exit(supervise())

from aiogram import Bot  # noqa: E402
from exa_py import Exa

from .agent import AgentRunner, conversation_prompt
from .anyapp import AnyApp
from .attio import Attio, FileTokens, McpBackend
from .connect import Connector
from .mcpapp import connected as connected_services
from .apps import Apps, ComposioBackend
from .browser import Browser, BrowserUseBackend
from .capture import MATERIAL as CAPTURE_MATERIAL
from .capture import Capture
from .closing import make_closer
from .checkins import CheckinLoop, Checkins
from .decisions import Decisions, PassWatch
from .door import Door
from .files import Shelf, deliver
from .mailbox import MATERIAL as INBOX_MATERIAL
from .mailbox import AgentMail, Mailbox
from .leads import Leads
from .linq import Linq
from .linq import attach as attach_linq
from .linq import make_fetch
from dataclasses import replace
from types import SimpleNamespace

from .config import ConfigError, Settings, imessage_on, load, make_clock, vobiz_on, whatsapp_group_on
from .conversation import Conversation
from .history import History
from .journal import Journal, Ledger
from .memory import MemoryStore
from .deckview import DeckViewer
from .links import Fetcher
from .ocr import MistralOcr
from .pages import make_page_reader
from .people import House, People
from .review import OUTGOING, anthropic_call, make_escalator, make_second_reader
from .routines import make_routine_runner, make_triage_runner
from .schedule import Scheduler, ScheduleStore
from .telegram import TelegramChat, build_dispatcher
from .tools import empty_server, make_gate, make_tool_server
from .tracing import Tracing
from .voice import MistralVoice
from .watch import ComposioTriggers, Watcher, WatchStore
from .whatsapp import WhatsApp, attach, make_http


SYNCED = ("onedrive", "dropbox", "google drive", "googledrive", "icloud", "mobile documents")


def synced_folder(path) -> str | None:
    """The name of the file-syncing service this path is inside, if any. The data folder holds mail text and the
    conversation in the clear; inside a synced folder it is also copied to that service."""
    full = str(path.resolve()).lower()
    return next((name for name in SYNCED if name in full), None)


# Work that ends with a "task_end" line of its own and is not the conversation's task: a scheduled routine, a meeting brief,
# a lookup of contacts. Each marks its line with one of these. A line so marked must not be taken for the end of the task
# the owner started. The lookup was once missing from this list, so a task cut off after one was never reported.
SIDE_WORK = ("routine", "brief", "leads")


def unfinished(journal: Journal) -> dict | None:
    """What was lost when the program last stopped, if anything: a task that had started and not ended, and any
    messages that were waiting behind it."""
    open_task, waiting, behind = None, {}, []
    for r in journal.read():
        if r["kind"] == "start":
            open_task, waiting, behind = None, {}, []       # an earlier restart: whatever was lost then has been reported already
        elif r["kind"] == "task_start" and r.get("background"):
            behind.append(r.get("preview", ""))     # a job in the background has its own beginning and end; there can be several
        elif r["kind"] == "task_end" and r.get("background"):
            if behind:
                behind.pop(0)                       # the lines carry no name for the job, so the one begun first is taken to have ended
        elif r["kind"] == "task_start":
            open_task = {"preview": r.get("preview", ""), "asked": False}
        elif r["kind"] == "task_end" and not any(k in r for k in SIDE_WORK):
            open_task = None
        elif r["kind"] == "queued" and "q" in r:
            waiting[r["q"]] = r.get("preview", "")
        elif r["kind"] == "dequeued":
            waiting.pop(r.get("q"), None)
        elif r["kind"] == "queue_cleared":
            waiting = {}
        elif r["kind"] == "gate" and r.get("verdict") == "asked" and open_task is not None:
            open_task["asked"] = True
        elif r["kind"] == "gate" and r.get("verdict") in ("approved", "declined", "expired", "cancelled", "unasked") and open_task is not None:
            open_task["asked"] = False
    if open_task is None and not waiting and not behind:
        return None
    return {**(open_task or {"preview": None, "asked": False}), "waiting": list(waiting.values()), **({"background": behind[-1]} if behind else {})}


LOST_CONVERSATION = ("I was restarted and could not pick up our earlier conversation, so this is a new one. "
                     "I still have what I remember about you; I do not have what we said before.")


def restart_notice(task: dict, resumed: bool) -> str:
    waiting = task.get("waiting") or []
    if task.get("preview") is None:
        text = "I was restarted."
    else:
        what = f': "{task["preview"]}"' if task.get("preview") else ""
        text = f"I was restarted while I was working on something{what}. It was not finished, and I have not carried on with it by myself."
        if task.get("asked"):
            text += " I had asked for your approval; that question no longer counts, so tapping it will do nothing."
    if waiting:
        n = len(waiting)
        text += (f" {n} message{'s' if n != 1 else ''} you sent {'were' if n != 1 else 'was'} waiting behind it and {'were' if n != 1 else 'was'} lost: "
                 + "; ".join(f'"{w}"' for w in waiting) + ".")
    if task.get("background") is not None:
        text += " A job I was doing in the background was also cut off" + (f': "{task["background"]}"' if task["background"] else "") + "."
    text += " Ask me again if you still want " + ("these" if waiting or task.get("background") is not None else "it") + " done."
    return text + ("" if resumed else " I also no longer have our earlier conversation, only what I remember about you.")


class Assistant:
    """Everything one person has: a conversation, its agents, its gate and budget, and the jobs that run for them unasked.
    The host has one. Each person the host invited has one of their own, built from a folder of their own (people.py)."""

    def __init__(self) -> None:
        self.conversation = self.journal = self.ledger = self.apps = self.notify = None
        self.tasks: list = []
        self.closers: list = []          # called in order when this assistant stops
        self.agents: list = []           # every agent this person has, each a process of its own while connected
        self.gate = None

    async def park(self) -> int:
        """Let go of every agent connection that is doing nothing, if the person is not in the middle of anything.
        How many were let go."""
        if self.conversation is None or self.conversation.busy or (self.gate is not None and self.gate.pending()):
            return 0
        return sum([await a.park() for a in self.agents])

    async def close(self) -> None:
        for t in self.tasks:
            t.cancel()
        if self.conversation is not None:
            await self.conversation.wait()
        for c in self.closers:
            try:
                out = c()
                if asyncio.iscoroutine(out):
                    await out
            except Exception as e:
                self.journal.write("error", where="assistant.close", error=type(e).__name__)


async def assemble(settings: Settings, shared: SimpleNamespace, host: bool, marks: dict | None = None) -> Assistant:
    """Build and start one person's assistant from their settings: `owner_id` is who they are and `data_dir` is their
    folder. `shared` holds what there is one of in the process: the bot, the tracing client, the keys' clients.

    The host's agents are connected at once, so the first message is not slow. An invited person's are connected by
    their first message: each connected agent is a process of its own, and most people are not talking at any moment.
    """
    def mark(name: str) -> None:
        if marks is not None:
            marks[name] = round(time.monotonic() - _BEGAN, 1)
    me = Assistant()
    bot, tracing, second_call, search = shared.bot, shared.tracing, shared.second_call, shared.search
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    now = make_clock(settings.tz)     # one clock for message stamps, memory dates and the schedule
    journal = me.journal = Journal(settings.data_dir / "journal.jsonl", echo=host)   # the host's summary lines also go to the machine's log
    ledger = me.ledger = Ledger(journal, tz=settings.tz)      # "today" ends at the person's midnight
    memory = MemoryStore(settings.data_dir / "memory", now=now)
    schedule = ScheduleStore(settings.data_dir / "schedule.db", now=now)

    # Mail and calendar exist only if a connector key is set. The owner's Telegram id names their connections.
    connector = ComposioBackend(settings.composio_api_key, f"odysseus-{settings.owner_id}") if settings.composio_api_key else None
    apps = me.apps = Apps(connector, journal, now, settings.tz) if connector else None
    anyapp = AnyApp(connector, journal) if connector else None      # every other app in the connector's catalogue
    # Attio, the firm's customer database, through Attio's own server. It exists once the owner has signed in (scripts/connect_attio.py).
    attio_tokens = FileTokens(settings.attio_file or settings.data_dir / "attio.json")
    attio = Attio(McpBackend(attio_tokens), journal) if attio_tokens.signed_in() else None
    services = connected_services(settings.data_dir, journal)       # other services with a server of their own, once signed in to
    mark("services")
    browser = Browser(BrowserUseBackend(settings.browser_use_api_key, settings.data_dir / "browser.json"), journal, settings.task_usd) \
        if settings.browser_use_api_key else None
    ocr = MistralOcr(settings.mistral_api_key) if settings.mistral_api_key else None
    voice = MistralVoice(settings.mistral_api_key, settings.voice_model) if settings.mistral_api_key else None     # same key: it also listens
    # Watches: being told when something happens in mail or calendar. They need the connector, which sends the events.
    watches = WatchStore(settings.data_dir / "watches.db", now=now) if connector else None
    box: dict = {}       # the watcher is built further down, once there is somewhere to send its messages

    async def on_watch():
        await asyncio.to_thread(box["watcher"].activate)
        await asyncio.to_thread(box["watcher"].start, asyncio.get_running_loop())
    gate = make_gate(journal, now, with_apps=apps is not None, browser=browser,
                     browser_ask_signed_in=settings.browser_ask_signed_in, anyapp=anyapp, apps=apps, with_watches=watches is not None,
                     taint_file=settings.data_dir / "taint.bin", attio=attio, services=services,
                     auto_small=not settings.ask_every_change,
                     escalate=None if settings.review_model.lower() == "off" else
                     make_escalator(journal, second_call, settings.review_model or settings.background_model))
    history = History(settings.data_dir / "history.jsonl")
    decisions = Decisions(settings.data_dir / "decisions.jsonl")       # the owner's verdict beside each take on a deck
    checkins = Checkins(settings.data_dir / "checkins.json", today=lambda: now().date())      # people to keep in touch with
    from .skills import SkillStore
    from .skills import PLAYBOOKS
    skills = SkillStore(settings.data_dir / "skills", now=now, builtin=PLAYBOOKS)       # ways of doing a task: the owner's, and those that come with the program (skills.py)
    from .portfolio import Portfolio
    portfolio = Portfolio(settings.data_dir / "portfolio.json", today=lambda: now().date())       # figures the firm's companies report (portfolio.py)
    shelf = Shelf(settings.data_dir / "files", now=now)       # what the owner sends is kept, to be handed back when asked for (files.py)
    own_chat = TelegramChat(bot, settings.owner_id)           # a kept file goes to this person's own chat and nowhere else
    # An email address of the assistant's own (mailbox.py), if there is a key. It is made when the person sends /address.
    late: dict = {}          # the conversation, once it exists: the mailbox and its tools are made before it
    inbox = inbox_tools = None
    if settings.agentmail_api_key:
        async def own_addresses() -> set:
            mine = set(settings.owner_emails) if host else set()
            if apps is not None:
                try:
                    mine.add(await apps.my_address())
                except Exception as e:
                    journal.write("error", where="mailbox.owner_address", error=type(e).__name__)
            return mine
        inbox = Mailbox(AgentMail(settings.agentmail_api_key), settings.data_dir / "mailbox.json", journal, own_addresses,
                        run=lambda prompt: late["c"].handle(settings.owner_id, prompt, own_chat, material=INBOX_MATERIAL, background=True),
                        offer=lambda question, prompt: late["c"].offer_task(own_chat, question, prompt, INBOX_MATERIAL),
                        tell=own_chat.send, label=f"odysseus-{settings.owner_id}")
    if apps is not None and settings.review_model.lower() != "off":
        # What reaches other people is read once more before the owner is asked. The reader can add a warning; it cannot approve.
        gate.set_second_reader(make_second_reader(journal, second_call,
                                                  settings.review_model or settings.background_model, gate.taint), OUTGOING)
    mark("gate")
    instructions = conversation_prompt(apps is not None, browser is not None, settings.browser_ask_signed_in,
                                       with_files=ocr is not None, with_anyapp=anyapp is not None,
                                       with_watches=watches is not None, with_attio=attio is not None) + "".join(s.prompt() for s in services)

    # Who to ask a contact about (leads.py): an agent of its own that can search the web and read nothing of the owner's.
    leads = leads_runner = None
    if settings.suggest_people:
        async def already_known(name: str) -> bool:
            if attio is None:
                return False
            from .attio_layout import parse
            from .attio_intake import _norm, _texts
            found = parse(await attio._call(attio._b.call("search-records", {"object": "people", "query": name, "limit": 5})))
            return any(isinstance(hit, dict) and _norm(name) in {_norm(t) for t in _texts(hit.get("attributes") or hit)}
                       for hit in found.get("results") or found.get("records") or [])
        # On the main model: in a trial on one public founder the small model named long-standing colleagues and the main one named
        # early founders the person had backed, which is the answer wanted, for about seven cents against under one.
        leads_runner = make_routine_runner(settings, journal, make_tool_server(search, journal, memory, read_only=True), memory, now, program_tasks=True,
                                           model=settings.model)
        leads = Leads(leads_runner, journal, ledger, settings.daily_usd, known=already_known, state_file=settings.data_dir / "leads.json")

    if inbox is not None:
        inbox_tools = inbox.handlers(lambda: late["c"].reader, Fetcher(), gate.taint,
                                     lambda data, name, mime, text, origin: late["c"]._keep(data, name, mime, len(data), "", text, source="mail", origin=origin))

    # Code, run on a rented machine that is thrown away after each run, if there is a key for one (sandbox.py).
    code_tools = None
    if settings.e2b_api_key:
        from .sandbox import E2B, CodeRunner
        code_runner = CodeRunner(E2B(settings.e2b_api_key, settings.sandbox_template), journal, ledger, settings.daily_usd, gate.taint)
        if settings.review_model.lower() != "off":
            # Before a document goes, its pages are read once more against what the owner asked, by a model that did not write it (pagecheck.py).
            from .pagecheck import make_page_checker
            code_runner.checker = make_page_checker(journal, settings.anthropic_api_key, settings.pages_check_model or settings.background_model)
            code_runner.request = lambda: gate.request
        code_tools = code_runner.handlers(shelf, lambda entry: deliver(shelf, entry, own_chat))

    # Asking a colleague's assistant (peers.py): this person's desk at the switchboard, and the tool that uses it.
    peer_tools = drafter = None
    board = getattr(shared, "board", None)
    if board is not None:
        from .peers import Desk, Trust
        from .peers import MATERIAL as PEER_MATERIAL
        from .routines import make_draft_runner
        # Drafting an answer to a colleague's question: an agent of its own that can read this person's records and
        # nothing else. It becomes a process only when a question arrives.
        drafter = make_draft_runner(settings, journal, make_tool_server(search, journal, memory, read_only=True, apps=apps, attio=attio,
                                                                         services=services),
                                    memory, now, with_apps=apps is not None, services=services, attio=attio)

        async def receive(text: str, private: str = "") -> None:
            # A colleague's answer, or word that none is coming. It goes to this person's chat as written by code around
            # the colleague's approved text, and the model is shown it later as other people's writing (record_delivery).
            if private:
                gate.taint.add(private, PEER_MATERIAL)
            await own_chat.send(text)
            late["c"].record_delivery(text)
        board.register(Desk(settings.owner_id, shared.name_of(settings.owner_id), Trust(settings.data_dir / "trust.json"), journal,
                            busy=apps.busy_between if apps is not None else None, tell=own_chat.send,
                            spent=ledger.spent_today, daily_usd=settings.daily_usd, receive=receive, draft=drafter, ask_owner=own_chat.ask,
                            files=shelf, hand_over=lambda entry: deliver(shelf, entry, own_chat)))
        from .gate import ASK as _ASK
        from .peers import COLLEAGUE_FILE, describe_file
        gate.rule(COLLEAGUE_FILE, _ASK, lambda a: describe_file(a, shelf))      # the question names the file
        peer_tools = board.handlers(settings.owner_id, now, settings.tz)
        me.closers.append(drafter.runner.close)

    # The weekly look at the pipeline (pipeline.py): code over the firm's database and the owner's mail. Also a tool.
    reviewer = None
    if attio is not None:
        from zoneinfo import ZoneInfo
        from .pipeline import Reviewer
        printer = None
        if settings.e2b_api_key and settings.sandbox_template:
            from .sandbox import E2B
            printer = E2B(settings.e2b_api_key, settings.sandbox_template)
        reviewer = Reviewer(attio, journal, now=now, state_file=settings.data_dir / "pipeline.json",
                            messages=apps.messages_for_code if apps is not None else None, zone=ZoneInfo(settings.tz) if settings.tz else None,
                            printer=printer, send_file=lambda name, data, caption: own_chat.send_file(name, data=data, caption=caption),
                            at=settings.pipeline_review_at)

    def tools_for_a_conversation():
        return make_tool_server(search, journal, memory, schedule, gate=gate, apps=apps, browser=browser, anyapp=anyapp, watches=watches,
                                on_watch=on_watch, attio=attio, services=services, history=history, checkins=checkins, leads=leads,
                                files=shelf, send_file=lambda entry: deliver(shelf, entry, own_chat), inbox=inbox_tools, code=code_tools,
                                peers=peer_tools, skills=skills, portfolio=portfolio, reviewer=reviewer)
    runner = AgentRunner(settings, journal, tools_for_a_conversation(), gate=gate, system_prompt=instructions, persist=True)
    # More agents with the same tools, the same instructions and the same gate, each for one job at a time in the background:
    # a deck sent by itself, a document read from a link, a filing approved from a question. They keep no conversation.
    workers = [AgentRunner(settings, journal, tools_for_a_conversation(), gate=gate, system_prompt=instructions) for _ in range(settings.background_jobs)]
    mark("runner")
    gate.taint.listeners.append(tracing.hide)      # what is read from mail, calendar and apps is kept out of the traces
    async def viewer_address() -> str:
        # The address given to a site that asks who is viewing: the one in the settings, or the connected mailbox's own.
        return settings.viewer_email or await apps.my_address()
    # A short PDF's pages are looked at in a run of their own, with no tools; only its notes reach the conversation.
    from .pages import make_counter
    pages = make_page_reader(settings, journal, empty_server(), model=settings.pages_model or None,
                             count=make_counter(settings.anthropic_api_key),      # counted first, free; see pages.py
                             room=lambda: min(settings.task_usd, settings.daily_usd - ledger.spent_today())) if ocr is not None else None
    mark("pages")
    conversation = me.conversation = Conversation(settings, journal, ledger, runner, memory, now=now, schedule=schedule, gate=gate, apps=apps,
                                browser=browser, ocr=ocr, anyapp=anyapp, tracing=tracing, voice=voice, watches=watches, attio=attio, services=services,
                                pages=pages, links=Fetcher() if ocr is not None else None, history=history, decisions=decisions, worker=workers, checkins=checkins,
                                closer=make_closer(journal, second_call, settings.background_model), files=shelf, skills=skills,
                                deckview=DeckViewer(browser._b, apps.messages_for_code, viewer_address, journal, max(settings.task_usd, 1.0))
                                if browser is not None and apps is not None else None)
    late["c"] = conversation
    # Reading notes, filed by code into the owner's website repository (booknotes.py). The host's alone.
    if host and settings.reading_repo and settings.reading_token:
        from . import booknotes
        conversation.reading = booknotes.Desk(booknotes.Shelf(booknotes.GitHubFiles(settings.reading_repo, settings.reading_token, settings.reading_branch),
                                                              journal, state=settings.data_dir / "reading.json"), gate, journal)
        mark("reading")
    # After every exchange, a line of labels and measurements in this person's own journal (insight.py): what was asked for
    # and how it went, never the words. It is what the host's /insights counts.
    from .insight import make_labeller
    conversation.labeller = make_labeller(journal, second_call, settings.background_model)
    conversation._inbox = inbox
    conversation._portfolio = portfolio
    if board is not None:
        conversation.alone = lambda: not board.colleagues(settings.owner_id)
    conversation.own_chat = own_chat        # where work offered before a restart reports, once approved
    if inbox is not None:
        me.tasks.append(asyncio.create_task(inbox.run_forever()))
    mark("conversation")
    if apps is not None:
        apps.reader, apps.fetcher = conversation.reader, Fetcher()      # so that a file attached to an email is read as any file is
        apps.keep = lambda data, name, mime, text, origin: conversation._keep(data, name, mime, len(data), "", text, source="mail", origin=origin)
    if attio is not None:
        attio.warm()        # read the workspace's layout now, so the first question about the firm does not wait for it

    # Optional: WhatsApp as a second channel. It needs a door (a port Meta can reach), which Telegram does not.
    whatsapp = None
    if host and settings.whatsapp_token and settings.whatsapp_phone_id and settings.whatsapp_app_secret and settings.owner_whatsapp:
        http, close_http = make_http(settings.whatsapp_token)
        whatsapp = WhatsApp(conversation, journal, http, phone_id=settings.whatsapp_phone_id, owner_number=settings.owner_whatsapp,
                            app_secret=settings.whatsapp_app_secret, verify_token=settings.whatsapp_verify_token, owner_id=settings.owner_id,
                            ids_file=settings.data_dir / "whatsapp.json", groups=lambda: getattr(shared, "groups", None))
    # Reminders and routines go to one place: Telegram, unless the owner chose WhatsApp and it is set up.
    telegram = TelegramChat(bot, settings.owner_id)
    # Optional: WhatsApp by a second route, through Vobiz and a number rented from it (vobiz.py). It cannot send
    # buttons, so its approval questions are asked and answered in words (typed.py).
    vobiz = None
    if host and vobiz_on(settings):
        from .vobiz import Vobiz
        from .vobiz import attach as attach_vobiz
        from .vobiz import make_http as vobiz_http
        via, close_via = vobiz_http(settings.vobiz_auth_id, settings.vobiz_auth_token)
        vobiz = Vobiz(conversation, journal, via, auth_id=settings.vobiz_auth_id, channel_id=settings.vobiz_channel_id,
                      number=settings.vobiz_number, owner_number=settings.owner_whatsapp, secret=settings.vobiz_secret,
                      owner_id=settings.owner_id, ids_file=settings.data_dir / "whatsapp.json")
        me.closers += [attach_vobiz(vobiz, shared.door), close_via]
    by_whatsapp = whatsapp or vobiz         # the direct route if it is set up, else the one through Vobiz
    on_whatsapp = by_whatsapp is not None and settings.notify_on == "whatsapp"
    notify = me.notify = by_whatsapp.chat() if on_whatsapp else telegram
    # Optional: iMessage as a third channel, through Linq (linq.py). It only answers: reminders stay where they are.
    # A question it cannot put as a poll is asked and answered in words (typed.py).
    imessage = None
    if host and imessage_on(settings):
        linq_http, close_linq = make_http(settings.linq_token)
        linq_fetch, close_fetch = make_fetch()
        imessage = Linq(conversation, journal, linq_http, number=settings.linq_number, owner_number=settings.owner_imessage,
                        secret=settings.linq_secret, owner_id=settings.owner_id, state_file=settings.data_dir / "linq.json",
                        fetch=linq_fetch)
        me.closers += [attach_linq(imessage, shared.door), close_linq, close_fetch]

    # Routines run on their own agent, which can only read. In a private chat the chat id is the user's id.
    reads_too = (("pipeline_review",) if reviewer is not None else ()) + ("portfolio_show",)
    run_routine = make_routine_runner(settings, journal, make_tool_server(search, journal, memory, read_only=True, apps=apps, attio=attio,
                                                                           services=services, portfolio=portfolio, reviewer=reviewer),
                                      memory, now, with_apps=apps is not None, services=services, attio=attio, extra_tools=reads_too)
    scheduler = Scheduler(schedule, notify, journal, ledger, settings.daily_usd,
                          run_routine, now=now, on_delivered=conversation.record_delivery,
                          fallback=telegram if on_whatsapp else None, history_file=settings.data_dir / "routine_history.json")     # WhatsApp refuses messages outside its 24-hour window

    # A brief before each meeting with someone outside the firm (brief.py). It is written by the main model on a
    # read-only agent of its own, so that it never waits behind a routine and is worth reading.
    briefer = brief_runner = None
    if apps is not None and settings.meeting_briefs:
        from .brief import SUGGEST_AGAIN_DAYS, Briefer
        from .search import make_search_handler
        brief_runner = make_routine_runner(settings, journal, make_tool_server(search, journal, memory, read_only=True, apps=apps, attio=attio,
                                                                                services=services),
                                           memory, now, with_apps=True, services=services, attio=attio, model=settings.model,
                                           program_tasks=True)
        briefer = Briefer(apps.events_between, brief_runner, notify.send, journal, ledger, settings.daily_usd, now=now,
                          state_file=settings.data_dir / "briefed.json", search=make_search_handler(search, journal), taint=gate.taint,
                          on_delivered=conversation.record_delivery,
                          suggest=(lambda name, company: leads.find(name, company, reuse_days=SUGGEST_AGAIN_DAYS)) if leads is not None else None)

    mark("jobs")
    watcher = triage = None
    if watches is not None:
        # Its own read-only agent, so that a check of an incoming email never waits behind a routine or the conversation.
        triage = make_triage_runner(settings, journal, make_tool_server(search, journal, memory, read_only=True, apps=apps, mail_only=True), now)
        watcher = box["watcher"] = Watcher(watches, ComposioTriggers(settings.composio_api_key, f"odysseus-{settings.owner_id}"), triage, notify,
                                           journal, ledger, settings.daily_usd, now=now, on_delivered=conversation.record_delivery,
                                           on_read=lambda text: (gate.taint.add(text), tracing.hide(text)))
        # While anything is being watched, keep the stream of events open: try again every minute if it cannot be
        # opened, and tell the owner if it stays closed.
        me.tasks.append(asyncio.create_task(watcher.keep_listening(asyncio.get_running_loop())))

    interrupted = unfinished(journal)      # read before this run of the program writes anything
    mark("built")
    # Connect the agent loops now, so the first message, and the first job in the background, are not slow.
    if host:
        await asyncio.gather(runner.start(), *(w.start() for w in workers))
    mark("loop")
    if interrupted is not None:
        # A task was running when the program last stopped. Say so: before this, it vanished without a word.
        try:
            await notify.send(restart_notice(interrupted, runner.resumed))
        except Exception as e:
            journal.write("error", where="restart.notice", error=type(e).__name__)
    elif runner.lost_session:
        # Nothing was interrupted, but the saved conversation could not be picked up. That was silent before.
        try:
            await notify.send(LOST_CONVERSATION)
        except Exception as e:
            journal.write("error", where="restart.notice", error=type(e).__name__)
    journal.write("start", model=settings.model, background_model=settings.background_model, apps=apps is not None, browser=browser is not None,
                  files=ocr is not None, voice=voice is not None, watches=watches is not None, briefs=briefer is not None, tracing=tracing.on, whatsapp=whatsapp is not None, vobiz=vobiz is not None, imessage=imessage is not None, host=host, boot=marks or {})
    if whatsapp is not None:
        me.closers += [attach(whatsapp, shared.door), close_http]
    me.tasks.append(asyncio.create_task(scheduler.run_forever()))
    if briefer is not None:
        me.tasks.append(asyncio.create_task(briefer.run_forever()))
    # Each morning: three of the owner's own past notes from their site, and the questions still open (resurface.py).
    # Code only, reads only, the host's alone. Telegram, for the links and the italics.
    if host and settings.reading_repo and settings.reading_token and re.fullmatch(r"\d\d:\d\d", settings.review_at):
        from .booknotes import GitHubFiles
        from .resurface import Review
        review = Review(GitHubFiles(settings.reading_repo, settings.reading_token, settings.reading_branch), telegram.send_html, journal,
                        state_file=settings.data_dir / "review.json", at=settings.review_at, studio=settings.reading_studio,
                        on_delivered=conversation.record_delivery)
        me.tasks.append(asyncio.create_task(review.run_forever()))
    # Once a week: has anything happened to the companies the owner passed on (decisions.py). A read-only run on the small model.
    watch_runner = make_routine_runner(settings, journal, make_tool_server(search, journal, memory, read_only=True), memory, now, program_tasks=True)
    passwatch = PassWatch(decisions, watch_runner, notify.send, journal, ledger, settings.daily_usd, now=now,
                          state_file=settings.data_dir / "passwatch.json", on_delivered=conversation.record_delivery)
    me.tasks.append(asyncio.create_task(passwatch.run_forever()))
    # Each working day: the check-ins that are due (checkins.py). Code only. The buttons need Telegram.
    if re.fullmatch(r"\d\d:\d\d", settings.checkins_at):
        async def note_on_person(item: dict, text: str) -> bool:
            if attio is None or not item.get("email"):
                return False
            rid = (await attio.intake._existing("people", {"email_addresses": [item["email"]]}))[0]
            if rid is None:
                return False
            await attio._call(attio._b.call("create-note", {"parent_object": "people", "parent_record_id": rid,
                                                           "title": f"Check-in, {now().strftime('%d %b %Y')}", "content": text}))
            return True
        async def people_to_ask_about(item: dict) -> list:
            company = item["email"].rsplit("@", 1)[-1] if item.get("email") else ""
            from .brief import PUBLIC_MAIL
            return await leads.find(item["name"], "" if company in PUBLIC_MAIL else company, item.get("suggested") or [])
        me.tasks.append(asyncio.create_task(CheckinLoop(checkins, telegram, journal, now=now, at=settings.checkins_at,
                                                   suggest=people_to_ask_about if leads is not None else None,
                                                   send_mail=apps.send_for_code if apps is not None else None,
                                                   messages=apps.messages_for_code if apps is not None else None, note=note_on_person,
                                                   on_delivered=conversation.record_delivery).run_forever()))
    # Each morning: who the owner met or heard from that is not in the firm's database (capture.py). Code only, reads only.
    if apps is not None and attio is not None and re.fullmatch(r"\d\d:\d\d", settings.capture_at):
        async def in_database(email: str) -> bool:
            return (await attio.intake._existing("people", {"email_addresses": [email]}))[0] is not None

        async def propose(question: str, prompt: str) -> None:
            await conversation.offer_task(notify, question, prompt, CAPTURE_MATERIAL)
        from zoneinfo import ZoneInfo
        capture = Capture(apps.events_between, apps.messages_for_code, apps.my_address, in_database, propose, journal, now=now,
                          state_file=settings.data_dir / "captured.json", at=settings.capture_at, zone=ZoneInfo(settings.tz) if settings.tz else None)
        me.tasks.append(asyncio.create_task(capture.run_forever()))
    # Once a week: the pipeline review, by code (pipeline.py). Each morning: updates from the firm's companies, found by
    # code and handed to the assistant one by one to read and record (portfolio.py).
    if reviewer is not None and settings.pipeline_review_at != "off":
        me.tasks.append(asyncio.create_task(reviewer.run_forever(notify.send, conversation.record_delivery)))
    if apps is not None and re.fullmatch(r"\d\d:\d\d", settings.updates_at):
        from zoneinfo import ZoneInfo
        from .portfolio import MATERIAL as UPDATE_MATERIAL
        from .portfolio import UpdateLoop, portfolio_domains

        async def company_domains() -> set:
            return await portfolio_domains(attio) if attio is not None else set()
        updates = UpdateLoop(portfolio, apps.messages_for_code, company_domains,
                             lambda prompt: conversation.handle(settings.owner_id, prompt, own_chat, material=UPDATE_MATERIAL, background=True),
                             journal, now=now, state_file=settings.data_dir / "updates.json", at=settings.updates_at,
                             zone=ZoneInfo(settings.tz) if settings.tz else None)
        me.tasks.append(asyncio.create_task(updates.run_forever()))
    # Connecting a service from the chat (connect.py). Afterwards only this person's assistant is built again, so
    # nobody else notices. Where the sign-in comes back to: a page of this program's own if it has a public address;
    # otherwise this machine for the host, and for an invited person, whose browser cannot reach this machine, the
    # address their browser ended on, sent back through the chat.
    conversation._connector = Connector(settings.data_dir, journal, also=("Attio",), restart=lambda: shared.rebuild(settings.owner_id),
                                        remote=not host, public_url=settings.public_url if shared.door.listening else "", door=shared.door)
    if brief_runner is not None:
        me.closers.append(brief_runner.runner.close)
    if leads_runner is not None:
        me.closers.append(leads_runner.runner.close)
    me.closers += [watch_runner.runner.close, runner.close, *(w.close for w in workers), run_routine.runner.close]
    me.gate = gate
    me.agents = [runner, *workers, run_routine.runner, watch_runner.runner, *(r.runner for r in (brief_runner, leads_runner, triage, drafter) if r is not None)]
    # Every agent this person has, attended or not, is held to the day's budget while it works and is counted by the
    # others while it runs: a routine, a brief or a draft for a colleague can no longer take the day past its limit.
    for agent in me.agents:
        agent.within(ledger, settings.daily_usd)
    if watcher is not None:
        me.closers += [watcher.stop, triage.runner.close]
    return me


def member_settings(settings: Settings, user_id: int) -> Settings:
    """The host's settings as an invited person gets them: their own id, a folder of their own inside the host's, their
    own daily limit, and no WhatsApp (one number serves one person)."""
    return replace(settings, owner_id=user_id, data_dir=settings.data_dir / "people" / str(user_id),
                   daily_usd=settings.member_daily_usd or settings.daily_usd, notify_on="telegram",
                   whatsapp_token="", whatsapp_phone_id="", whatsapp_app_secret="", owner_whatsapp="",
                   linq_token="", linq_number="", linq_secret="", owner_imessage="",
                   vobiz_auth_id="", vobiz_auth_token="", vobiz_channel_id="", vobiz_number="", vobiz_secret="",
                   reading_repo="", reading_token="")       # the reading notes go to the host's own website, and only the host's


def firm_settings(settings: Settings, chat_id: int, attio: bool) -> Settings:
    """The host's settings as the group's assistant gets them: the group is who it answers, a folder of its own, a
    member's daily limit, and no key for anybody's mail, calendar, apps, mailbox or browser. With `attio`, it reads the
    firm's database through the host's sign-in: one sign-in, in one file, renewed in one place."""
    return replace(member_settings(settings, chat_id), data_dir=settings.data_dir / "people" / "group", composio_api_key="",
                   agentmail_api_key="", browser_use_api_key="", owner_emails=(), attio_file=(settings.data_dir / "attio.json") if attio else None)


async def main() -> int:
    marks = {"imports": round(time.monotonic() - _BEGAN, 1)}      # seconds since the process began, at each stage of start-up
    try:
        settings = load()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    service = synced_folder(settings.data_dir)
    if service:
        print(f"Warning: the data folder {settings.data_dir.resolve()} is inside a {service} folder, so your mail text and "
              "conversation are being copied to that service. Set ODYSSEUS_DATA_DIR to a folder that is not synced.", file=sys.stderr)
    # The one port, opened only if something needs it: a public address for sign-ins, or WhatsApp (door.py).
    door = Door()
    if settings.public_url or imessage_on(settings) or vobiz_on(settings) or (settings.whatsapp_token and settings.whatsapp_phone_id and settings.whatsapp_app_secret and settings.owner_whatsapp):
        await door.open(settings.whatsapp_port)
        from . import shortlink
        shortlink.LINKS.open(door, settings.public_url)       # links the owner is sent go out under this address, not a supplier's
    # What there is one of, however many people are served: the bot, the search client, one plain call to the small model
    # (the second reader, and closing a conversation), and the optional record of every task in Langfuse, from which
    # every secret in the settings is removed.
    bot = Bot(settings.telegram_token)
    tracing = Tracing(settings.langfuse_public_key, settings.langfuse_secret_key, settings.langfuse_base_url,
                      secrets=(settings.telegram_token, settings.anthropic_api_key, settings.exa_api_key, settings.composio_api_key,
                               settings.browser_use_api_key, settings.mistral_api_key, settings.agentmail_api_key, settings.whatsapp_token,
                               settings.whatsapp_app_secret, settings.linq_token, settings.linq_secret, settings.vobiz_auth_token, settings.vobiz_secret), full_content=settings.langfuse_full_content)
    marks["tracing"] = round(time.monotonic() - _BEGAN, 1)
    second_call = anthropic_call(settings.anthropic_api_key)
    # The people the host has invited, each with an assistant of their own (people.py), and the one way between two
    # people's assistants (peers.py). A colleague is found by the name Telegram gave when they joined.
    people = People(settings.data_dir / "people.json")
    from .peers import Switchboard
    from .people import clean_name
    try:
        host_name = clean_name((await bot.get_chat(settings.owner_id)).full_name)
    except Exception:
        host_name = ""         # learned from the host's first message instead (people.py)
    board = Switchboard(state_file=settings.data_dir / "switchboard.json")
    shared = SimpleNamespace(bot=bot, tracing=tracing, second_call=second_call,
                             search=Exa(api_key=settings.exa_api_key).tools.web_search(), door=door, rebuild=lambda user_id: None, board=board,
                             name_of=lambda user_id: host_name if user_id == settings.owner_id else people.members.get(str(user_id), {}).get("name", ""))
    host = await assemble(settings, shared, True, marks)
    if service:
        host.journal.write("warning", where="data_dir", note=f"inside a {service} folder")
    asyncio.get_running_loop().run_in_executor(None, second_call.warm)      # load the reader's library now, off the main path

    async def link(code: str) -> str:
        return f"https://t.me/{(await bot.get_me()).username}?start={code}"
    def build(user_id: int):
        return assemble(settings, shared, True) if user_id == settings.owner_id else assemble(member_settings(settings, user_id), shared, False)
    async def build_firm(chat_id: int, attio: bool):
        # No way to or from any person's assistant: it is not on the switchboard, and connecting a service does not rebuild it.
        apart = SimpleNamespace(**{**vars(shared), "board": None, "rebuild": lambda user_id: None})
        return await assemble(firm_settings(settings, chat_id, attio), apart, False)
    house = House(settings.owner_id, people, host.journal, build, link, most=settings.people_max, board=board,
                  firm=build_firm, group_file=settings.data_dir / "group.json")
    house.add(settings.owner_id, host)
    shared.rebuild = house.rebuild_soon
    for user_id in people.ids()[:settings.people_max - 1]:
        try:
            house.add(user_id, await assemble(member_settings(settings, user_id), shared, False))
        except Exception as e:
            host.journal.write("error", where="people.start", user_id=user_id, error=type(e).__name__, message=str(e)[:200])
    if people.ids():
        host.journal.write("people", serving=len(people.ids()) + 1)
    if await house.start_firm():
        host.journal.write("firm", outcome="started")
    close_groups = None
    if whatsapp_group_on(settings):
        # The group's assistant in a WhatsApp group (whatsapp_group.py). It is reached through the direct route's door,
        # which asks `shared.groups` at each delivery, so it is there whichever copy of the host's assistant is running.
        from .whatsapp_group import WhatsAppGroups, members_from
        group_http, close_groups = make_http(settings.whatsapp_token)
        shared.groups = WhatsAppGroups(house, host.journal, group_http, phone_id=settings.whatsapp_group_phone_id,
                                       owner_number=settings.owner_whatsapp, owner_id=settings.owner_id,
                                       members=members_from(settings.whatsapp_group_members), name=settings.group_name,
                                       tell=TelegramChat(bot, settings.owner_id).send, state_file=settings.data_dir / "whatsapp_group.json")
        host.journal.write("whatsapp_group", outcome="on", serving=bool(shared.groups.group_id))
    waiting = board.resume()        # drafts that were waiting for a colleague's tap when the program last stopped
    if waiting:
        host.journal.write("exchange", resumed=waiting)
    async def park_the_idle() -> None:
        # An invited person who has said nothing for a while keeps their assistant and loses only its processes.
        while settings.park_after_min > 0:
            await asyncio.sleep(60)
            try:
                await house.park_idle(settings.park_after_min * 60)
            except Exception as e:
                host.journal.write("error", where="people.park", error=type(e).__name__)
    parking = asyncio.create_task(park_the_idle())
    dispatcher = build_dispatcher(bot, house)
    try:
        # Long polling: the bot asks Telegram for messages, so no public address is needed.
        await dispatcher.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        parking.cancel()
        if close_groups is not None:
            await close_groups()
        await house.close()
        await door.close()
        await bot.session.close()
        tracing.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
