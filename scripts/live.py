"""First live check of stage 1, without Telegram.

Runs the real agent loop and the real search against the points the book's chapter on the conversation lists
as untested. Spends real money (cents). Prints a JSON summary; never prints keys.

    .venv/Scripts/python scripts/live.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_agent_sdk import ClaudeSDKClient, ResultMessage, SystemMessage  # noqa: E402
from exa_py import Exa  # noqa: E402

from odysseus.agent import AgentRunner  # noqa: E402
from odysseus.config import load  # noqa: E402
from odysseus.conversation import Conversation  # noqa: E402
from odysseus.journal import Journal, Ledger  # noqa: E402
from odysseus.search import make_tool_server  # noqa: E402


class ConsoleChat:
    def __init__(self):
        self.sent, self.drafts = [], 0

    async def send(self, text):
        self.sent.append(text)

    async def draft(self, draft_id, text):
        self.drafts += 1


async def main():
    settings = replace(load(), data_dir=Path("var/live"))
    journal = Journal(settings.data_dir / f"journal-{int(time.time())}.jsonl")
    spec = Exa(api_key=settings.exa_api_key).tools.web_search()
    runner = AgentRunner(settings, journal, make_tool_server(spec, journal))
    conv = Conversation(settings, journal, Ledger(journal), runner)
    out: dict = {"model": settings.model}

    # 1. Which tools is the model really offered?
    t0 = time.monotonic()
    async with ClaudeSDKClient(options=runner.options()) as client:
        await client.query("Reply with the single word: ready")
        async for msg in client.receive_response():
            if isinstance(msg, SystemMessage) and msg.subtype == "init":
                out["tools_offered"] = msg.data.get("tools")
                out["mcp_servers"] = msg.data.get("mcp_servers")
            elif isinstance(msg, ResultMessage):
                out["probe"] = {"subtype": msg.subtype, "cost_usd": msg.total_cost_usd, "reply": msg.result,
                                "seconds": round(time.monotonic() - t0, 1)}

    async def ask(label, text):
        chat = ConsoleChat()
        t = time.monotonic()
        await conv.handle(settings.owner_id, text, chat)
        await conv.wait()
        end = [r for r in journal.read() if r["kind"] == "task_end"][-1]
        out[label] = {"reply": chat.sent, "drafts": chat.drafts, "seconds": round(time.monotonic() - t, 1),
                      "status": end["status"], "cost_usd": end["cost_usd"], "turns": end.get("turns")}

    # 1b. How long does a one-word reply take on the long-lived client?
    t0 = time.monotonic()
    await runner.start()
    out["connect_seconds"] = round(time.monotonic() - t0, 1)
    await ask("one_word_first", "Reply with the single word: ready")
    await ask("one_word_second", "Reply with the single word: steady")

    # 2. A question that needs a search.  3. A follow-up that needs the session to continue.
    await ask("search_question", "What is the latest stable release of Python, and when did it come out? One line.")
    await ask("follow_up", "What did I ask you a moment ago? Answer in five words or fewer.")

    # 4. Stop a long task part-way.
    chat = ConsoleChat()
    await conv.handle(settings.owner_id, "Research the history of the bicycle in depth: run at least eight separate "
                      "searches on different periods, then write a long, detailed essay.", chat)
    await asyncio.sleep(8)
    t = time.monotonic()
    await conv.handle(settings.owner_id, "stop", chat)
    await conv.wait()
    end = [r for r in journal.read() if r["kind"] == "task_end"][-1]
    out["stop"] = {"seconds_to_stop": round(time.monotonic() - t, 2), "status": end["status"],
                   "forced": end.get("forced", False), "cost_usd": end["cost_usd"], "detail": end.get("detail"),
                   "last_message": chat.sent[-1][-80:] if chat.sent else None, "drafts": chat.drafts}

    # 5. A stranger.
    chat = ConsoleChat()
    await conv.handle(settings.owner_id + 1, "hello", chat)
    out["stranger"] = {"replies": len(chat.sent)}

    # 6. After a stop, is the same client still usable?
    await ask("after_stop", "Reply with the single word: back")
    await runner.close()

    out["tool_calls"] = [{k: r.get(k) for k in ("name", "ok", "results", "query")} for r in journal.read() if r["kind"] == "tool"]
    out["errors"] = [r for r in journal.read() if r["kind"] == "error"]
    out["total_cost_usd"] = round(sum(r.get("cost_usd") or 0 for r in journal.read() if r["kind"] == "task_end")
                                  + (out.get("probe", {}).get("cost_usd") or 0), 4)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
