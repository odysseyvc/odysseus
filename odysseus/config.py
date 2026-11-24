"""Settings, read from the environment (and from a .env file if one exists).

Secrets live only here and in the process environment. They are never placed in
anything the model reads.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    owner_id: int
    anthropic_api_key: str
    exa_api_key: str
    composio_api_key: str = ""   # optional: without it, mail and calendar are simply not there
    browser_use_api_key: str = ""  # optional: without it, there is no browser
    browser_ask_signed_in: bool = True    # ask the owner before a browser run that is signed in as them; 0 turns it off
    background_jobs: int = 2              # how many jobs may run in the background at once (a deck sent by itself, a filing you approved) while you go on talking; 0: one thing at a time
    suggest_people: bool = True           # with a check-in that is due, and on request: up to three people to ask that person about, from a web search. 0: off
    checkins_at: str = "09:30"            # when each working day to ask about check-ins that are due and remind colleagues; "off": never
    capture_at: str = "08:30"             # when each day to ask about people met or heard from who are not in the database; "off": never
    pipeline_review_at: str = "mon 08:00"   # the weekday and time the pipeline review is sent (pipeline.py); "off": only when asked for
    updates_at: str = "08:45"             # when each day to look for updates from the firm's companies and record their figures (portfolio.py); "off": never
    meeting_briefs: bool = True           # a short brief before each meeting with someone outside the firm; 0 turns it off
    viewer_email: str = ""                # the address given to a document site that asks who is viewing. Empty: the connected mailbox's
    pages_model: str = ""                 # the model that looks at a PDF's pages (pages.py). Empty: the main model
    pages_check_model: str = ""           # the model that reads a document's pages against your request before it is sent (pagecheck.py). Empty: the background model
    review_model: str = ""                # the model that reads an email or invitation again before you are asked (review.py). Empty: the background model. "off": no second reading
    ask_every_change: bool = False        # 1: ask before every change, as before 19 Nov 2026. Default: small changes run and are reported
    mistral_api_key: str = ""    # optional: without it, files the owner sends cannot be read
    agentmail_api_key: str = ""  # optional: with it, each person can give the assistant an email address of its own (mailbox.py)
    e2b_api_key: str = ""        # optional: with it, the assistant can run code on a throwaway machine (sandbox.py)
    sandbox_template: str = ""   # optional: the name of an image of our own for that machine, which can make decks and PDFs (scripts/build_sandbox.py)
    owner_emails: tuple = ()     # the host's other addresses, besides the connected mailbox's: mail from these to the assistant is the host's own
    # Optional: WhatsApp as a second channel (whatsapp.py). All four of the first five are needed for it to switch on.
    whatsapp_token: str = ""          # a permanent access token for the Meta app
    whatsapp_phone_id: str = ""       # the id of the WhatsApp number the bot sends from
    whatsapp_app_secret: str = ""     # the Meta app's secret, which signs every delivery
    whatsapp_verify_token: str = ""   # any phrase you choose; Meta repeats it once when the address is registered
    owner_whatsapp: str = ""          # your own WhatsApp number with country code; only this number is listened to
    whatsapp_port: int = 8080         # the port the program listens on, for Meta's deliveries and for sign-ins coming back
    public_url: str = ""              # https address at which that port is reachable from the internet, if any (door.py)
    notify_on: str = "telegram"       # where reminders and routines are sent: telegram or whatsapp
    # Optional: iMessage as a third channel, bought from Linq (linq.py). All four are needed, and `imessage` must not be off.
    linq_token: str = ""              # the Linq account's API token
    linq_number: str = ""             # the Linq line the assistant writes from, with country code
    linq_secret: str = ""             # the signing secret of the webhook subscription, which signs every delivery
    owner_imessage: str = ""          # your own number as iMessage knows it; only this number is listened to
    imessage: bool = True             # ODYSSEUS_IMESSAGE=off switches the channel off and leaves its settings in place
    # Optional: WhatsApp by a second route, through Vobiz and a number rented from it (vobiz.py). All five are needed,
    # with owner_whatsapp above, and `vobiz` must not be off. The direct route above is separate and may be on as well.
    vobiz_auth_id: str = ""           # the Vobiz account's id
    vobiz_auth_token: str = ""        # and its token
    vobiz_channel_id: str = ""        # the WhatsApp channel made in Vobiz for the number
    vobiz_number: str = ""            # the WhatsApp number rented from Vobiz, with country code
    vobiz_secret: str = ""            # a secret of your own choosing, given to Vobiz when the address is registered; it signs every delivery
    vobiz: bool = True                # ODYSSEUS_VOBIZ=off switches this route off and leaves its settings in place
    langfuse_public_key: str = ""   # optional: with both Langfuse keys, every task is also recorded as a trace (tracing.py)
    langfuse_secret_key: str = ""
    langfuse_base_url: str = ""     # the Langfuse region or your own server; empty means Langfuse's default
    langfuse_full_content: bool = False   # also record what mail, calendar and app tools returned (off: only its length)
    model: str = "claude-sonnet-5-5"
    background_model: str = "claude-haiku-5-5"   # runs scheduled routines
    voice_model: str = "voxtral-small-transcribe-3"   # turns voice notes into text; uses the Mistral key
    speak: str = "voice"         # voice: a reply to a voice note is also spoken. off: never
    tz: str = ""                 # IANA zone such as Asia/Kolkata; empty means the machine's own clock
    max_turns: int = 25          # model round-trips allowed in one task
    task_usd: float = 0.50       # spending limit for one task
    daily_usd: float = 3.00      # spending limit for one calendar day
    fresh_after_min: int = 120   # after this long without a message, the next one starts a new conversation. 0: never
    fresh_above_tokens: int = 40000   # ...and after ten idle minutes, if the conversation has grown past this size
    effort: str = ""             # how hard the model thinks before each step: low, medium or high; empty leaves it to the model's default. Measured: medium moved nothing, so it is off (agent.py)
    cache_1h: bool = False       # keep the conversation's cache for an hour, not five minutes: a dearer write, fewer of them (agent.py)
    stop_grace_s: float = 5.0    # how long a task gets to end after "stop"
    data_dir: Path = Path("var")
    # Optional: reading notes filed into the owner's own website (booknotes.py). Both are needed.
    reading_repo: str = ""            # the site's GitHub repository, as owner/name
    reading_token: str = ""           # a fine-grained GitHub token with Contents read and write on that repository only
    reading_branch: str = "main"      # the branch whose pushes deploy the site
    review_at: str = "08:00"          # India time each day to send three of the owner's past notes back (resurface.py); "off": never
    reading_studio: str = ""          # the site's editor, e.g. https://example.com/studio/, for links in that message; empty: no links
    people_max: int = 10         # how many people this copy serves at most, the host included (people.py)
    park_after_min: int = 15     # an invited person silent this long has their agents' processes let go until they speak. 0: never
    member_daily_usd: float = 0.0   # the daily spending limit of each invited person. 0: the same as daily_usd
    attio_file: Path | None = None  # set by the program for the group's assistant when the host shares the firm's database with it


def imessage_on(s: Settings) -> bool:
    """Whether the iMessage channel is switched on: every one of its settings is present and it has not been turned off."""
    return bool(s.imessage and s.linq_token and s.linq_number and s.linq_secret and s.owner_imessage)


def vobiz_on(s: Settings) -> bool:
    """Whether WhatsApp through Vobiz is switched on: every one of its settings is present and it has not been turned off."""
    return bool(s.vobiz and s.vobiz_auth_id and s.vobiz_auth_token and s.vobiz_channel_id and s.vobiz_number and s.vobiz_secret
                and s.owner_whatsapp)


def make_clock(tz: str = ""):
    """The one clock used for message stamps, memory dates and the schedule: local wall-clock time, no zone."""
    if not tz:
        return datetime.now
    from zoneinfo import ZoneInfo
    zone = ZoneInfo(tz)
    return lambda: datetime.now(zone).replace(tzinfo=None)


def read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=value lines. No interpolation, no export, no multi-line values."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _how_many(value: str, default: int = 2, most: int = 6) -> int:
    """A count from a setting: empty is the default, "off" and the like are none, and it is never more than `most`."""
    v = value.strip().lower()
    if not v:
        return default
    if v in ("off", "false", "no"):
        return 0
    try:
        return max(0, min(most, int(v)))
    except ValueError:
        return default

def load(env: Mapping[str, str] | None = None, env_file: Path = Path(".env")) -> Settings:
    merged = {**read_env_file(env_file), **(os.environ if env is None else env)}
    required = ["TELEGRAM_BOT_TOKEN", "OWNER_TELEGRAM_ID", "ANTHROPIC_API_KEY", "EXA_API_KEY"]
    missing = [k for k in required if not merged.get(k)]
    if missing:
        raise ConfigError("Missing settings: " + ", ".join(missing) + ". See .env.example.")
    try:
        owner = int(merged["OWNER_TELEGRAM_ID"])
    except ValueError as e:
        raise ConfigError("OWNER_TELEGRAM_ID must be a number (your Telegram user ID).") from e
    return Settings(
        telegram_token=merged["TELEGRAM_BOT_TOKEN"],
        owner_id=owner,
        anthropic_api_key=merged["ANTHROPIC_API_KEY"],
        exa_api_key=merged["EXA_API_KEY"],
        composio_api_key=merged.get("COMPOSIO_API_KEY", ""),
        browser_use_api_key=merged.get("BROWSER_USE_API_KEY", ""),
        mistral_api_key=merged.get("MISTRAL_API_KEY", ""),
        agentmail_api_key=merged.get("AGENTMAIL_API_KEY", "").strip(),
        e2b_api_key=merged.get("E2B_API_KEY", "").strip(),
        sandbox_template=merged.get("ODYSSEUS_SANDBOX_TEMPLATE", "").strip(),
        owner_emails=tuple(a.strip().lower() for a in merged.get("ODYSSEUS_OWNER_EMAILS", "").split(",") if "@" in a),
        whatsapp_token=merged.get("WHATSAPP_TOKEN", ""),
        whatsapp_phone_id=merged.get("WHATSAPP_PHONE_ID", ""),
        whatsapp_app_secret=merged.get("WHATSAPP_APP_SECRET", ""),
        whatsapp_verify_token=merged.get("WHATSAPP_VERIFY_TOKEN", ""),
        owner_whatsapp=merged.get("OWNER_WHATSAPP", ""),
        whatsapp_port=int(merged.get("ODYSSEUS_PORT", "") or merged.get("WHATSAPP_PORT", 8080)),
        public_url=(lambda u: u if u.startswith("https://") else "")(merged.get("ODYSSEUS_PUBLIC_URL", "").strip().rstrip("/")),
        notify_on=merged.get("ODYSSEUS_NOTIFY_ON", "telegram").strip().lower(),
        linq_token=merged.get("LINQ_API_TOKEN", "").strip(),
        linq_number=merged.get("LINQ_NUMBER", "").strip(),
        linq_secret=merged.get("LINQ_WEBHOOK_SECRET", "").strip(),
        owner_imessage=merged.get("OWNER_IMESSAGE", "").strip(),
        imessage=merged.get("ODYSSEUS_IMESSAGE", "").strip().lower() not in ("0", "false", "no", "off"),
        vobiz_auth_id=merged.get("VOBIZ_AUTH_ID", "").strip(),
        vobiz_auth_token=merged.get("VOBIZ_AUTH_TOKEN", "").strip(),
        vobiz_channel_id=merged.get("VOBIZ_CHANNEL_ID", "").strip(),
        vobiz_number=merged.get("VOBIZ_NUMBER", "").strip(),
        vobiz_secret=merged.get("VOBIZ_WEBHOOK_SECRET", "").strip(),
        vobiz=merged.get("ODYSSEUS_VOBIZ", "").strip().lower() not in ("0", "false", "no", "off"),
        langfuse_public_key=merged.get("LANGFUSE_PUBLIC_KEY", ""),
        langfuse_secret_key=merged.get("LANGFUSE_SECRET_KEY", ""),
        langfuse_base_url=merged.get("LANGFUSE_BASE_URL", "") or merged.get("LANGFUSE_HOST", ""),
        langfuse_full_content=merged.get("LANGFUSE_FULL_CONTENT", "").strip().lower() in ("1", "true", "yes", "on"),
        browser_ask_signed_in=merged.get("ODYSSEUS_BROWSER_ASK_SIGNED_IN", "").strip().lower() not in ("0", "false", "no", "off"),
        background_jobs=_how_many(merged.get("ODYSSEUS_BACKGROUND_JOBS", "")),
        capture_at=(merged.get("ODYSSEUS_CAPTURE_AT", "") or "08:30").strip().lower(),
        pipeline_review_at=" ".join((merged.get("ODYSSEUS_PIPELINE_REVIEW_AT", "") or "mon 08:00").lower().split()),
        updates_at=(merged.get("ODYSSEUS_UPDATES_AT", "") or "08:45").strip().lower(),
        checkins_at=(merged.get("ODYSSEUS_CHECKINS_AT", "") or "09:30").strip().lower(),
        suggest_people=merged.get("ODYSSEUS_SUGGEST_PEOPLE", "").strip().lower() not in ("0", "false", "no", "off"),
        meeting_briefs=merged.get("ODYSSEUS_MEETING_BRIEFS", "").strip().lower() not in ("0", "false", "no", "off"),
        viewer_email=merged.get("ODYSSEUS_VIEWER_EMAIL", "").strip(),
        pages_model=merged.get("ODYSSEUS_PAGES_MODEL", "").strip(),
        review_model=merged.get("ODYSSEUS_REVIEW_MODEL", "").strip(),
        pages_check_model=merged.get("ODYSSEUS_PAGES_CHECK_MODEL", "").strip(),
        ask_every_change=merged.get("ODYSSEUS_ASK_EVERY_CHANGE", "").strip().lower() in ("1", "true", "yes", "on"),
        model=merged.get("ODYSSEUS_MODEL", "claude-sonnet-5-5"),
        background_model=merged.get("ODYSSEUS_BACKGROUND_MODEL", "claude-haiku-5-5"),
        voice_model=merged.get("ODYSSEUS_VOICE_MODEL", "voxtral-small-transcribe-3"),
        speak="off" if merged.get("ODYSSEUS_SPEAK", "voice").strip().lower() in ("off", "0", "no", "false") else "voice",
        tz=merged.get("ODYSSEUS_TZ", ""),
        max_turns=int(merged.get("ODYSSEUS_MAX_TURNS", 25)),
        fresh_after_min=int(merged.get("ODYSSEUS_FRESH_AFTER_MIN", 120)),
        fresh_above_tokens=int(merged.get("ODYSSEUS_FRESH_ABOVE_TOKENS", 40000)),
        cache_1h=merged.get("ODYSSEUS_CACHE_1H", "").strip().lower() in ("1", "true", "yes", "on"),
        effort=(lambda e: e if e in ("low", "medium", "high", "") else "")(merged.get("ODYSSEUS_EFFORT", "").strip().lower()),
        task_usd=float(merged.get("ODYSSEUS_TASK_USD", 0.50)),
        daily_usd=float(merged.get("ODYSSEUS_DAILY_USD", 3.00)),
        data_dir=Path(merged.get("ODYSSEUS_DATA_DIR", "var")),
        reading_repo=merged.get("ODYSSEUS_READING_REPO", "").strip(),
        reading_token=merged.get("ODYSSEUS_READING_TOKEN", "").strip(),
        reading_branch=merged.get("ODYSSEUS_READING_BRANCH", "").strip() or "main",
        review_at=(merged.get("ODYSSEUS_REVIEW_AT", "") or "08:00").strip().lower(),
        reading_studio=merged.get("ODYSSEUS_READING_STUDIO", "").strip(),
        people_max=max(1, min(50, int(merged.get("ODYSSEUS_PEOPLE_MAX", "") or 10))),
        park_after_min=max(0, int(merged.get("ODYSSEUS_PARK_AFTER_MIN", "") or 15)),
        member_daily_usd=float(merged.get("ODYSSEUS_MEMBER_DAILY_USD", "") or 0.0),
    )
