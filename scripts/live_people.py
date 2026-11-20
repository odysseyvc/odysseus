"""More than one person on one copy, live: two invented people join by invitation and each talks to the real model.

    python scripts/live_people.py

The real start-up code, the real model and the real connector, in a folder made for this run. The people are invented
Telegram ids and the chat is a stand-in, so nothing is sent to anyone. Checks that each person gets sign-in links of
their own, that what one tells the assistant the other cannot get back, and what a person costs in time and money.
"""
from __future__ import annotations

import asyncio
import sys
import time
import warnings
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

warnings.simplefilter("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exa_py import Exa  # noqa: E402

import odysseus.__main__ as entry  # noqa: E402
from odysseus.config import load  # noqa: E402
from odysseus.people import WELCOME, House, People  # noqa: E402
from odysseus.review import anthropic_call  # noqa: E402
from odysseus.tracing import Tracing  # noqa: E402

HOST, A, B = 900000000, 900000001, 900000002
SECRET = "persimmon-7741"


class Bot:
    async def send_message(self, *a, **k):
        pass


class Chat:
    def __init__(self, who=""):
        self.who, self.sent, self.buttons = who, [], []

    async def send(self, text):
        self.sent.append(text)

    async def draft(self, draft_id, text):
        pass

    async def ask(self, text, approval_id, thread=False):
        self.sent.append("[asked] " + text)

    async def choose(self, text, offer, options):
        self.sent.append("[buttons] " + text)

    async def links(self, text, links):
        self.buttons.append((text, links))


async def say(house, uid, text) -> tuple[str, float]:
    chat = Chat()
    began = time.monotonic()
    await house.handle(uid, text, chat)
    await house._at[uid].conversation.wait()
    return "\n".join(chat.sent), time.monotonic() - began


async def main() -> int:
    real = load()
    root = Path(f"var/live/people-{int(time.time())}")
    settings = replace(real, owner_id=HOST, data_dir=root, background_jobs=1, meeting_briefs=False, checkins_at="off", capture_at="off",
                       whatsapp_token="", langfuse_public_key="", langfuse_secret_key="")
    shared = SimpleNamespace(bot=Bot(), tracing=Tracing("", "", ""), second_call=anthropic_call(settings.anthropic_api_key),
                             search=Exa(api_key=settings.exa_api_key).tools.web_search(), restart=lambda: None)
    host = await entry.assemble(settings, shared, False)
    people = People(root / "people.json")
    built = []

    async def assemble(uid):
        began = time.monotonic()
        a = await entry.assemble(entry.member_settings(settings, uid), shared, False)
        built.append(round(time.monotonic() - began, 1))
        return a

    async def link(code):
        return f"https://t.me/example_bot?start={code}"
    house = House(HOST, people, host.journal, assemble, link, most=settings.people_max)
    house.add(HOST, host)
    ok = []

    def check(name, passed, detail=""):
        ok.append(passed)
        print(f"{'PASS' if passed else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")

    # 1. Two invitations, two people.
    joined = {}
    for uid, name in ((A, "Tara Example"), (B, "Sam Example")):
        chat = Chat()
        await house.handle(HOST, f"/invite {name}", chat)
        code = chat.sent[-1].rsplit("start=", 1)[1]
        joined[uid] = Chat(name)
        began = time.monotonic()
        await house.handle(uid, f"/start {code}", joined[uid])
        joined[uid].took = time.monotonic() - began
    a, b = joined[A], joined[B]
    check("each newcomer is welcomed", a.sent[:1] == [WELCOME] and b.sent[:1] == [WELCOME])
    links_a = [u for _, ls in a.buttons for _, u in ls]
    links_b = [u for _, ls in b.buttons for _, u in ls]
    check("each gets two sign-in links from the real connector, and not the same ones",
          len(links_a) == 2 and len(links_b) == 2 and not set(links_a) & set(links_b) and all(u.startswith("https://") for u in links_a + links_b),
          f"link hosts: {sorted({urlsplit(u).hostname for u in links_a + links_b})}; joining took {a.took:.1f} s and {b.took:.1f} s; building an assistant {built} s")

    # 2. One person tells it something. The other asks for it.
    told, t_first = await say(house, A, f"Remember this about me: my favourite fruit is the {SECRET}.")
    print(f"\nA, first message ({t_first:.0f} s):\n{told[:400]}\n")
    asked_b, t_b = await say(house, B, "What is my favourite fruit? Tell me everything you remember about me.")
    print(f"B, first message ({t_b:.0f} s):\n{asked_b[:400]}\n")
    asked_a, t_second = await say(house, A, "What is my favourite fruit?")
    print(f"A, second message ({t_second:.0f} s):\n{asked_a[:300]}\n")
    check("the person who said it gets it back", "7741" in asked_a)
    check("the other person does not", "7741" not in asked_b and "persimmon" not in asked_b.lower())
    b_files = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in (root / "people" / str(B)).rglob("*") if p.is_file())
    host_files = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in root.glob("*") if p.is_file())
    check("nothing of it is anywhere in the other person's folder, or the host's", "7741" not in b_files and "7741" not in host_files)
    a_memory = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in (root / "people" / str(A) / "memory").rglob("*") if p.is_file())
    check("it is in the first person's own memory folder", "7741" in a_memory)

    # 3. The host's view, and what it cost.
    chat = Chat()
    await house.handle(HOST, "/people", chat)
    print(chat.sent[-1], "\n")
    spent = {uid: house._at[uid].ledger.spent_today() for uid in (HOST, A, B)}
    check("each person's spending is counted apart", spent[A] > 0 and spent[B] > 0 and spent[HOST] == 0, f"A ${spent[A]:.3f}, B ${spent[B]:.3f}, host ${spent[HOST]:.3f}")

    # 4. Removing a person.
    await house.handle(HOST, f"/remove {B}", chat)
    gone = Chat()
    await house.handle(B, "hello again", gone)
    check("a removed person gets no reply, and their folder is left", gone.sent == [] and (root / "people" / str(B)).exists())
    await house.close()
    print(f"\n{sum(ok)} of {len(ok)} checks passed. Model cost: ${sum(spent.values()):.3f}. First reply {t_first:.0f} s (connects the agent), later reply {t_second:.0f} s.")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
