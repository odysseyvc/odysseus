"""The assistant's own address, against the real mailbox service (mailbox.py). No model is called and nothing is spent.

  python scripts/live_mailbox.py make     make this person's inbox, as /address does, in the real data folder
  python scripts/live_mailbox.py check    look at what has arrived, without marking anything as seen

`check` prints the shape of what the service answers (field names, not contents), so that a difference from its
documentation shows. Send the address a message with an attachment first.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus import config  # noqa: E402
from odysseus.journal import Journal  # noqa: E402
from odysseus.links import Fetcher  # noqa: E402
from odysseus.mailbox import AgentMail, Mailbox, files_in, from_owner, address_of, question  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


async def nothing(*_: object) -> None:
    return None


async def main(what: str) -> int:
    settings = config.load()
    if not settings.agentmail_api_key:
        print("No AGENTMAIL_API_KEY in the settings.")
        return 2
    service = AgentMail(settings.agentmail_api_key)
    state_file = settings.data_dir / "mailbox.json"

    async def none() -> set:
        return set()
    box = Mailbox(service, state_file, Journal(settings.data_dir / "journal.jsonl"), none, nothing, nothing, nothing, label=f"odysseus-{settings.owner_id}")

    if what == "make":
        raw = await service.create(display_name="Odysseus", client_id=f"odysseus-{settings.owner_id}") if not box.address else {}
        if raw:
            print("create answered with fields:", sorted(raw))
        address = await box.make()
        check("an address is made and saved", "@" in address and box.inbox_id != "", address)
        again = await service.create(display_name="Odysseus", client_id=f"odysseus-{settings.owner_id}")
        check("asking again with the same label gives the same inbox, not a second one", str(again.get("inbox_id")) == box.inbox_id)
    else:
        if not box.inbox_id:
            print("No address yet: run `make` first.")
            return 2
        state = json.loads(state_file.read_text(encoding="utf-8"))
        rows = await service.messages(box.inbox_id, after=str(state.get("after") or ""))
        check("the list of messages can be read", isinstance(rows, list), f"{len(rows)} since the address was made")
        if not rows:
            print("Nothing has arrived yet.")
        for m in rows:
            print("\nmessage fields:", sorted(m))
            print("labels:", m.get("labels"), "| authentication_results:", m.get("authentication_results"), "| timestamp:", m.get("timestamp"))
            sender = address_of(m.get("from", ""))
            check("a listed message has an id, a sender and a time", bool(m.get("message_id") and sender and m.get("timestamp")))
            check("with the sender as the owner's address it counts as the owner's", from_owner(m, {sender}))
            check("with no owner address it is a stranger's", not from_owner(m, set()) and "Read it and deal with it?" in question(m, box.address))
            full = await service.message(box.inbox_id, m["message_id"])
            print("one message's fields:", sorted(full))
            body = str(full.get("extracted_text") or full.get("text") or "")
            check("the message's text can be read", bool(body.strip()), f"{len(body)} characters; extracted_text {'present' if full.get('extracted_text') else 'absent'}")
            files = files_in(full)
            print("attachment fields:", [sorted(a) for a in full.get("attachments") or []])
            check("attachments are listed on the message as on the list", len(files) == len(files_in(m)), f"{len(files)} attached")
            for a in files:
                got = await service.attachment(box.inbox_id, m["message_id"], str(a["attachment_id"]))
                print("attachment answer fields:", sorted(got))
                where = str(got.get("download_url") or "")
                check("an attachment comes with a secure link to fetch it from", where.startswith("https://"))
                if where.startswith("https://"):
                    content, _ = await Fetcher().fetch(where, pdf_only=False)
                    check("the attachment's bytes arrive whole", len(content) == int(a.get("size") or -1), f"{len(content)} bytes against {a.get('size')} listed")
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "check")))
