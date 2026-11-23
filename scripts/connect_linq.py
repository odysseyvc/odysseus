"""Register this program's /linq address with Linq, once, and keep the signing secret Linq returns.

    python scripts/connect_linq.py            register, and write LINQ_WEBHOOK_SECRET into .env
    python scripts/connect_linq.py --check    only list what is registered

It needs LINQ_API_TOKEN in .env and ODYSSEUS_PUBLIC_URL (in .env, or the one in fly.toml). Linq shows the signing
secret once, when the subscription is made; this writes it straight into .env and never prints it. On a server, send
the four LINQ lines and OWNER_IMESSAGE to the host's secret store afterwards.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from odysseus.config import read_env_file  # noqa: E402
from odysseus.linq import API, EVENTS, WEBHOOK_VERSION  # noqa: E402


def main() -> int:
    env_path = Path(".env")
    env = read_env_file(env_path)
    token, public = env.get("LINQ_API_TOKEN", ""), env.get("ODYSSEUS_PUBLIC_URL", "")
    if not public and Path("fly.toml").exists():
        found = re.search(r'ODYSSEUS_PUBLIC_URL\s*=\s*"(https://[^"]+)"', Path("fly.toml").read_text(encoding="utf-8"))
        public = found.group(1) if found else ""
    if not token or not public.startswith("https://"):
        print("Put LINQ_API_TOKEN in .env, and ODYSSEUS_PUBLIC_URL (https) in .env or fly.toml, first.")
        return 2
    target = f"{public.rstrip('/')}/linq?version={WEBHOOK_VERSION}"
    with httpx.Client(timeout=30, headers={"Authorization": f"Bearer {token}"}) as http:
        r = http.get(f"{API}/webhook-subscriptions")
        rows = (r.json().get("subscriptions") or r.json().get("data") or []) if r.status_code == 200 and isinstance(r.json(), dict) else []
        for row in rows:
            print("registered:", row.get("target_url"), "| active:", row.get("is_active"), "| events:", ", ".join(row.get("subscribed_events") or []))
        if r.status_code != 200:
            print("Linq did not list the subscriptions:", r.status_code)
            return 1
        if "--check" in sys.argv:
            return 0
        if any(row.get("target_url") == target for row in rows):
            print("This address is already registered. Its secret was shown when it was made; if it is lost, delete the "
                  "subscription in Linq's dashboard and run this again.")
            return 0
        r = http.post(f"{API}/webhook-subscriptions", json={"target_url": target, "subscribed_events": list(EVENTS)})
        secret = r.json().get("signing_secret") if r.status_code < 300 and isinstance(r.json(), dict) else None
        if not secret:
            print("Linq did not register the address:", r.status_code, str(r.text)[:200])
            return 1
    lines = [line for line in env_path.read_text(encoding="utf-8").splitlines() if not line.startswith("LINQ_WEBHOOK_SECRET=")]
    env_path.write_text("\n".join(lines + ["LINQ_WEBHOOK_SECRET=" + secret]) + "\n", encoding="utf-8")
    print("Registered", target, "for", ", ".join(EVENTS) + ". The signing secret is in .env as LINQ_WEBHOOK_SECRET.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
