"""Live check of sending on WhatsApp: real messages from the bot's number to the owner's own phone.

    .venv/Scripts/python scripts/live_whatsapp_send.py template    Meta's sample template; works at any time
    .venv/Scripts/python scripts/live_whatsapp_send.py text        an ordinary message, sent by the bot's own code
    .venv/Scripts/python scripts/live_whatsapp_send.py ask         an approval question with Approve and Decline buttons

"text" and "ask" are refused by WhatsApp unless the owner has written to the bot's number
in the last 24 hours. Nothing is printed that identifies the owner or the token.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus.config import load  # noqa: E402
from odysseus.journal import Journal  # noqa: E402
from odysseus.whatsapp import GRAPH, WhatsAppChat, digits, make_http  # noqa: E402


async def main(what: str) -> int:
    s = load()
    if not (s.whatsapp_token and s.whatsapp_phone_id and s.owner_whatsapp):
        print("WhatsApp is not set up: the token, phone number id or owner's number is missing.")
        return 2
    http, close = make_http(s.whatsapp_token)
    journal = Journal(Path(tempfile.mkdtemp()) / "journal.jsonl")
    chat = WhatsAppChat(http, s.whatsapp_phone_id, digits(s.owner_whatsapp), journal)
    out: dict = {"what": what}
    try:
        if what == "template":
            status, data = await http("POST", f"{GRAPH}/{s.whatsapp_phone_id}/messages", {
                "messaging_product": "whatsapp", "to": digits(s.owner_whatsapp), "type": "template",
                "template": {"name": "hello_world", "language": {"code": "en_US"}}})
            out.update(status=status, accepted=bool(isinstance(data, dict) and data.get("messages")),
                       error=(data.get("error") or {}).get("message") if isinstance(data, dict) else None,
                       code=(data.get("error") or {}).get("code") if isinstance(data, dict) else None)
        elif what == "text":
            await chat.send("Odysseus here, on WhatsApp. This message was sent by the bot's own code. Nothing will happen if you reply yet: "
                            "replies need the server to be reachable.")
            out["sent"] = True
        elif what == "ask":
            await chat.ask("This is a test of the approval buttons. Nothing is waiting behind them and tapping either does nothing yet.\n\n"
                           "Send this email from your account?\n\nTo: priya@example.com\nSubject: Lunch\n\nFriday at 1 works for me.", "test0000")
            out["sent"] = True
    except Exception as e:
        out.update(sent=False, error=str(e), log=[{k: r.get(k) for k in ("status", "message")} for r in journal.read() if r["kind"] == "error"])
    await close()
    print(json.dumps(out, indent=2)[:1200])
    return 0 if out.get("accepted") or out.get("sent") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "template")))
