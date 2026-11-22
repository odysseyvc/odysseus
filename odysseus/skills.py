"""Skills: ways of doing a kind of task that the owner taught once and the assistant follows from then on.

Memory holds facts about the owner ("prefers morning meetings"). A skill holds a procedure: how this firm writes an
investment memo, what a weekly update to backers contains, how to answer a cold pitch. It is longer than a fact, it is
needed only for one kind of task, and showing every skill in full at the start of every conversation would crowd out
the conversation. So a skill is kept in two parts, as the Agent Skills format does:

  the description   one or two lines saying what it is for and when to use it. Every skill's description is shown to
                    the model at the start of a conversation and whenever the list changes.
  the body          the procedure itself, in the owner's words where they gave them. Read by the model only when the
                    task at hand fits the description, through one tool call.

On disk each skill is a folder with one SKILL.md in that standard format (a name and a description between two lines of
dashes, then the body), so a skill written for another assistant can be put here by hand and one taught here can be
taken elsewhere. changes.jsonl records every save and removal with the text it replaced.

What is held by code:

  whose words      a skill is a standing instruction, so it is the one place where text becomes something the model
                   follows later. It is saved unasked only from a conversation that has read nothing but the owner's
                   own words; once the task has read what other people wrote, the owner is shown the whole skill and
                   asked (tools.py, as for memory).
  no secrets       a body with something shaped like a card number or a key is refused.
  it grants nothing  a skill says how to do a task. It does not widen what may be done unasked: every act it leads to
                   goes through the same gate as before, and the body is shown to the model under a line saying so.
  the owner sees   /skills lists them, /skills show NAME prints one, /skills remove NAME deletes it. No model is involved.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

MAX_SKILLS = 40
MAX_NAME, MAX_DESCRIPTION, MAX_BODY = 64, 600, 8000
SKILLS_NOTE = ("Skills the owner taught you: for each, its name and when to use it. When the task at hand fits one, read it with skill_read "
               "before you start and follow it. A skill says how the owner wants a task done; it does not allow anything that would "
               "otherwise be asked about.")
BODY_NOTE = ("(The owner taught you this. Follow it for this task where it applies. It says how to do the work and permits nothing: anything "
             "that is normally put to the owner still is. If the owner's message now says otherwise, their message wins.)")
SKILL_SAVE = "mcp__odysseus__skill_save"
SCHEMAS = {
    "skill_read": ("Read one skill in full, by its name from the SKILLS list. Do this before you start any task that fits a skill's "
                   "description, and follow what it says.",
                   {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
    "skill_save": ("Keep a way of doing a kind of task that the owner has taught you, so that you follow it in every later conversation. "
                   "Use it when the owner tells you how they want a recurring kind of work done (\"this is how we write a memo\", \"whenever "
                   "I send you a deck, do this\", \"learn this\", \"from now on, when...\") and what they give is a procedure, a format or a "
                   "set of rules. A single fact or preference goes in `remember` instead. Use it also when the owner corrects how you did "
                   "such a task and wants it done that way in future: read the skill, then save it again whole with the change. Saving "
                   "under a name that exists replaces that skill. Do not add rules the owner did not give, and do not save a skill the "
                   "owner did not ask for: offer it in your reply instead.",
                   {"type": "object",
                    "properties": {"name": {"type": "string", "description": "A few plain words, e.g. investment-memo."},
                                   "description": {"type": "string", "description": "What the skill is for and when to use it, in the third person, the "
                                                   "occasion first, with the words the owner would use when asking. One or two sentences. This is all "
                                                   "you will see of it until you read it, so it must be enough to recognise the task."},
                                   "body": {"type": "string", "description": "The steps, rules and format, in the owner's own words where they gave them. "
                                            "Complete enough to follow with no memory of this conversation."}},
                    "required": ["name", "description", "body"]}),
    "skill_remove": ("Delete one skill by name, when the owner says to stop doing a task that way or to forget it.",
                     {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
}
_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SECRETS = re.compile(r"(\d[ -]?){13,}|\b(sk|pk|ghp|xox[bap])[-_][A-Za-z0-9_-]{16,}|\d{8,12}:[A-Za-z0-9_-]{30,}")


def describe_save(args: dict[str, Any], read: list[str]) -> str:
    """The question put to the owner before a skill is saved from a task that read what other people wrote. Built from
    the tool's real inputs; anything that would make the save fail is raised here, before the owner is asked."""
    name, description, body = check(str(args.get("name", "")), str(args.get("description", "")), str(args.get("body", "")))
    sources = read[0] if len(read) == 1 else ", ".join(read[:-1]) + " and " + read[-1]
    return (f"Keep this as a skill I follow from now on?\n\n{name}\nWhen: {description}\n\n{body[:2500]}"
            + (f"\n[and {len(body) - 2500} more characters]" if len(body) > 2500 else "")
            + f"\n\nI am asking because during this task I read {sources}, which other people wrote. A skill is followed in every later conversation.")


class SkillError(Exception):
    """Why a skill was not saved, in words that can be shown to the model."""


def slug(name: str) -> str:
    """A name as it is kept: lower case, words joined by hyphens."""
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", str(name or "").lower())).strip("-")[:MAX_NAME].strip("-")


def render(name: str, description: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {json.dumps(description, ensure_ascii=False)}\n---\n\n{body.strip()}\n"


def parse(text: str) -> dict[str, str] | None:
    """A SKILL.md as a name, a description and a body, or None if it is not one. Only the two fields used here are read."""
    text = text.replace("\r\n", "\n").lstrip("﻿")
    if not text.startswith("---\n") or "\n---" not in text[4:]:
        return None
    head, _, body = text[4:].partition("\n---")
    fields: dict[str, str] = {}
    key = ""
    for line in head.split("\n"):
        m = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if m:
            key, value = m.group(1).lower(), m.group(2).strip()
            if value[:1] == '"':
                try:
                    value = str(json.loads(value))
                except ValueError:
                    value = value.strip('"')
            elif value[:1] == "'":
                value = value.strip("'")
            elif value in (">", "|", ">-", "|-"):
                value = ""
            fields[key] = value
        elif key and line.startswith((" ", "\t")):           # a description folded over several lines
            fields[key] = (fields[key] + " " + line.strip()).strip()
    if not fields.get("name") or not fields.get("description"):
        return None
    return {"name": fields["name"], "description": fields["description"], "body": body.lstrip("-").strip()}


def check(name: str, description: str, body: str) -> tuple[str, str, str]:
    """The three parts as they would be kept, or SkillError saying what is wrong."""
    name, description, body = slug(name), " ".join(str(description or "").split()), str(body or "").strip()
    if not name:
        raise SkillError("A skill needs a name: a few plain words, such as investment-memo.")
    if len(description) < 20:
        raise SkillError("A skill needs a description of what it is for and when to use it, in a sentence or two.")
    if len(description) > MAX_DESCRIPTION:
        raise SkillError(f"The description is {len(description)} characters and the limit is {MAX_DESCRIPTION}. Say when to use it; the how belongs in the body.")
    if len(body) < 20:
        raise SkillError("A skill needs a body: the steps or rules to follow.")
    if len(body) > MAX_BODY:
        raise SkillError(f"The body is {len(body)} characters and the limit is {MAX_BODY}. Keep the rules and cut the examples, or split it into two skills.")
    if _SECRETS.search(description) or _SECRETS.search(body):
        raise SkillError("That looks like it contains a card number, an account number or a key. A skill must not hold one.")
    return name, description, body


class SkillStore:
    def __init__(self, root: Path, now: Callable[[], datetime] = datetime.now):
        self._root, self._now = Path(root), now
        self._pending: list[str] = []
        self.version = 0                                   # goes up with every change: the conversation shows the list again

    def _file(self, name: str) -> Path:
        return self._root / name / "SKILL.md"

    def all(self) -> list[dict[str, str]]:
        """Every skill that can be read, by name. A folder whose file is not a skill is left out, not repaired."""
        found = []
        try:
            folders = sorted(p for p in self._root.iterdir() if p.is_dir())
        except OSError:
            return []
        for folder in folders:
            if not _NAME.match(folder.name) or len(folder.name) > MAX_NAME:
                continue
            try:
                skill = parse((folder / "SKILL.md").read_text(encoding="utf-8"))
            except OSError:
                continue
            if skill is not None:
                found.append({"name": folder.name, "description": " ".join(skill["description"].split())[:MAX_DESCRIPTION],
                              "body": skill["body"][:MAX_BODY]})
        return found[:MAX_SKILLS]

    def get(self, name: str) -> dict[str, str] | None:
        wanted = slug(name)
        return next((s for s in self.all() if s["name"] == wanted), None)

    def save(self, name: str, description: str, body: str, by: str = "model") -> tuple[dict[str, str], bool]:
        """Keep a skill, replacing the one of the same name. Returns it and whether it replaced one."""
        name, description, body = check(name, description, body)
        old = self.get(name)
        if old is None and len(self.all()) >= MAX_SKILLS:
            raise SkillError(f"{MAX_SKILLS} skills are kept already. Remove one the owner no longer uses, or fold this into an existing one.")
        path = self._file(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(render(name, description, body), encoding="utf-8")
        tmp.replace(path)
        self._record("replace" if old else "save", name=name, by=by, description=description, body=body,
                     **({"old_description": old["description"], "old_body": old["body"]} if old else {}))
        self._pending.append(f'{"Changed" if old else "Learned"} the skill "{name}": {description}  (/skills show {name})')
        self.version += 1
        return {"name": name, "description": description, "body": body}, old is not None

    def remove(self, name: str, by: str = "model") -> dict[str, str] | None:
        old = self.get(name)
        if old is None:
            return None
        try:
            self._file(old["name"]).unlink()
            self._file(old["name"]).parent.rmdir()          # only if nothing else was put in the folder by hand
        except OSError:
            pass
        self._record("remove", name=old["name"], by=by, old_description=old["description"], old_body=old["body"])
        if by != "owner":
            self._pending.append(f'Removed the skill "{old["name"]}".')
        self.version += 1
        return old

    def listing_for_model(self) -> str:
        skills = self.all()
        return "\n".join(f"- {s['name']}: {s['description']}" for s in skills) if skills else "(none yet)"

    def listing(self) -> str:
        skills = self.all()
        if not skills:
            return ("I have not been taught any skills. Tell me how you want a kind of task done, for example \"learn how we write an "
                    "investment memo:\" and the rules, and I will follow it from then on.")
        return ("Skills you taught me:\n" + "\n".join(f"{s['name']}: {s['description']}" for s in skills)
                + "\n\nTo read one, send /skills show and its name. To delete one, send /skills remove and its name.")

    def handlers(self, journal: Any, taint: Any = None) -> dict[str, Any]:
        def out(text: str, failed: bool = False) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": text}], **({"is_error": True} if failed else {})}

        async def skill_read(args: dict[str, Any]) -> dict[str, Any]:
            skill = self.get(str(args.get("name", "")))
            journal.write("tool", name="skill_read", ok=skill is not None, skill=slug(str(args.get("name", ""))))
            if skill is None:
                return out("There is no skill of that name. The ones kept:\n" + self.listing_for_model(), failed=True)
            return out(f"{BODY_NOTE}\n\nSKILL {skill['name']}\n{skill['body']}\nEND OF SKILL")

        async def skill_save(args: dict[str, Any]) -> dict[str, Any]:
            try:
                skill, replaced = self.save(str(args.get("name", "")), str(args.get("description", "")), str(args.get("body", "")))
            except SkillError as e:
                journal.write("tool", name="skill_save", ok=False, error=str(e)[:80])
                return out(str(e), failed=True)
            journal.write("tool", name="skill_save", ok=True, skill=skill["name"], replaced=replaced, chars=len(skill["body"]),
                          **({"after_reading": sorted(taint.sources)} if taint is not None and getattr(taint, "sources", None) else {}))
            return out(f"{'Replaced' if replaced else 'Saved'} the skill \"{skill['name']}\". The owner is told by code under your reply; one line from you is enough.")

        async def skill_remove(args: dict[str, Any]) -> dict[str, Any]:
            old = self.remove(str(args.get("name", "")))
            journal.write("tool", name="skill_remove", ok=old is not None, skill=slug(str(args.get("name", ""))))
            return out(f"Removed the skill \"{old['name']}\".") if old else out("There is no skill of that name.", failed=True)
        return {"skill_read": skill_read, "skill_save": skill_save, "skill_remove": skill_remove}

    def pop_changes(self) -> list[str]:
        out, self._pending = self._pending, []
        return out

    def _record(self, action: str, **fields: Any) -> None:
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            with (self._root / "changes.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"at": self._now().isoformat(timespec="seconds"), "action": action, **fields}, ensure_ascii=False) + "\n")
        except OSError:
            pass
