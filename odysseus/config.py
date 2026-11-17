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
    browser_ask_signed_in: bool = False   # ask the owner before a browser run that is signed in as them
    mistral_api_key: str = ""    # optional: without it, files the owner sends cannot be read
    langfuse_public_key: str = ""   # optional: with both Langfuse keys, every task is also recorded as a trace (tracing.py)
    langfuse_secret_key: str = ""
    langfuse_base_url: str = ""     # the Langfuse region or your own server; empty means Langfuse's default
    model: str = "claude-sonnet-5-5"
    background_model: str = "claude-haiku-5-5"   # runs scheduled routines
    tz: str = ""                 # IANA zone such as Asia/Kolkata; empty means the machine's own clock
    max_turns: int = 25          # model round-trips allowed in one task
    task_usd: float = 0.50       # spending limit for one task
    daily_usd: float = 3.00      # spending limit for one calendar day
    stop_grace_s: float = 5.0    # how long a task gets to end after "stop"
    data_dir: Path = Path("var")


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
        langfuse_public_key=merged.get("LANGFUSE_PUBLIC_KEY", ""),
        langfuse_secret_key=merged.get("LANGFUSE_SECRET_KEY", ""),
        langfuse_base_url=merged.get("LANGFUSE_BASE_URL", "") or merged.get("LANGFUSE_HOST", ""),
        browser_ask_signed_in=merged.get("ODYSSEUS_BROWSER_ASK_SIGNED_IN", "").strip().lower() in ("1", "true", "yes", "on"),
        model=merged.get("ODYSSEUS_MODEL", "claude-sonnet-5-5"),
        background_model=merged.get("ODYSSEUS_BACKGROUND_MODEL", "claude-haiku-5-5"),
        tz=merged.get("ODYSSEUS_TZ", ""),
        max_turns=int(merged.get("ODYSSEUS_MAX_TURNS", 25)),
        task_usd=float(merged.get("ODYSSEUS_TASK_USD", 0.50)),
        daily_usd=float(merged.get("ODYSSEUS_DAILY_USD", 3.00)),
        data_dir=Path(merged.get("ODYSSEUS_DATA_DIR", "var")),
    )
