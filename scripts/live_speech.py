"""The speech service, live: one short sentence turned into sound. Prints the size and the container, nothing else.

    python scripts/live_speech.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus.config import load  # noqa: E402
from odysseus.voice import MistralVoice, speakable  # noqa: E402


def main() -> int:
    settings = load()
    if not settings.mistral_api_key:
        print("No Mistral key in the settings.")
        return 1
    words = speakable("Your call with the founder is at three tomorrow. I have put the deck with your files.")
    began = time.monotonic()
    sound = MistralVoice(settings.mistral_api_key).say(words)
    ogg = sound[:4] == b"OggS"
    print(f"{len(words)} characters -> {len(sound)} bytes in {time.monotonic() - began:.1f} s; "
          f"{'Opus in an Ogg file: Telegram shows it as a voice note' if ogg else 'not an Ogg file: it would go as a sound file'}; "
          f"about ${len(words) * 13 / 1_000_000:.5f}")
    return 0 if sound else 1


if __name__ == "__main__":
    sys.exit(main())
