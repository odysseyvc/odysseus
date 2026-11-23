"""Register this program's /vobiz address with Vobiz, once, under a secret made here.

    python scripts/connect_vobiz.py

It needs VOBIZ_AUTH_ID and VOBIZ_AUTH_TOKEN in .env and ODYSSEUS_PUBLIC_URL (in .env, or the one in fly.toml). Vobiz
signs every delivery with a secret the subscriber chooses and never shows it again; this makes a random one, gives it
to Vobiz, and writes it into .env as VOBIZ_WEBHOOK_SECRET without printing it. Running it again replaces the secret,
at Vobiz and in .env. On a server, send the VOBIZ lines to the host's secret store afterwards.
"""
from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from odysseus.config import read_env_file  # noqa: E402
from odysseus.vobiz import base  # noqa: E402


def main() -> int:
    env_path = Path(".env")
    env = read_env_file(env_path)
    auth_id, token, public = env.get("VOBIZ_AUTH_ID", ""), env.get("VOBIZ_AUTH_TOKEN", ""), env.get("ODYSSEUS_PUBLIC_URL", "")
    if not public and Path("fly.toml").exists():
        found = re.search(r'ODYSSEUS_PUBLIC_URL\s*=\s*"(https://[^"]+)"', Path("fly.toml").read_text(encoding="utf-8"))
        public = found.group(1) if found else ""
    if not auth_id or not token or not public.startswith("https://"):
        print("Put VOBIZ_AUTH_ID and VOBIZ_AUTH_TOKEN in .env, and ODYSSEUS_PUBLIC_URL (https) in .env or fly.toml, first.")
        return 2
    target, secret = public.rstrip("/") + "/vobiz", secrets.token_urlsafe(32)
    r = httpx.post(f"{base(auth_id)}/Webhook/", json={"url": target, "secret": secret}, timeout=30,
                   headers={"X-Auth-ID": auth_id, "X-Auth-Token": token})
    if r.status_code >= 300:
        print("Vobiz did not register the address:", r.status_code, r.text[:200])
        return 1
    lines = [line for line in env_path.read_text(encoding="utf-8").splitlines() if not line.startswith("VOBIZ_WEBHOOK_SECRET=")]
    env_path.write_text("\n".join(lines + ["VOBIZ_WEBHOOK_SECRET=" + secret]) + "\n", encoding="utf-8")
    print("Registered", target, "with Vobiz. The signing secret is in .env as VOBIZ_WEBHOOK_SECRET.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
