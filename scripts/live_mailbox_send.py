"""Can the key in the settings send from the assistant's own address? One message, from the inbox to itself.

  python scripts/live_mailbox_send.py

Prints whether the service accepted it, and nothing of the address, the key or the message ids. The message arrives in
the same inbox a few seconds later, so the running assistant will mention it as new mail.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus import config  # noqa: E402
from odysseus.mailbox import AgentMail, MailboxError  # noqa: E402


async def main() -> int:
    settings = config.load()
    if not settings.agentmail_api_key:
        print("FAIL no key in the settings")
        return 1
    try:
        state = json.loads((settings.data_dir / "mailbox.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("FAIL no inbox has been made yet: send /address first")
        return 1
    service = AgentMail(settings.agentmail_api_key)
    try:
        sent = await service.send(state["inbox_id"], [state["email"]], [], "A check that this address can send",
                                  "Sent by scripts/live_mailbox_send.py to the address it came from. Nothing to do.")
    except MailboxError as e:
        print(f"FAIL the service refused: {e}")
        return 1
    print("PASS sent" + ("  [message id and thread id returned]" if sent.get("message_id") and sent.get("thread_id") else f"  [fields: {sorted(sent)}]"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
