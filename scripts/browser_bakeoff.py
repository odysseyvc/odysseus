"""Which vendor keeps a browser signed in? A bake-off, run the same way on each.

    python scripts/browser_bakeoff.py status                    which vendors have a key, which have a saved sign-in
    python scripts/browser_bakeoff.py signin <vendor>           opens that vendor's browser; you sign in by hand; press Enter when done
    python scripts/browser_bakeoff.py open <vendor>             the same in two steps: opens it with the sign-in pages loaded and prints the link
    python scripts/browser_bakeoff.py close <vendor>            ...and saves the sign-ins once you have finished
    python scripts/browser_bakeoff.py check [vendor ...]        opens each site in each vendor's saved browser and records what it sees
    python scripts/browser_bakeoff.py report                    the table so far

Vendors: browseruse (the one the assistant uses today), browserbase, kernel. Keys in .env: BROWSER_USE_API_KEY,
BROWSERBASE_API_KEY (and BROWSERBASE_PROJECT_ID if the account has more than one project), KERNEL_API_KEY.

The question is whether a sign-in survives: for hours, across days, on sites that look for automation. So the check
is made by plain code, the same on every vendor, with no model in it: open the saved browser, go to an address that
only a signed-in person can see, and record where the browser ended up and whether the page shows a sign-in form.
Run `check` straight after signing in, the next morning, and after a week, and the table shows which sign-ins lasted.

It only ever opens pages and reads their address, title and whether they ask for a sign-in. It clicks nothing, types
nothing and changes nothing. Passwords are typed by the owner, by hand, in the vendor's own live view; nothing
here sees them. Results go to private/bakeoff/browsers.jsonl, which holds sites and outcomes and no page content.

The two extra libraries (browserbase, kernel) are for this script only and are not in requirements.txt.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus.config import load  # noqa: E402

OUT = Path("private/bakeoff/browsers.jsonl")
STATE = Path("private/bakeoff/browsers-state.json")       # each vendor's profile or context id; no secrets
NAME = "odysseus-bakeoff"
# An address only a signed-in person sees, and what the browser's address looks like when it is turned away instead.
# Add your own (a paid newsletter, a data site) to private/bakeoff/sites.json as {"name": ["address", "turned-away pattern"]}.
SITES: dict[str, tuple[str, str]] = {
    "docsend": ("https://docsend.com/documents", r"/login|/users/sign_in|/signin"),
    "attio": ("https://app.attio.com/", r"/welcome|/sign-in|/login|auth\."),
    "linkedin": ("https://www.linkedin.com/feed/", r"/login|/uas/|/checkpoint|/authwall|/signup"),
}
ASKS_FOR_SIGN_IN = "() => !!document.querySelector('input[type=password]')"


def sites() -> dict[str, tuple[str, str]]:
    extra = Path("private/bakeoff/sites.json")
    more = json.loads(extra.read_text(encoding="utf-8")) if extra.exists() else {}
    return {**SITES, **{k: (v[0], v[1]) for k, v in more.items()}}


def state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def remember(vendor: str, **values) -> None:
    s = state()
    s[vendor] = {**s.get(vendor, {}), **values}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=1), encoding="utf-8")


def env(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    try:
        for line in Path(".env").read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(name + "="):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


# --- one small adapter a vendor: open the saved browser, say where to connect and where to watch, close it so it saves ---

class BrowserUse:
    key = "BROWSER_USE_API_KEY"

    def __init__(self):
        from browser_use_sdk import BrowserUse as Client
        self.c = Client(api_key=env(self.key))

    def open(self) -> tuple[str, str, str]:
        """(id, address to connect a program to, address for a person to watch and take over)"""
        profile = state().get("browseruse", {}).get("profile")
        if not profile:
            profile = str(self.c.profiles.create(name=NAME).id)
            remember("browseruse", profile=profile)
        b = self.c.browsers.create(profile_id=profile, timeout=20)
        return str(b.id), b.cdp_url or "", b.live_url or ""

    def close(self, browser_id: str) -> None:
        self.c.browsers.stop(browser_id)        # stopping is what saves the sign-ins to the profile


class Browserbase:
    key = "BROWSERBASE_API_KEY"

    def __init__(self):
        from browserbase import Browserbase as Client
        self.c = Client(api_key=env(self.key))
        self.project = env("BROWSERBASE_PROJECT_ID") or None

    def open(self) -> tuple[str, str, str]:
        context = state().get("browserbase", {}).get("context")
        if not context:
            context = self.c.contexts.create(**({"project_id": self.project} if self.project else {})).id
            remember("browserbase", context=context)
        s = self.c.sessions.create(browser_settings={"context": {"id": context, "persist": True}}, keep_alive=True,
                                   **({"project_id": self.project} if self.project else {}))
        return s.id, s.connect_url, self.c.sessions.debug(s.id).debugger_fullscreen_url

    def close(self, session_id: str) -> None:
        self.c.sessions.update(session_id, status="REQUEST_RELEASE", **({"project_id": self.project} if self.project else {}))
        time.sleep(5)       # the vendor asks for a few seconds before a context is used again


class Kernel:
    key = "KERNEL_API_KEY"

    def __init__(self):
        from kernel import Kernel as Client
        self.c = Client(api_key=env(self.key))

    def open(self) -> tuple[str, str, str]:
        if not state().get("kernel", {}).get("profile"):
            try:
                self.c.profiles.create(name=NAME)
            except Exception as e:      # it already exists
                if "onflict" not in type(e).__name__ and "409" not in str(e):
                    raise
            remember("kernel", profile=NAME)
        b = self.c.browsers.create(profile={"name": NAME, "save_changes": True}, stealth=True, timeout_seconds=1200)
        return b.session_id, b.cdp_ws_url, b.browser_live_view_url or ""

    def close(self, session_id: str) -> None:
        self.c.browsers.delete_by_id(session_id)        # deleting the browser is what saves the profile


VENDORS = {"browseruse": BrowserUse, "browserbase": Browserbase, "kernel": Kernel}


def ready(vendor: str) -> bool:
    return bool(env(VENDORS[vendor].key))


# --- the same check on every vendor, by code ------------------------------------------------------------------------

async def look(cdp: str, where: str, turned_away: str) -> dict:
    """Open one address in the connected browser and say what happened. Reads the address, the title and one yes or no."""
    from playwright.async_api import async_playwright
    began = time.monotonic()
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp, timeout=60000)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()
        try:
            await page.goto(where, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(4000)                   # redirects to a sign-in page happen after the first load
            ended, title = page.url, (await page.title())[:80]
            asks = bool(await page.evaluate(ASKS_FOR_SIGN_IN))
            blocked = bool(re.search(r"(?i)captcha|are you a human|unusual activity|verify you are|access denied|security check", title + " " + ended))
            path = re.sub(r"[?#].*", "", ended)
            signed_in = not asks and not blocked and not re.search(turned_away, path, re.I)
            outcome = "signed in" if signed_in else "blocked" if blocked else "asked to sign in"
        except Exception as e:
            ended, title, outcome = "", "", f"error: {type(e).__name__}"
        finally:
            await page.close()
    host_and_path = re.sub(r"^https?://", "", re.sub(r"[?#].*", "", ended))[:80]
    return {"outcome": outcome, "ended_at": host_and_path, "seconds": round(time.monotonic() - began, 1)}


def check(vendor: str) -> list[dict]:
    v = VENDORS[vendor]()
    began = time.monotonic()
    browser_id, cdp, _ = v.open()
    rows = []
    try:
        for site, (where, turned_away) in sites().items():
            r = asyncio.run(look(cdp, where, turned_away))
            rows.append({"ts": round(time.time()), "vendor": vendor, "site": site, **r})
            print(f"  {vendor:12} {site:10} {r['outcome']:18} {r['seconds']:>5}s  {r['ended_at']}")
    finally:
        v.close(browser_id)
    for r in rows:
        r["browser_seconds"] = round(time.monotonic() - began)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return rows


def signin(vendor: str) -> None:
    v = VENDORS[vendor]()
    browser_id, _, live = v.open()
    remember(vendor, signed_in_at=round(time.time()))
    print(f"\nOpen this address, and sign in by hand to: {', '.join(sites())}.\nTreat it like a password: anyone who has it can control that browser.\n\n{live}\n")
    try:
        input("Press Enter here when you have signed in to all of them, and the sign-ins will be saved... ")
    finally:
        v.close(browser_id)
    print("Saved. Now run: python scripts/browser_bakeoff.py check", vendor)


SIGN_IN_PAGES = {"docsend": "https://docsend.com/login", "attio": "https://app.attio.com/", "linkedin": "https://www.linkedin.com/login"}


async def _load(cdp: str) -> None:
    """Put each site's sign-in page in a tab of its own, so that all that is left for the owner is to sign in."""
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp, timeout=60000)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        for i, site in enumerate(sites()):
            page = context.pages[0] if i == 0 and context.pages else await context.new_page()
            try:
                await page.goto(SIGN_IN_PAGES.get(site, sites()[site][0]), wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass


def open_for_sign_in(vendor: str) -> str:
    v = VENDORS[vendor]()
    browser_id, cdp, live = v.open()
    remember(vendor, open_browser=browser_id)
    asyncio.run(_load(cdp))
    return live


def close_after_sign_in(vendor: str) -> bool:
    browser_id = state().get(vendor, {}).get("open_browser")
    if not browser_id:
        return False
    VENDORS[vendor]().close(browser_id)
    remember(vendor, open_browser=None, signed_in_at=round(time.time()))
    return True


def report() -> None:
    rows = [json.loads(line) for line in OUT.read_text(encoding="utf-8").splitlines()] if OUT.exists() else []
    if not rows:
        print("Nothing recorded yet.")
        return
    s = state()
    print(f"{'vendor':12} {'site':10} {'checks':>6} {'signed in':>9}  last outcome (hours since you signed in)")
    for vendor in VENDORS:
        since = s.get(vendor, {}).get("signed_in_at")
        for site in sites():
            mine = [r for r in rows if r["vendor"] == vendor and r["site"] == site]
            if mine:
                last = mine[-1]
                age = f"{(last['ts'] - since) / 3600:.0f}h" if since else "?"
                print(f"{vendor:12} {site:10} {len(mine):>6} {sum(r['outcome'] == 'signed in' for r in mine):>9}  {last['outcome']} ({age})")


def main() -> int:
    load()
    args = sys.argv[1:] or ["status"]
    if args[0] == "status":
        for vendor in VENDORS:
            print(f"{vendor:12} key: {'yes' if ready(vendor) else 'NO (' + VENDORS[vendor].key + ' in .env)':34} saved sign-in: {'yes' if state().get(vendor, {}).get('signed_in_at') else 'not yet'}")
        print("sites:", ", ".join(sites()))
    elif args[0] == "signin" and len(args) == 2 and args[1] in VENDORS:
        signin(args[1])
    elif args[0] == "open" and len(args) == 2 and args[1] in VENDORS:
        print(open_for_sign_in(args[1]))
    elif args[0] == "close" and len(args) == 2 and args[1] in VENDORS:
        print("Saved." if close_after_sign_in(args[1]) else "No browser was open for sign-in.")
    elif args[0] == "check":
        for vendor in (args[1:] or [v for v in VENDORS if ready(v) and state().get(v, {}).get("signed_in_at")]):
            check(vendor)
    elif args[0] == "report":
        report()
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
