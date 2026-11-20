"""Does the firm's database accept a sign-in that comes back to a public page of this program's, live?

    python scripts/live_public_sign_in.py

Asks Attio's real server to register this program with an https return address and to issue a sign-in link, in a
folder made for this run. Nobody opens the link, so nothing is connected. Then sends the door an address with the
wrong mark, as a stranger could, and checks that the sign-in is still waiting and nothing was saved.
"""
from __future__ import annotations

import asyncio
import sys
import time
import warnings
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

warnings.simplefilter("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus.connect import Connector  # noqa: E402
from odysseus.door import Door  # noqa: E402
from odysseus.journal import Journal  # noqa: E402

PUBLIC = "https://odysseus-example.fly.dev"


async def main() -> int:
    root = Path(f"var/live/public-sign-in-{int(time.time())}")
    root.mkdir(parents=True)
    door, sent, rebuilt, ok = Door(), [], [], []

    async def send(text):
        sent.append(text)

    def check(name, passed, detail=""):
        ok.append(passed)
        print(f"{'PASS' if passed else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    c = Connector(root, Journal(root / "j.jsonl"), lambda: rebuilt.append(1), remote=True, public_url=PUBLIC, door=door, timeout_s=60)
    await c.connect("attio", send)
    for _ in range(100):
        if sent or c._task.done():
            break
        await asyncio.sleep(0.2)
    link = next((w for w in (sent[0].split() if sent else []) if w.startswith("https://")), "")
    query = parse_qs(urlsplit(link).query)
    check("Attio's server registered the program and issued a link", bool(link), f"host {urlsplit(link).hostname}" if link else (sent[-1] if sent else "nothing sent"))
    check("the link returns to the public page, with a mark and a proof key", query.get("redirect_uri") == [PUBLIC + "/callback"]
          and len((query.get("state") or [""])[0]) >= 20 and bool(query.get("code_challenge")), f"scope asked: {(query.get('scope') or ['?'])[0]}")
    check("the person is told they will see a page saying Connected", bool(sent) and "says Connected" in sent[0] and "does not load" not in sent[0])
    check("an address with another mark is refused, and the sign-in still waits", door.came_back({"code": "x", "state": "not-the-mark"}) is False and not c._task.done())
    c._task.cancel()
    await asyncio.gather(c._task, return_exceptions=True)
    check("nothing was connected or rebuilt", rebuilt == [] and not (root / "attio.json").exists() or "tokens" not in (root / "attio.json").read_text(encoding="utf-8"))
    print(f"\n{sum(ok)} of {len(ok)} checks passed. Not covered: a person approving, so a real code has not been exchanged through the page.")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
