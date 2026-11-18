"""Live check of tracing: one real task, traced to Langfuse, then read back and inspected.

The task runs against the errand suite's stand-in mailbox, so no real mail is read. It
is a real model call (about a cent) and a real trace in the Langfuse project. The check
reads the trace back through Langfuse's API and looks for three things: that the model
and tool steps are there, that no key from the settings is in it, and that the text of
the mail the agent read is not in it.

    .venv/Scripts/python scripts/live_tracing.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import warnings
from dataclasses import replace
from pathlib import Path

warnings.simplefilter("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exa_py import Exa  # noqa: E402

from evals.sim import Clock, Sim  # noqa: E402
from evals.world import FakeSearch  # noqa: E402
from odysseus.agent import AgentRunner, conversation_prompt  # noqa: E402
from odysseus.apps import Apps  # noqa: E402
from odysseus.config import load  # noqa: E402
from odysseus.conversation import Conversation  # noqa: E402
from odysseus.journal import Journal, Ledger  # noqa: E402
from odysseus.memory import MemoryStore  # noqa: E402
from odysseus.tools import make_gate, make_tool_server  # noqa: E402
from odysseus.tracing import Tracing  # noqa: E402

MAIL_TEXT = ("Are you free for lunch this Thursday or Friday", "Saffron Table in Bandra")     # from the stand-in mailbox


class Chat:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)

    async def draft(self, draft_id, text):
        pass

    async def ask(self, text, approval_id):
        pass


async def main() -> int:
    settings = replace(load(), data_dir=Path(f"var/live/tracing-{int(time.time())}"))
    secrets = (settings.telegram_token, settings.anthropic_api_key, settings.exa_api_key, settings.composio_api_key,
               settings.browser_use_api_key, settings.mistral_api_key)
    tracing = Tracing(settings.langfuse_public_key, settings.langfuse_secret_key, settings.langfuse_base_url, secrets=secrets, environment="check")
    if not tracing.on:
        print("Tracing is off: the Langfuse keys are not set.")
        return 2
    clock, sim = Clock(), Sim()
    journal = Journal(settings.data_dir / "journal.jsonl")
    memory = MemoryStore(settings.data_dir / "memory", now=clock.now)
    spec = Exa(api_key="unused").tools.web_search()
    apps = Apps(sim, journal, clock.now, tz="Asia/Kolkata")
    gate = make_gate(journal, clock.now, timeout_s=20, apps=apps)
    gate.taint.listeners.append(tracing.hide)
    runner = AgentRunner(settings, journal, make_tool_server(FakeSearch(spec.description, spec.input_schema), journal, memory, gate=gate, apps=apps),
                         gate=gate, system_prompt=conversation_prompt(True))
    conv = Conversation(settings, journal, Ledger(journal), runner, memory, now=clock.now, gate=gate, apps=apps, tracing=tracing)
    chat = Chat()
    trace_ids = []
    real_task = tracing.task

    def task(*a, **k):          # note the id of the trace as it is opened, to read it back afterwards
        cm = real_task(*a, **k)

        class Wrap:
            def __enter__(self):
                out = cm.__enter__()
                trace_ids.append(tracing._client.get_current_trace_id())
                return out

            def __exit__(self, *e):
                return cm.__exit__(*e)
        return Wrap()
    tracing.task = task

    await conv.handle(settings.owner_id, "Read Priya's latest email about lunch. Reply to me with only the two days she offers.", chat)
    await conv.wait()
    await runner.close()
    tracing._client.flush()

    out = {"reply": chat.sent, "trace_id": trace_ids[0] if trace_ids else None}
    api, steps = tracing._client.api, []
    for _ in range(12):          # the trace takes a few seconds to become readable
        await asyncio.sleep(5)
        try:
            got = api.observations.get_many(trace_id=trace_ids[0], limit=100, fields="core,basic,io")
            steps = (got.dict() if hasattr(got, "dict") else got).get("data") or []
            if len(steps) >= 2:
                break
        except Exception as e:
            out["waiting"] = f"{type(e).__name__}: {str(e)[:120]}"
    if not steps:
        out["error"] = "the trace could not be read back"
        print(json.dumps(out, indent=2, default=str))
        return 1
    try:
        scored = api.scores.get_many(trace_id=trace_ids[0])
        scores = (scored.dict() if hasattr(scored, "dict") else scored).get("data") or []
    except Exception:
        scores = []
    blob = json.dumps(steps, default=str)
    out.update({
        "steps": len(steps),
        "kinds": sorted({str(o.get("type")) for o in steps}),
        "names": sorted({str(o.get("name")) for o in steps})[:12],
        "scores": sorted(str(x.get("name")) for x in scores),
        "contains_a_secret_from_settings": any(x and x in blob for x in (*secrets, settings.langfuse_secret_key)),
        "contains_mail_text": any(t in blob for t in MAIL_TEXT),
        "notes_of_hidden_content": blob.count("characters read from the owner's accounts; not recorded"),
        "characters_in_trace": len(blob),
    })
    print(json.dumps(out, indent=2, default=str, ensure_ascii=False))
    tracing.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
