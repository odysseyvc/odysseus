"""Start Odysseus:  python -m odysseus"""
from __future__ import annotations

import time

_BEGAN = time.monotonic()      # before the heavy imports below, so that start-up can be timed whole

import asyncio  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402

RESTART = 75      # the exit code by which the child asks to be started again


def supervise() -> int:
    """Run the program as a child process, and start it again whenever it ends asking to be restarted.

    A newly connected service needs a restart (connect.py). Starting again inside the same process was tried first and
    worked six times in a row, except that whatever exists once per process, such as the tracing client, had been shut
    down by the first run and stayed shut. A new process has none of that history.
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
from .mcpapp import load_custom as load_custom_services
from .apps import Apps, ComposioBackend
from .browser import Browser, BrowserUseBackend
from .closing import make_closer
from .config import ConfigError, load, make_clock
from .conversation import Conversation
from .history import History
from .journal import Journal, Ledger
from .memory import MemoryStore
from .deckview import DeckViewer
from .links import Fetcher
from .ocr import MistralOcr
from .pages import make_page_reader
from .review import OUTGOING, anthropic_call, make_second_reader
from .routines import make_routine_runner, make_triage_runner
from .schedule import Scheduler, ScheduleStore
from .telegram import TelegramChat, build_dispatcher
from .tools import empty_server, make_gate, make_tool_server
from .tracing import Tracing
from .voice import MistralVoice
from .watch import ComposioTriggers, Watcher, WatchStore
from .whatsapp import WhatsApp, make_http, serve


SYNCED = ("onedrive", "dropbox", "google drive", "googledrive", "icloud", "mobile documents")


def synced_folder(path) -> str | None:
    """The name of the file-syncing service this path is inside, if any. The data folder holds mail text and the
    conversation in the clear; inside a synced folder it is also copied to that service."""
    full = str(path.resolve()).lower()
    return next((name for name in SYNCED if name in full), None)


def unfinished(journal: Journal) -> dict | None:
    """What was lost when the program last stopped, if anything: a task that had started and not ended, and any
    messages that were waiting behind it."""
    open_task, waiting = None, {}
    for r in journal.read():
        if r["kind"] == "start":
            open_task, waiting = None, {}       # an earlier restart: whatever was lost then has been reported already
        elif r["kind"] == "task_start":
            open_task = {"preview": r.get("preview", ""), "asked": False}
        elif r["kind"] == "task_end" and "routine" not in r and "brief" not in r:
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
    if open_task is None and not waiting:
        return None
    return {**(open_task or {"preview": None, "asked": False}), "waiting": list(waiting.values())}


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
    text += " Ask me again if you still want " + ("these" if waiting else "it") + " done."
    return text + ("" if resumed else " I also no longer have our earlier conversation, only what I remember about you.")


async def main() -> int:
    marks = {"imports": round(time.monotonic() - _BEGAN, 1)}      # seconds since the process began, at each stage of start-up
    try:
        settings = load()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    now = make_clock(settings.tz)     # one clock for message stamps, memory dates and the schedule
    journal = Journal(settings.data_dir / "journal.jsonl", echo=True)   # a summary line per step also goes to the machine's log
    ledger = Ledger(journal, tz=settings.tz)      # "today" ends at the owner's midnight
    service = synced_folder(settings.data_dir)
    if service:
        journal.write("warning", where="data_dir", note=f"inside a {service} folder")
        print(f"Warning: the data folder {settings.data_dir.resolve()} is inside a {service} folder, so your mail text and "
              "conversation are being copied to that service. Set ODYSSEUS_DATA_DIR to a folder that is not synced.", file=sys.stderr)
    memory = MemoryStore(settings.data_dir / "memory", now=now)
    schedule = ScheduleStore(settings.data_dir / "schedule.db", now=now)
    search = Exa(api_key=settings.exa_api_key).tools.web_search()

    # Mail and calendar exist only if a connector key is set. The owner's Telegram id names their connections.
    connector = ComposioBackend(settings.composio_api_key, f"odysseus-{settings.owner_id}") if settings.composio_api_key else None
    apps = Apps(connector, journal, now, settings.tz) if connector else None
    anyapp = AnyApp(connector, journal) if connector else None      # every other app in the connector's catalogue
    # Attio, the firm's customer database, through Attio's own server. It exists once the owner has signed in (scripts/connect_attio.py).
    attio_tokens = FileTokens(settings.data_dir / "attio.json")
    attio = Attio(McpBackend(attio_tokens), journal) if attio_tokens.signed_in() else None
    load_custom_services(settings.data_dir)                          # servers the owner connected from the chat, by address
    services = connected_services(settings.data_dir, journal)       # other services with a server of their own, once signed in to
    marks["services"] = round(time.monotonic() - _BEGAN, 1)
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
                     auto_small=not settings.ask_every_change)
    second_call = anthropic_call(settings.anthropic_api_key)      # one plain call to the small model: the second reader, and closing a conversation
    history = History(settings.data_dir / "history.jsonl")
    if apps is not None and settings.review_model.lower() != "off":
        # What reaches other people is read once more before the owner is asked. The reader can add a warning; it cannot approve.
        gate.set_second_reader(make_second_reader(journal, second_call,
                                                  settings.review_model or settings.background_model, gate.taint), OUTGOING)
    marks["gate"] = round(time.monotonic() - _BEGAN, 1)
    runner = AgentRunner(settings, journal, make_tool_server(search, journal, memory, schedule, gate=gate, apps=apps, browser=browser,
                                                            anyapp=anyapp, watches=watches, on_watch=on_watch, attio=attio, services=services, history=history),
                         gate=gate, system_prompt=conversation_prompt(apps is not None, browser is not None, settings.browser_ask_signed_in,
                                                           with_files=ocr is not None, with_anyapp=anyapp is not None,
                                                           with_watches=watches is not None, with_attio=attio is not None)
                         + "".join(s.prompt() for s in services), persist=True)
    marks["runner"] = round(time.monotonic() - _BEGAN, 1)
    # Optional: a readable record of every task in Langfuse. Every secret in the settings is removed from what is sent.
    tracing = Tracing(settings.langfuse_public_key, settings.langfuse_secret_key, settings.langfuse_base_url,
                      secrets=(settings.telegram_token, settings.anthropic_api_key, settings.exa_api_key, settings.composio_api_key,
                               settings.browser_use_api_key, settings.mistral_api_key, settings.whatsapp_token,
                               settings.whatsapp_app_secret), full_content=settings.langfuse_full_content)
    marks["tracing"] = round(time.monotonic() - _BEGAN, 1)
    gate.taint.listeners.append(tracing.hide)      # what is read from mail, calendar and apps is kept out of the traces
    async def viewer_address() -> str:
        # The address given to a site that asks who is viewing: the one in the settings, or the connected mailbox's own.
        return settings.viewer_email or await apps.my_address()
    # A short PDF's pages are looked at in a run of their own, with no tools; only its notes reach the conversation.
    pages = make_page_reader(settings, journal, empty_server(),
                             model=settings.pages_model or None) if ocr is not None else None
    marks["pages"] = round(time.monotonic() - _BEGAN, 1)
    conversation = Conversation(settings, journal, ledger, runner, memory, now=now, schedule=schedule, gate=gate, apps=apps,
                                browser=browser, ocr=ocr, anyapp=anyapp, tracing=tracing, voice=voice, watches=watches, attio=attio, services=services,
                                pages=pages, links=Fetcher() if ocr is not None else None, history=history,
                                closer=make_closer(journal, second_call, settings.background_model),
                                deckview=DeckViewer(browser._b, apps.messages_for_code, viewer_address, journal, max(settings.task_usd, 1.0))
                                if browser is not None and apps is not None else None)
    marks["conversation"] = round(time.monotonic() - _BEGAN, 1)
    if apps is not None:
        apps.reader, apps.fetcher = conversation.reader, Fetcher()      # so that a file attached to an email is read as any file is
    if attio is not None:
        attio.warm()        # read the workspace's layout now, so the first question about the firm does not wait for it
    bot = Bot(settings.telegram_token)

    # Optional: WhatsApp as a second channel. It needs a door (a port Meta can reach), which Telegram does not.
    whatsapp = close_http = door = None
    if settings.whatsapp_token and settings.whatsapp_phone_id and settings.whatsapp_app_secret and settings.owner_whatsapp:
        http, close_http = make_http(settings.whatsapp_token)
        whatsapp = WhatsApp(conversation, journal, http, phone_id=settings.whatsapp_phone_id, owner_number=settings.owner_whatsapp,
                            app_secret=settings.whatsapp_app_secret, verify_token=settings.whatsapp_verify_token, owner_id=settings.owner_id)
    # Reminders and routines go to one place: Telegram, unless the owner chose WhatsApp and it is set up.
    telegram = TelegramChat(bot, settings.owner_id)
    on_whatsapp = whatsapp is not None and settings.notify_on == "whatsapp"
    notify = whatsapp.chat() if on_whatsapp else telegram

    # Routines run on their own agent, which can only read. In a private chat the chat id is the user's id.
    run_routine = make_routine_runner(settings, journal, make_tool_server(search, journal, memory, read_only=True, apps=apps, attio=attio,
                                                                           services=services),
                                      memory, now, with_apps=apps is not None, services=services, attio=attio)
    scheduler = Scheduler(schedule, notify, journal, ledger, settings.daily_usd,
                          run_routine, now=now, on_delivered=conversation.record_delivery,
                          fallback=telegram if on_whatsapp else None, history_file=settings.data_dir / "routine_history.json")     # WhatsApp refuses messages outside its 24-hour window

    # A brief before each meeting with someone outside the firm (brief.py). It is written by the main model on a
    # read-only agent of its own, so that it never waits behind a routine and is worth reading.
    briefer = briefing = brief_runner = None
    if apps is not None and settings.meeting_briefs:
        from .brief import Briefer
        from .search import make_search_handler
        brief_runner = make_routine_runner(settings, journal, make_tool_server(search, journal, memory, read_only=True, apps=apps, attio=attio,
                                                                                services=services),
                                           memory, now, with_apps=True, services=services, attio=attio, model=settings.model,
                                           program_tasks=True)
        briefer = Briefer(apps.events_between, brief_runner, notify.send, journal, ledger, settings.daily_usd, now=now,
                          state_file=settings.data_dir / "briefed.json", search=make_search_handler(search, journal), taint=gate.taint,
                          on_delivered=conversation.record_delivery)

    marks["jobs"] = round(time.monotonic() - _BEGAN, 1)
    watcher = triage = watching = None
    if watches is not None:
        # Its own read-only agent, so that a check of an incoming email never waits behind a routine or the conversation.
        triage = make_triage_runner(settings, journal, make_tool_server(search, journal, memory, read_only=True, apps=apps, mail_only=True), now)
        watcher = box["watcher"] = Watcher(watches, ComposioTriggers(settings.composio_api_key, f"odysseus-{settings.owner_id}"), triage, notify,
                                           journal, ledger, settings.daily_usd, now=now, on_delivered=conversation.record_delivery,
                                           on_read=lambda text: (gate.taint.add(text), tracing.hide(text)))
        # While anything is being watched, keep the stream of events open: try again every minute if it cannot be
        # opened, and tell the owner if it stays closed.
        watching = asyncio.create_task(watcher.keep_listening(asyncio.get_running_loop()))

    interrupted = unfinished(journal)      # read before this run of the program writes anything
    marks["built"] = round(time.monotonic() - _BEGAN, 1)
    await runner.start()   # connect the agent loop now, so the first message is not slow
    marks["loop"] = round(time.monotonic() - _BEGAN, 1)
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
    asyncio.get_running_loop().run_in_executor(None, second_call.warm)      # load the reader's library now, off the main path
    journal.write("start", model=settings.model, background_model=settings.background_model, apps=apps is not None, browser=browser is not None,
                  files=ocr is not None, voice=voice is not None, watches=watches is not None, briefs=briefer is not None, tracing=tracing.on, whatsapp=whatsapp is not None, boot=marks)
    if whatsapp is not None:
        door = await serve(whatsapp, settings.whatsapp_port)
    ticking = asyncio.create_task(scheduler.run_forever())
    if briefer is not None:
        briefing = asyncio.create_task(briefer.run_forever())
    dispatcher = build_dispatcher(bot, conversation)
    again = []

    def restart() -> None:
        # A newly connected service brings tools that are fixed when the program starts, so it starts again, in place.
        again.append(True)
        asyncio.get_running_loop().call_later(1.5, lambda: asyncio.ensure_future(dispatcher.stop_polling()))
    conversation._connector = Connector(settings.data_dir, journal, restart, also=("Attio",) if attio is not None else ())
    try:
        # Long polling: the bot asks Telegram for messages, so no public address is needed.
        await dispatcher.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        ticking.cancel()
        if briefing is not None:
            briefing.cancel()
            await brief_runner.runner.close()
        if watching is not None:
            watching.cancel()
        await conversation.wait()
        await runner.close()
        await run_routine.runner.close()
        if watcher is not None:
            watcher.stop()
            await triage.runner.close()
        await bot.session.close()
        if door is not None:
            await door.cleanup()
        if close_http is not None:
            await close_http()
        tracing.close()
    return RESTART if again else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
