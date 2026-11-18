"""Check the WhatsApp door on this machine, without Meta and without a model.

Starts the real listener on a local port with a stand-in conversation, then calls it
over HTTP the way Meta would: the handshake with the right and the wrong phrase, and a
delivery with a good signature, a bad one and none. Nothing leaves the machine.

    .venv/Scripts/python scripts/live_whatsapp_door.py
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from odysseus.journal import Journal  # noqa: E402
from odysseus.whatsapp import WhatsApp, serve  # noqa: E402

SECRET, PHRASE, OWNER, PORT = "local-check-secret", "local-check-phrase", "919820012345", 8791


class Conversation:
    def __init__(self):
        self.heard = []

    async def handle(self, user_id, text, chat):
        self.heard.append(text)
        await chat.send("heard: " + text)


async def main() -> int:
    sent, conv = [], Conversation()

    async def http(method, url, body):
        sent.append(body)
        return 200, {}
    wa = WhatsApp(conv, Journal(Path(tempfile.mkdtemp()) / "journal.jsonl"), http, phone_id="1", owner_number=OWNER, app_secret=SECRET,
                  verify_token=PHRASE, owner_id=1)
    door = await serve(wa, PORT)
    base = f"http://127.0.0.1:{PORT}/whatsapp"
    raw = json.dumps({"entry": [{"changes": [{"value": {"messages": [{"from": OWNER, "id": "wamid.x", "type": "text", "text": {"body": "hello"}}]}}]}]}).encode()
    good = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    out = {}
    async with httpx.AsyncClient() as c:
        r = await c.get(base, params={"hub.mode": "subscribe", "hub.verify_token": PHRASE, "hub.challenge": "424242"})
        out["handshake, right phrase"] = (r.status_code, r.text)
        out["handshake, wrong phrase"] = (await c.get(base, params={"hub.mode": "subscribe", "hub.verify_token": "guess", "hub.challenge": "1"})).status_code
        out["delivery, no signature"] = (await c.post(base, content=raw)).status_code
        out["delivery, wrong signature"] = (await c.post(base, content=raw, headers={"X-Hub-Signature-256": "sha256=" + "0" * 64})).status_code
        out["delivery, good signature"] = (await c.post(base, content=raw, headers={"X-Hub-Signature-256": good})).status_code
        out["another path"] = (await c.get(f"http://127.0.0.1:{PORT}/")).status_code
    await wa.wait()
    out["heard by the conversation"] = conv.heard
    out["sent back"] = [m["text"]["body"] for m in sent]
    await door.cleanup()
    print(json.dumps(out, indent=2))
    ok = (out["handshake, right phrase"] == (200, "424242") and out["handshake, wrong phrase"] == 403 and out["delivery, no signature"] == 403
          and out["delivery, wrong signature"] == 403 and out["delivery, good signature"] == 200 and conv.heard == ["hello"])
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
