"""The gate: the one place that decides whether a requested action happens.

For every tool call the gate gives one of three answers:

  allow    it happens
  ask      it is held; the owner is shown exactly what will happen and taps a
           button; it happens only on "Approve"
  refuse   it does not happen

The model can request anything. It cannot grant anything.

Two locks guard an action that needs asking, and they are independent:

  1. Before the tool runs, the agent library asks the gate (permission()).
     The gate asks the owner and waits.
  2. Inside the tool itself, the handler asks the gate again (guard()). It
     runs only if there is an unused approval for this exact tool and these
     exact inputs. So if lock 1 were ever skipped or wrong, lock 2 still holds,
     and what runs is always what the owner was shown.

The text the owner sees is built here from the tool's real inputs by a
describe function. The model never writes it.
"""
from __future__ import annotations

import asyncio
import copy
import json
import secrets
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from .journal import Journal

ALLOW, ASK, REFUSE = "allow", "ask", "refuse"
STANDING_NOTE = ("(The owner was NOT shown this before it went. It was sent under the approval they gave earlier today for replies in this "
                 "thread to these people. Tell them in one plain line that it has been sent, and to whom.)")
STANDING_MAX = 5         # replies one thread's approval covers; the next is shown to the owner again
STANDING_BODY = 4000     # characters; a longer reply is always shown first
APPROVED_NOTE = ("(The program showed the owner exactly this action before it ran, and the owner approved it. Do not say it ran "
                 "without their approval.)")
MAX_QUESTION = 12000     # characters of an approval question; it is sent whole, over several messages if need be


@dataclass
class Rule:
    decide: Callable[[dict[str, Any]], str]              # inputs -> allow | ask | refuse
    describe: Callable[[dict[str, Any]], str] | None = None   # inputs -> the question shown to the owner
    constant: str | None = None                          # set when the answer never depends on the inputs
    after: Callable[[dict[str, Any]], None] | None = None     # called once the owner has approved these inputs


@dataclass
class Approval:
    id: str
    tool: str
    args: dict[str, Any]        # a frozen copy: this, and nothing else, is what may run
    text: str
    answer: asyncio.Future = field(repr=False, default=None)
    offer_thread: bool = False      # the question may also be answered "and further replies in this thread today"


def _key(tool: str, args: dict[str, Any]) -> str:
    return tool + "\n" + json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)


# Shows a pending approval to the owner (a message with buttons). Returns nothing; the answer comes through resolve().
Asker = Callable[[Approval], Awaitable[None]]


class Gate:
    def __init__(self, journal: Journal, timeout_s: float = 600.0):
        self._journal = journal
        self._timeout_s = timeout_s
        self._rules: dict[str, Rule] = {}
        self._asker: Asker | None = None
        self._pending: dict[str, Approval] = {}
        self._grants: list[str] = []       # approvals given and not yet used, one use each
        self._approved_now = False
        # A second reading of what is about to leave (review.py). It can add lines under a question. It cannot answer one.
        self._second: Callable[[str, dict[str, Any], str, str], Awaitable[str]] | None = None
        self._second_tools: frozenset[str] = frozenset()
        self.request = ""                  # what the owner asked in this task, with the exchanges before it; set by the conversation
        # A change that the rules let run unasked can still be put to the owner: when the task has read what other people
        # wrote, it is counted and read once more (review.make_escalator). This can only add a question.
        self._escalate: Callable[..., Awaitable[str]] | None = None
        self._escalate_when: Callable[[str, dict[str, Any]], bool] | None = None
        self._unasked = 0                  # such changes so far in this task
        # Several tasks may run at once: the owner's conversation, and jobs in the background (conversation.py). Whatever
        # is kept "for the current task" is then kept for all of them together, and always in the stricter direction: the
        # count of unasked changes goes on, what was asked is every request, nobody stops being asked until the last task
        # ends, and an approval widened to a thread does not apply while more than one is running.
        self._tasks: dict[str, str] = {}   # the tasks running now, each with what the owner asked in it
        # An approval the owner may widen, by their own tap, to later replies in the same thread to the same people until
        # midnight. Code holds the scope. Each such reply is still read by the second reader, which can send it back to
        # the owner but cannot let anything through that the scope does not cover.
        self._thread_tool = ""
        self._thread_of: Callable[[dict[str, Any]], str] | None = None
        self._people_of: Callable[[dict[str, Any]], frozenset[str]] | None = None
        self._clean: Callable[..., Awaitable[bool]] | None = None
        self._standing: dict[str, dict[str, Any]] = {}      # thread id -> who, until when, how many sent
        self._standing_keys: set[str] = set()               # calls let through under one, until their tool has run
        self._standing_used: list[str] = []                 # what went out that way in this task, for the owner's report
        self.now: Callable[[], Any] | None = None           # the owner's clock, for "until midnight"; set by make_gate

    # --- set-up ----------------------------------------------------------------

    def rule(self, tool: str, decide: Callable[[dict[str, Any]], str] | str,
             describe: Callable[[dict[str, Any]], str] | None = None,
             after: Callable[[dict[str, Any]], None] | None = None) -> None:
        if callable(decide):
            self._rules[tool] = Rule(decide, describe, after=after)
        else:
            self._rules[tool] = Rule(lambda args, d=decide: d, describe, constant=decide)

    def set_asker(self, asker: Asker | None) -> None:
        """Who to ask. None means nobody is here, and anything that needs asking is refused."""
        self._asker = asker

    def set_second_reader(self, reader: Callable[[str, dict[str, Any], str, str], Awaitable[str]] | None, tools: Any = ()) -> None:
        """Have `reader` look at the question for each of `tools` before it is shown. What it returns is put under the question."""
        self._second, self._second_tools = reader, frozenset(tools)
        self._clean = getattr(reader, "clean", None)

    def set_thread_approval(self, tool: str, thread_of: Callable[[dict[str, Any]], str], people_of: Callable[[dict[str, Any]], frozenset[str]]) -> None:
        """Let approvals of `tool` be widened by the owner to its thread. Takes effect only once a second reader is set."""
        self._thread_tool, self._thread_of, self._people_of = tool, thread_of, people_of

    def clear_standing(self) -> int:
        n = len(self._standing)
        self._standing.clear()
        return n

    def pop_standing(self) -> list[str]:
        out, self._standing_used = self._standing_used, []
        return out

    def _midnight(self) -> Any:
        from datetime import datetime, time as clock_time, timedelta
        today = (self.now or datetime.now)()
        return datetime.combine(today.date() + timedelta(days=1), clock_time.min, tzinfo=today.tzinfo)

    async def _standing_covers(self, tool: str, args: dict[str, Any]) -> bool:
        """Whether this call may go without a question, under an approval the owner widened to its thread.

        Every condition is checked by code, and any doubt means the owner is asked as usual: the same thread, nobody
        addressed who was not addressed when the owner approved, before midnight, not the sixth, not long, describable,
        and read by the second reader with nothing to check. A reader that fails or is slow is a doubt."""
        if tool != self._thread_tool or self._thread_of is None or self._people_of is None or self._clean is None or self._asker is None:
            return False
        if self.overlapping:
            return False        # the reader would be judging this reply against two requests at once: ask, as usual
        try:
            thread, people = self._thread_of(args), self._people_of(args)
            grant = self._standing.get(thread)
            if grant is None:
                return False
            from datetime import datetime
            if (self.now or datetime.now)() >= grant["until"]:
                del self._standing[thread]
                return False
            if not people or not people <= grant["people"] or grant["sent"] >= STANDING_MAX or len(str(args.get("body", ""))) > STANDING_BODY:
                return False
            rule = self._rules[tool]
            text = rule.describe(copy.deepcopy(args)) if rule.describe else ""
            if not text or len(text) > MAX_QUESTION or not await self._clean(tool, copy.deepcopy(args), text, self.request):
                return False
        except Exception as e:
            self._journal.write("error", where="gate.standing", error=type(e).__name__)
            return False
        grant["sent"] += 1
        key = _key(tool, args)
        self._grants.append(key)
        self._standing_keys.add(key)
        self._standing_used.append(f"reply to {', '.join(sorted(people))}")
        self._journal.write("gate", tool=tool, verdict="standing", nth=grant["sent"])
        return True

    def set_escalator(self, check: Callable[..., Awaitable[str]] | None, when: Callable[[str, dict[str, Any]], bool] | None = None) -> None:
        """`when(tool, args)` says whether an allowed call is a change to look at again; `check` returns why to ask, or ""."""
        self._escalate, self._escalate_when = check, when

    def begin_task(self, key: str, asker: Asker | None, request: str = "") -> None:
        """A task is starting. `key` names it ("main", "background")."""
        if not self._tasks:
            self._unasked = 0               # a fresh count only when nothing else is running
        self._tasks[key] = request
        self._asker = asker
        self.request = "\n\n---\n\n".join(r for r in self._tasks.values() if r)

    def end_task(self, key: str) -> None:
        """That task has ended. Only when it was the last one running does anybody stop being asked."""
        self._tasks.pop(key, None)
        if self._tasks:
            self.request = "\n\n---\n\n".join(r for r in self._tasks.values() if r)
        if not self._tasks:
            self._asker = None              # between tasks nobody is being asked, so nothing can be approved
            self.cancel_all()

    @property
    def overlapping(self) -> bool:
        return len(self._tasks) > 1

    def new_task(self, request: str = "") -> None:
        """The conversation is starting a task: what the owner asked, and a fresh count of changes made unasked."""
        self.request, self._unasked = request, 0

    async def _escalation(self, tool: str, args: dict[str, Any]) -> str:
        """Why a call the rules would allow should be put to the owner after all, or "" to let it run."""
        if self._escalate is None or self._escalate_when is None or self._asker is None:
            return ""
        try:
            if not self._escalate_when(tool, args):
                return ""
            self._unasked += 1
            rule = self._rules[tool]
            text = rule.describe(copy.deepcopy(args)) if rule.describe else f"{tool} with {json.dumps(args, ensure_ascii=False, default=str)}"
            return str(await self._escalate(tool, copy.deepcopy(args), text, self.request, self._unasked) or "").strip()
        except Exception as e:      # a reader that fails must never stop what the rules allow
            self._journal.write("error", where="gate.escalation", error=type(e).__name__)
            return ""

    async def _second_reading(self, tool: str, args: dict[str, Any], text: str) -> str:
        if self._second is None or tool not in self._second_tools:
            return ""
        try:
            note = str(await self._second(tool, copy.deepcopy(args), text, self.request) or "").strip()
        except Exception as e:      # a reader that fails must never stop the owner being asked
            self._journal.write("error", where="gate.second_reading", error=type(e).__name__)
            return ""
        return note if note and len(text) + len(note) + 2 <= MAX_QUESTION else ""

    def decide(self, tool: str, args: dict[str, Any]) -> str:
        rule = self._rules.get(tool)
        if rule is None:
            return REFUSE          # a tool the gate has never heard of does not run
        try:
            answer = rule.decide(args)
        except Exception:
            return REFUSE
        return answer if answer in (ALLOW, ASK, REFUSE) else REFUSE

    def always_allowed(self) -> list[str]:
        """Tools whose rule is 'allow' whatever the inputs. Only these may skip lock 1."""
        return sorted(t for t, r in self._rules.items() if r.constant == ALLOW)

    # --- lock 1: called by the agent library before a tool runs -------------------

    async def permission(self, tool: str, args: dict[str, Any], context: Any = None):
        verdict = self.decide(tool, args)
        if verdict == ALLOW:
            why_ask = await self._escalation(tool, args)
            if not why_ask:
                return PermissionResultAllow()
            approved, why, approval = await self.ask(tool, args, note=why_ask)
            if approved:
                return PermissionResultAllow(updated_input=copy.deepcopy(approval.args))
            return PermissionResultDeny(message=why)
        if verdict == REFUSE:
            self._journal.write("gate", tool=tool, verdict="refused")
            return PermissionResultDeny(message=f"{tool} is not available.")
        if await self._standing_covers(tool, args):
            return PermissionResultAllow(updated_input=copy.deepcopy(args))
        approved, why, approval = await self.ask(tool, args)
        if approved:
            return PermissionResultAllow(updated_input=copy.deepcopy(approval.args))
        return PermissionResultDeny(message=why)

    async def ask(self, tool: str, args: dict[str, Any], note: str = "") -> tuple[bool, str, Approval | None]:
        if self._asker is None:
            self._journal.write("gate", tool=tool, verdict="refused", reason="nobody to ask")
            return False, "This needs the owner's approval and nobody is here to give it. It was not done.", None
        frozen = copy.deepcopy(args)
        rule = self._rules[tool]
        try:
            text = rule.describe(frozen) if rule.describe else f"Allow {tool} with {json.dumps(frozen, ensure_ascii=False)}?"
        except Exception as e:
            self._journal.write("gate", tool=tool, verdict="refused", reason=f"cannot describe: {type(e).__name__}")
            detail = f" ({e})" if type(e).__name__.endswith("Error_") or type(e).__name__ in ("ScheduleError", "AppError") else ""
            return False, f"This request is not well formed, so it was not shown to the owner and was not done.{detail}", None
        if len(text) > MAX_QUESTION:
            # What cannot be shown in full cannot be approved: the owner would be agreeing to text they never saw.
            self._journal.write("gate", tool=tool, verdict="refused", reason="too long to show", chars=len(text))
            return False, (f"This is too long to show the owner in full ({len(text)} characters; the limit is {MAX_QUESTION}), so it was "
                           "not shown and was not done. Do it in smaller pieces."), None
        note = note or await self._second_reading(tool, frozen, text)
        if note and len(text) + len(note) + 2 <= MAX_QUESTION:
            text = f"{text}\n\n{note}"
        approval = Approval(secrets.token_hex(4), tool, frozen, text, asyncio.get_running_loop().create_future(),
                            offer_thread=tool == self._thread_tool and self._clean is not None and self._thread_of is not None)
        self._pending[approval.id] = approval
        self._journal.write("gate", tool=tool, verdict="asked", approval=approval.id, args=frozen, **({"second_reading": True} if note else {}))
        try:
            await self._asker(approval)
            answer = await asyncio.wait_for(asyncio.shield(approval.answer), self._timeout_s)
        except asyncio.TimeoutError:
            answer = "expired"
        except Exception as e:     # the question could not be shown
            self._journal.write("error", where="gate.ask", error=type(e).__name__)
            answer = "unasked"
        finally:
            self._pending.pop(approval.id, None)
        self._journal.write("gate", tool=tool, verdict=answer, approval=approval.id)
        if answer == "approved":
            self._grants.append(_key(tool, frozen))
            if rule.after is not None:
                rule.after(frozen)
            return True, "", approval
        why = {"declined": "The owner declined. It was not done. Do not try again unless the owner asks.",
               "expired": "The owner did not answer in time. It was not done.",
               "cancelled": "The owner stopped the task. It was not done.",
               "unasked": "The request could not be shown to the owner. It was not done."}[answer]
        return False, why, approval

    # --- the owner's answer ------------------------------------------------------

    def pending(self) -> list[Approval]:
        return list(self._pending.values())

    def resolve(self, approval_id: str, approved: bool, thread: bool = False) -> Approval | None:
        """Record the owner's tap. Returns None if nothing with that id is waiting.

        `thread`: the owner tapped the third button, so later replies in this thread to these people go without a
        question until midnight (see _standing_covers). Only an approval that offered it can be widened."""
        approval = self._pending.get(approval_id)
        if approval is None or approval.answer.done():
            return None
        if approved and thread and approval.offer_thread:
            try:
                self._standing[self._thread_of(approval.args)] = {"people": self._people_of(approval.args), "until": self._midnight(), "sent": 0}
                self._journal.write("gate", tool=approval.tool, verdict="widened to the thread", approval=approval.id)
            except Exception as e:
                self._journal.write("error", where="gate.widen", error=type(e).__name__)
        approval.answer.set_result("approved" if approved else "declined")
        return approval

    def cancel_all(self) -> int:
        n = 0
        for approval in list(self._pending.values()):
            if not approval.answer.done():
                approval.answer.set_result("cancelled")
                n += 1
        return n

    # --- lock 2: called inside a tool's own handler --------------------------------

    def guard(self, tool: str, args: dict[str, Any]) -> bool:
        """True if this exact call may run now. An approval is used up by the call it allows."""
        verdict = self.decide(tool, args)
        self._approved_now = False      # set below when this call runs on the owner's approval
        if verdict == ALLOW:
            # If an approval for exactly this call is waiting, it is used up here too, so that it cannot be
            # spent later on a call that would otherwise have needed asking.
            key = _key(tool, args)
            if key in self._grants:
                self._grants.remove(key)
                self._approved_now = True       # allowed by rule, but it was put to the owner after a second reading, and approved
            return True
        if verdict == ASK:
            key = _key(tool, args)
            if key in self._grants:
                self._grants.remove(key)
                self._approved_now = True
                return True
        self._journal.write("gate", tool=tool, verdict="blocked", args=args)
        return False

    def guarded(self, tool: str, handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]):
        async def wrapped(args: dict[str, Any]) -> dict[str, Any]:
            if not self.guard(tool, args):
                return {"content": [{"type": "text", "text": "This was not approved by the owner, so it was not done."}], "is_error": True}
            approved = self._approved_now
            key = _key(tool, args)
            standing = key in self._standing_keys
            self._standing_keys.discard(key)
            result = await handler(args)
            if standing and isinstance(result, dict) and isinstance(result.get("content"), list):
                return {**result, "content": [*result["content"], {"type": "text", "text": STANDING_NOTE}]}
            if approved and isinstance(result, dict) and isinstance(result.get("content"), list):
                # The model is otherwise told nothing of the question. In a live run it then told the owner that no
                # question had been shown, about an email they had just approved.
                result = {**result, "content": [*result["content"], {"type": "text", "text": APPROVED_NOTE}]}
            return result
        return wrapped
