"""Start Odysseus:  python -m odysseus"""
from __future__ import annotations

import asyncio
import sys

from aiogram import Bot
from exa_py import Exa

from .agent import AgentRunner, conversation_prompt
from .anyapp import AnyApp
from .apps import Apps, ComposioBackend
from .browser import Browser, BrowserUseBackend
from .config import ConfigError, load, make_clock
from .conversation import Conversation
from .journal import Journal, Ledger
from .memory import MemoryStore
from .ocr import MistralOcr
from .routines import make_routine_runner
from .schedule import Scheduler, ScheduleStore
from .telegram import TelegramChat, build_dispatcher
from .tools import make_gate, make_tool_server
from .tracing import Tracing


async def main() -> int:
    try:
        settings = load()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    now = make_clock(settings.tz)     # one clock for message stamps, memory dates and the schedule
    journal = Journal(settings.data_dir / "journal.jsonl", echo=True)   # a summary line per step also goes to the machine's log
    ledger = Ledger(journal)
    memory = MemoryStore(settings.data_dir / "memory", now=now)
    schedule = ScheduleStore(settings.data_dir / "schedule.db", now=now)
    search = Exa(api_key=settings.exa_api_key).tools.web_search()

    # Mail and calendar exist only if a connector key is set. The owner's Telegram id names their connections.
    connector = ComposioBackend(settings.composio_api_key, f"odysseus-{settings.owner_id}") if settings.composio_api_key else None
    apps = Apps(connector, journal, now, settings.tz) if connector else None
    anyapp = AnyApp(connector, journal) if connector else None      # every other app in the connector's catalogue
    browser = Browser(BrowserUseBackend(settings.browser_use_api_key, settings.data_dir / "browser.json"), journal, settings.task_usd) \
        if settings.browser_use_api_key else None
    ocr = MistralOcr(settings.mistral_api_key) if settings.mistral_api_key else None
    gate = make_gate(journal, now, with_apps=apps is not None, with_browser=browser is not None,
                     browser_ask_signed_in=settings.browser_ask_signed_in, anyapp=anyapp, apps=apps)
    runner = AgentRunner(settings, journal, make_tool_server(search, journal, memory, schedule, gate=gate, apps=apps, browser=browser,
                                                            anyapp=anyapp),
                         gate=gate, system_prompt=conversation_prompt(apps is not None, browser is not None, settings.browser_ask_signed_in,
                                                           with_files=ocr is not None, with_anyapp=anyapp is not None))
    # Optional: a readable record of every task in Langfuse. Every secret in the settings is removed from what is sent.
    tracing = Tracing(settings.langfuse_public_key, settings.langfuse_secret_key, settings.langfuse_base_url,
                      secrets=(settings.telegram_token, settings.anthropic_api_key, settings.exa_api_key, settings.composio_api_key,
                               settings.browser_use_api_key, settings.mistral_api_key))
    conversation = Conversation(settings, journal, ledger, runner, memory, now=now, schedule=schedule, gate=gate, apps=apps,
                                browser=browser, ocr=ocr, anyapp=anyapp, tracing=tracing)
    bot = Bot(settings.telegram_token)

    # Routines run on their own agent, which can only read. In a private chat the chat id is the user's id.
    run_routine = make_routine_runner(settings, journal, make_tool_server(search, journal, memory, read_only=True, apps=apps),
                                      memory, now, with_apps=apps is not None)
    scheduler = Scheduler(schedule, TelegramChat(bot, settings.owner_id), journal, ledger, settings.daily_usd,
                          run_routine, now=now, on_delivered=conversation.record_delivery)

    await runner.start()   # connect the agent loop now, so the first message is not slow
    journal.write("start", model=settings.model, background_model=settings.background_model, apps=apps is not None, browser=browser is not None,
                  files=ocr is not None, tracing=tracing.on)
    ticking = asyncio.create_task(scheduler.run_forever())
    try:
        # Long polling: the bot asks Telegram for messages, so no public address is needed.
        await build_dispatcher(bot, conversation).start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        ticking.cancel()
        await conversation.wait()
        await runner.close()
        await run_routine.runner.close()
        await bot.session.close()
        tracing.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
