"""Make, look at, or delete the one WhatsApp group this program serves, through Meta's Groups API.

    python scripts/whatsapp_group.py subscribe ACCOUNT_ID   once: have Meta deliver this number's account to your own app
    python scripts/whatsapp_group.py create "Deal room"     ask Meta to make the group; the link arrives at the running program
    python scripts/whatsapp_group.py link GROUP_ID          the group's invite link
    python scripts/whatsapp_group.py info GROUP_ID          its subject and how many people are in it
    python scripts/whatsapp_group.py delete GROUP_ID        delete it, for everyone in it

It needs WHATSAPP_TOKEN and WHATSAPP_GROUP_PHONE_ID in .env. Meta opens this API only to a number with Official
Business Account status; for any other number every command here is refused (error 131215) and nothing is changed.
The app's subscription must also include the four group fields (group_lifecycle_update, group_participants_update,
group_settings_update, group_status_update) beside "messages"; add them where you registered the /whatsapp address.

A group is made by the number and joined by a link. The program learns the group from Meta's report that it was made,
which goes to the running program, and that program sends you the link. Written from Meta's documentation; not run
against Meta.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from odysseus.config import read_env_file  # noqa: E402
from odysseus.whatsapp import GRAPH  # noqa: E402


def main(argv: list[str]) -> int:
    env = read_env_file(Path(".env"))
    token, phone_id = env.get("WHATSAPP_TOKEN", ""), env.get("WHATSAPP_GROUP_PHONE_ID", "")
    if len(argv) < 2 or argv[0] not in ("subscribe", "create", "link", "info", "delete"):
        print(__doc__)
        return 2
    if not token or not phone_id:
        print("Put WHATSAPP_TOKEN and WHATSAPP_GROUP_PHONE_ID in .env first.")
        return 2
    what, arg = argv[0], argv[1]
    with httpx.Client(timeout=30, headers={"Authorization": f"Bearer {token}"}) as http:
        if what == "subscribe":
            r = http.post(f"{GRAPH}/{arg}/subscribed_apps")
        elif what == "create":
            r = http.post(f"{GRAPH}/{phone_id}/groups", json={"messaging_product": "whatsapp", "subject": arg[:128]})
        elif what == "link":
            r = http.get(f"{GRAPH}/{arg}/invite_link")
        elif what == "info":
            r = http.get(f"{GRAPH}/{arg}", params={"fields": "subject,suspended,total_participant_count,join_approval_mode"})
        else:
            if input(f"Delete the group {arg} for everyone in it? Type yes: ").strip().lower() != "yes":
                print("Left as it is.")
                return 0
            r = http.delete(f"{GRAPH}/{arg}")
    print(r.status_code, r.text[:500])
    if r.status_code >= 300 and "131215" in r.text:
        print("Meta has not opened its Groups API to this number. It needs Official Business Account status.")
    elif r.status_code < 300 and what == "create":
        print("Asked. Meta reports the group, and its link, to the running program, which sends the link to you.")
    return 0 if r.status_code < 300 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
