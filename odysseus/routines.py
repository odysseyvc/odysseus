"""Running a routine: one unattended task on a separate, read-only agent.

A routine does not share the owner's conversation. It starts with nothing but
its own instructions, the owner's profile and the time, runs on the cheaper
background model, and can only search. When it finishes, its agent is shut
down, so nothing is carried from one run to the next.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from claude_agent_sdk import ClaudeSDKClient

from .agent import ROUTINE_APPS_PROMPT, ROUTINE_PROMPT, AgentRunner
from .config import Settings
from .journal import Journal
from .memory import MEMORY_NOTE, MemoryStore
from .tools import routine_tools


def make_routine_runner(settings: Settings, journal: Journal, read_only_server: Any, memory: MemoryStore,
                        now: Callable[[], datetime] = datetime.now,
                        client_factory: Callable[..., Any] = ClaudeSDKClient, with_apps: bool = False):
    runner = AgentRunner(settings, journal, read_only_server, client_factory, model=settings.background_model,
                         system_prompt=ROUTINE_PROMPT + (ROUTINE_APPS_PROMPT if with_apps else ""),
                         allowed_tools=routine_tools(with_apps))

    async def noop(text: str) -> None:
        pass

    async def run_routine(instructions: str) -> tuple[str, str, float]:
        await runner.forget_session()
        prompt = (f"MEMORY\n{MEMORY_NOTE}\n{memory.profile_text()}\nEND OF MEMORY\n\n"
                  f"ROUTINE INSTRUCTIONS (written earlier, at the owner's request)\n{instructions}\n\n"
                  f"[{now().strftime('%A %d %B %Y, %H:%M')}]")
        try:
            outcome = await runner.run(prompt, noop)
        finally:
            await runner.forget_session()
        return outcome.status, outcome.text, outcome.cost_usd

    run_routine.runner = runner   # for tests and for shutdown
    return run_routine
