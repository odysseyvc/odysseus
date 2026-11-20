"""Who to ask a contact about, live: the real search and the real model, for a public figure named on the command line.

    python scripts/live_leads.py "Full Name" "Company" [main]

Runs once on the background model, or on the main model if the third word is "main". Reads nothing of the owner's.
Prints the suggestions as the owner would see them, the raw reply, the time and the cost.
"""
from __future__ import annotations

import asyncio
import sys
import time
import warnings
from dataclasses import replace
from pathlib import Path

warnings.simplefilter("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exa_py import Exa  # noqa: E402

from odysseus.config import load  # noqa: E402
from odysseus.journal import Journal, Ledger  # noqa: E402
from odysseus.leads import Leads, lines  # noqa: E402
from odysseus.memory import MemoryStore  # noqa: E402
from odysseus.routines import make_routine_runner  # noqa: E402
from odysseus.tools import make_tool_server  # noqa: E402


async def main() -> int:
    name, company = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""
    real = load()
    settings = replace(real, data_dir=Path(f"var/live/leads-{int(time.time())}"))
    journal = Journal(settings.data_dir / "journal.jsonl")
    memory = MemoryStore(settings.data_dir / "memory")
    model = settings.model if sys.argv[3:] == ["main"] else settings.background_model
    raw = []
    runner = make_routine_runner(settings, journal, make_tool_server(Exa(api_key=settings.exa_api_key).tools.web_search(), journal, memory, read_only=True),
                                 memory, program_tasks=True, model=model)

    async def run(prompt):
        out = await runner(prompt)
        raw.append(out[1])
        return out
    began = time.monotonic()
    found = await Leads(run, journal, Ledger(journal), 5.0).find(name, company)
    took = time.monotonic() - began
    await runner.runner.close()
    cost = sum(r.get("cost_usd", 0) for r in journal.read() if r["kind"] == "task_end")
    searches = sum(1 for r in journal.read() if r["kind"] == "tool" and r.get("name") == "web_search")
    print(f"model: {model}\n\n--- as the owner would see it ---\n{lines(found) or '(nothing)'}\n\n--- the raw reply ---\n{(raw or [''])[0][:1500]}")
    print(f"\n{len(found)} kept, {searches} searches, {took:.0f} s, ${cost:.3f}")
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
