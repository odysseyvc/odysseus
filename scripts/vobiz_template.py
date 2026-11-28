"""The approved opening line for WhatsApp: submit it for approval, or see whether it has been approved.

WhatsApp lets a business write freely to a person only for a day after that person last wrote. After that it accepts
one thing: a message approved in advance, called a template. The assistant uses one such line to reach someone who has
been silent (a reminder, a brief, the end of a long job) and then sends the message itself (odysseus/vobiz.py).

    python scripts/vobiz_template.py submit     sends the line for approval; approval is WhatsApp's, and takes minutes to a day
    python scripts/vobiz_template.py status     asks for the latest verdicts and prints the line's state

Once it is approved, set ODYSSEUS_WA_TEMPLATE to its name where the program runs. Each time the line opens a day, the
number's provider charges for a conversation. Prints states and counts only.
"""
from __future__ import annotations

import sys

import httpx
from dotenv import dotenv_values

from odysseus.vobiz import TEMPLATE_BODY, TEMPLATE_NAME, base


def main() -> int:
    env = dotenv_values(".env")
    auth, token, channel = env.get("VOBIZ_AUTH_ID", ""), env.get("VOBIZ_AUTH_TOKEN", ""), env.get("VOBIZ_CHANNEL_ID", "")
    if not (auth and token and channel) or len(sys.argv) != 2 or sys.argv[1] not in ("submit", "status"):
        print(__doc__)
        return 2
    url = f"{base(auth)}/Channel/{channel}/Template/"
    with httpx.Client(timeout=40, headers={"X-Auth-ID": auth, "X-Auth-Token": token}) as c:
        if sys.argv[1] == "submit":
            r = c.post(url, json={"name": TEMPLATE_NAME, "language": "en", "category": "UTILITY", "components": [
                {"type": "BODY", "text": TEMPLATE_BODY, "example": {"body_text": [["The weekly pipeline review is ready"]]}}]})
            body = r.json() if "json" in r.headers.get("content-type", "") else {}
            print("submitted:", r.status_code, "| state:", body.get("status"), "| refused because:" if r.status_code >= 300 else "", str(body.get("error") or "")[:300])
            return 0 if r.status_code < 300 else 1
        c.post(url + "Sync/")
        r = c.get(url)
        items = (r.json() or {}).get("items", []) if r.status_code < 300 else []
        mine = [t for t in items if t.get("name") == TEMPLATE_NAME]
        print("templates on the number:", len(items), "|", TEMPLATE_NAME + ":", [(t.get("status"), t.get("category"), t.get("language")) for t in mine] or "not there")
    return 0


if __name__ == "__main__":
    sys.exit(main())
