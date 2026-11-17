"""The same request, said differently.

A real owner does not type the sentence the suite's author typed. If an errand is
done when it is worded one way and not when it is worded another, the agent has not
learned the errand; it has learned the sentence. So every errand is also run with
its wording changed, and the same check must still pass. Two runs are compared, and
no new answer has to be written.

  reword   the request rewritten by a model, once, and saved to rewordings.json so that
           every later run uses the same words. A rewording is kept only if every
           specific in the original survives in it: addresses, numbers, times, quoted
           titles and names. One that drops any of them is thrown away and the original
           is used, and that is recorded.
  messy    how people type on a phone: lower case, missing punctuation, a couple of
           slips, and something beside the point in front. Made by rule from a seed.

Two sets of rewordings are kept. The suite is developed against set "a". Set "b" is
sealed: it is run to report a score, and its failures are not used to tune anything.

    .venv/Scripts/python -m evals.perturb      (re)make rewordings.json; costs a few cents
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import sys
from pathlib import Path

FILE = Path(__file__).with_name("rewordings.json")
CHATTER = ["morning. ", "ok so ", "quick one - ", "hey, ", "sorry, in a rush. ", "back from a meeting. ", "one more thing: ", "right. "]
_SPECIFIC = re.compile(r"[\w.+-]+@[\w.-]+\.\w+|'[^']{2,}'|\"[^\"]{2,}\"|\b\d[\d:.,]*\s?(?:am|pm)?\b|\b[A-Z][a-z]{2,}\b|\b[A-Z]{2,}\d*\b")
_KEEP = re.compile(r"[\w.+-]+@[\w.-]+\.\w+|'[^']{2,}'|\"[^\"]{2,}\"|\d")


def specifics(text: str) -> set[str]:
    """What a rewording must not lose. Sentence-initial words are skipped: they are capitalised for another reason."""
    found = set()
    for m in _SPECIFIC.finditer(text):
        token = m.group(0).strip().strip("'\"")
        starts_sentence = m.start() == 0 or text[:m.start()].rstrip().endswith((".", "?", "!", ":"))
        if starts_sentence and token.isalpha() and token[0].isupper() and token[1:].islower():
            continue
        found.add(re.sub(r"\s+", "", token.lower().rstrip(".,")))
    return {f for f in found if f}


def keeps_specifics(original: str, reworded: str) -> bool:
    squashed = re.sub(r"\s+", "", reworded.lower())
    return all(s in squashed for s in specifics(original))


def messy(text: str, rng: random.Random) -> str:
    """Typed in a hurry. Addresses, numbers and quoted titles are left exactly as they were."""
    out = []
    for word in text.split(" "):
        if _KEEP.search(word) or len(word) < 5 or rng.random() > 0.12:
            out.append(word if _KEEP.search(word) else word.lower())
            continue
        i = rng.randrange(1, len(word) - 2)
        out.append((word[:i] + word[i + 1] + word[i] + word[i + 2:]).lower())      # two neighbouring letters swapped
    joined = re.sub(r"[,;]", "", " ".join(out))
    joined = re.sub(r"\.(\s|$)", r"\1", joined) if rng.random() < 0.6 else joined
    return rng.choice(CHATTER) + joined.strip()


def load() -> dict[str, dict[str, str]]:
    return json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else {}


def apply(text: str, how: str, rng: random.Random, sealed: bool = False, table: dict | None = None) -> str:
    if how == "reword":
        entry = (table if table is not None else load()).get(text) or {}
        return entry.get("b" if sealed else "a") or text
    if how == "messy":
        return messy(text, rng)
    return text


async def _reword(settings, text: str, style: str) -> str:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
    prompt = ("Rewrite this message that a person sent to their personal assistant, so that it asks for exactly the same thing in different "
              f"words and a different sentence structure. Style: {style}. Keep every name, email address, number, date, time and quoted "
              "title exactly as written. Do not add or remove any request or detail. Reply with the rewritten message only.\n\n" + text)
    out = ""
    async for msg in query(prompt=prompt, options=ClaudeAgentOptions(
            model=settings.background_model, system_prompt="You rewrite one message. Output only the rewritten message.", tools=[],
            setting_sources=[], max_turns=1, env={"ANTHROPIC_API_KEY": settings.anthropic_api_key})):
        if isinstance(msg, ResultMessage):
            out = (msg.result or "").strip().strip('"')
    return out


_META = re.compile(r"(?i)rewrit|original message|paste (it|the)|here(?:'s| is) (a|the) re")


async def _same(settings, original: str, reworded: str) -> bool:
    """A second reading, by a model, of whether the rewording asks for exactly what the original asked for."""
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
    prompt = ("Two messages from a person to their personal assistant follow. Does B ask the assistant to DO exactly the same things as A: "
              "the same actions, to the same people, with the same details, and nothing A asked for left out or turned from an instruction "
              f"into a question about whether it is possible? Answer with one word: YES or NO." + chr(10) * 2 + f"A: {original}" + chr(10) * 2 + f"B: {reworded}")
    out = ""
    async for msg in query(prompt=prompt, options=ClaudeAgentOptions(
            model=settings.model, system_prompt="You compare two messages. Answer YES or NO only.", tools=[],
            setting_sources=[], max_turns=1, env={"ANTHROPIC_API_KEY": settings.anthropic_api_key})):
        if isinstance(msg, ResultMessage):
            out = msg.result or ""
    return out.strip().upper().startswith("YES")


async def main() -> int:
    from odysseus.config import load as load_settings

    from .dynamic import SCENARIOS
    from .tasks import TASKS
    settings = load_settings()
    texts = []
    for t in [*TASKS, *SCENARIOS]:
        for text in [t.say, *t.then, *[s[1] for s in t.script if s[0] == "say"]]:
            if text and text not in texts:
                texts.append(text)
    table = load()
    styles = {"a": "casual and a little indirect, as if typed to a friend", "b": "brisk and compressed, as if dictated while walking"}
    gate = asyncio.Semaphore(6)
    dropped = 0

    rejected = 0

    async def acceptable(text: str, new: str) -> bool:
        if not new or new == text or _META.search(new) or not keeps_specifics(text, new):
            return False
        try:
            return await _same(settings, text, new)
        except Exception:
            return False

    async def one(text: str, which: str):
        nonlocal dropped, rejected
        async with gate:
            have = (table.get(text) or {}).get(which)
            if have:
                if await acceptable(text, have):
                    return
                rejected += 1          # an earlier rewording that does not survive the stricter checks
            for _ in range(3):
                try:
                    new = await _reword(settings, text, styles[which])
                except Exception:
                    new = ""
                if await acceptable(text, new):
                    table.setdefault(text, {})[which] = new
                    return
        dropped += 1
        table.setdefault(text, {})[which] = ""        # no acceptable rewording: the original will be used

    await asyncio.gather(*(one(t, w) for t in texts for w in styles))
    FILE.write_text(json.dumps({k: table[k] for k in texts}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(texts)} messages, two rewordings each; {rejected} earlier rewordings were rejected by the stricter checks; "
          f"{dropped} have no acceptable rewording and keep their original words")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
