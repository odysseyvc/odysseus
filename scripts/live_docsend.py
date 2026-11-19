"""Live check of reading a DocSend link: a real cloud browser, the owner's real mailbox for the verification email.

Give it a DocSend link the owner may open. It gives the owner's address if the page asks, answers a verification
email if one is sent, reads every page and prints what came back. It files nothing and no model of the assistant runs.
The sender of the link is told who looked.

    .venv/Scripts/python scripts/live_docsend.py https://docsend.com/view/...
"""
from __future__ import annotations

import asyncio
import sys
import time
import warnings
from pathlib import Path

warnings.simplefilter("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus.apps import Apps, ComposioBackend  # noqa: E402
from odysseus.browser import BrowserUseBackend  # noqa: E402
from odysseus.config import load, make_clock  # noqa: E402
from odysseus.deckview import DeckViewer, docsend_link  # noqa: E402
from odysseus.journal import Journal  # noqa: E402


async def main() -> int:
    link = docsend_link(sys.argv[1] if len(sys.argv) > 1 else "")
    s = load()
    if not link or not s.browser_use_api_key or not s.composio_api_key:
        print("This needs a DocSend link, a browser key and a connected mailbox.")
        return 1
    journal = Journal(Path(f"var/live/docsend-{int(time.time())}") / "journal.jsonl")
    apps = Apps(ComposioBackend(s.composio_api_key, f"odysseus-{s.owner_id}"), journal, make_clock(s.tz), s.tz)

    async def address() -> str:
        return s.viewer_email or await apps.my_address()
    viewer = DeckViewer(BrowserUseBackend(s.browser_use_api_key, Path(s.data_dir) / "browser.json"), apps.messages_for_code, address, journal,
                        max(s.task_usd, 1.0))
    t = time.monotonic()
    view = await viewer.view(link)
    print(f"=== {time.monotonic() - t:.0f}s  ok={view.ok}  pages={view.pages}  verified={view.verified}  cost=${view.cost_usd:.3f} ===")
    print(view.text)
    print("\n=== what happened ===")
    for r in journal.read():
        if r["kind"] in ("tool", "error"):
            print(r["kind"], {k: v for k, v in r.items() if k not in ("ts", "kind")})
    return 0 if view.ok else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
