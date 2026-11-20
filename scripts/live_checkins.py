"""People to keep in touch with, live: the real model sets the list up from plain requests, and code does the rest.

    python scripts/live_checkins.py

The real model; an invented list, an invented colleague, a stand-in mailbox and a clock moved forward by the script.
No email is sent and nothing real is touched. Prints what the owner would have seen at each step.
"""
from __future__ import annotations

import asyncio
import sys
import time
import warnings
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

warnings.simplefilter("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exa_py import Exa  # noqa: E402

from odysseus.agent import AgentRunner, conversation_prompt  # noqa: E402
from odysseus.checkins import CheckinLoop, Checkins  # noqa: E402
from odysseus.config import load  # noqa: E402
from odysseus.conversation import Conversation  # noqa: E402
from odysseus.journal import Journal, Ledger  # noqa: E402
from odysseus.memory import MemoryStore  # noqa: E402
from odysseus.tools import make_gate, make_tool_server  # noqa: E402

COLLEAGUE = "sam@acme-capital.example"


class Chat:
    def __init__(self, conv_box):
        self.log, self._box = [], conv_box

    async def send(self, text):
        self.log.append(("reply", text))

    async def draft(self, draft_id, text):
        pass

    async def ask(self, text, approval_id, thread=False):
        self.log.append(("question, approved by the script", text))
        asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(self._box["conv"].handle_decision(self._box["owner"], approval_id, True)))

    async def choose(self, text, offer, options):
        self.log.append(("buttons " + " | ".join(options), text))
        self.offer = offer


async def main() -> int:
    real = load()
    settings = replace(real, data_dir=Path(f"var/live/checkins-{int(time.time())}"))
    journal = Journal(settings.data_dir / "journal.jsonl")
    memory = MemoryStore(settings.data_dir / "memory")
    clock = SimpleNamespace(t=datetime(2026, 11, 23, 9, 0))       # a Monday
    now = lambda: clock.t
    store = Checkins(settings.data_dir / "checkins.json", today=lambda: clock.t.date())
    gate = make_gate(journal, now, timeout_s=30, auto_small=True)
    search = Exa(api_key=settings.exa_api_key).tools.web_search()
    runner = AgentRunner(settings, journal, make_tool_server(search, journal, memory, gate=gate, checkins=store), gate=gate,
                         system_prompt=conversation_prompt(False))
    conv = Conversation(settings, journal, Ledger(journal), runner, memory, now=now, gate=gate, checkins=store)
    box = {"conv": conv, "owner": settings.owner_id}
    chat, mails, inbox = Chat(box), [], []

    async def send_mail(to, subject, body):
        mails.append((to, subject, body))

    async def messages(query, limit):
        return list(inbox)
    loop = CheckinLoop(store, chat, journal, now=now, send_mail=send_mail, messages=messages)
    await runner.start()

    async def say(text):
        chat.log.append(("the owner", text))
        await conv.handle(settings.owner_id, text, chat)
        await conv.wait()

    await say("I want to speak to Tara Menon every month, starting today. Ask her who the best founder she has met lately is.")
    await say(f"And Dev Malhotra every two months, starting today, but that one is Sam Rivera's job ({COLLEAGUE}). Keep reminding Sam until it's done.")
    clock.t = clock.t.replace(hour=9, minute=45)
    chat.log.append(("the clock", f"{clock.t:%a %d %b %H:%M}: the daily check runs -> {await loop.tick()}"))
    line = await conv.handle_choice(settings.owner_id, chat.offer, 0)
    chat.log.append(("the owner taps Done; left on the message", line))
    await say("She said Priya Nair at Lumen Grid is the sharpest she's met this year, and offered to introduce us.")
    clock.t += timedelta(days=3)
    chat.log.append(("the clock", f"{clock.t:%a %d %b}: the daily check runs -> {await loop.tick()}"))
    inbox.append({"sender": f"Sam Rivera <{COLLEAGUE}>", "messageTimestamp": (clock.t + timedelta(hours=1)).isoformat(),
                  "messageText": "Spoke to Dev this morning. Nobody new, but he is raising in January.\n\nOn Mon 23 Nov someone wrote:\n> Hi Sam,"})
    clock.t += timedelta(hours=3)
    chat.log.append(("the clock", f"{clock.t:%a %d %b %H:%M}: Sam has replied; the check runs -> {await loop.tick()}"))
    await say("/checkins")
    await runner.close()

    for who, text in chat.log:
        print(f"\n--- {who} ---\n{text[:900]}")
    print("\n--- emails the stand-in mailbox was asked to send ---")
    for to, subject, body in mails:
        print(f"to {to} | {subject} | {body.splitlines()[2][:80]}")
    cost = sum(r.get("cost_usd", 0) for r in journal.read() if r["kind"] == "task_end")
    tools = [r["name"] for r in journal.read() if r["kind"] == "tool" and r.get("ok")]
    tara, dev = store.get("tara"), store.get("dev")
    checks = {"both on the list": tara is not None and dev is not None,
              "Tara monthly, the owner's job": bool(tara) and tara["every"] in (30, 31) and not tara["assignee_email"],
              "Dev every two months, Sam's job": bool(dev) and dev["every"] in (60, 61) and dev["assignee_email"] == COLLEAGUE,
              "the owner approved Sam's emails once": sum(1 for w, _ in chat.log if w.startswith("question")) == 1,
              "what came of Tara's was recorded": bool(tara) and bool(tara["results"]) and "Priya" in tara["results"][-1]["text"],
              "two emails to Sam, then his reply ended them": len(mails) == 2 and bool(dev) and bool(dev["results"]) and dev["asks"] == 0}
    print(f"\ntools used: {tools}\ncost: ${cost:.3f}")
    for name, ok in checks.items():
        print(("PASS  " if ok else "FAIL  ") + name)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
