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
            return PermissionResultAllow()
        if verdict == REFUSE:
            self._journal.write("gate", tool=tool, verdict="refused")
            return PermissionResultDeny(message=f"{tool} is not available.")
        approved, why, approval = await self.ask(tool, args)
        if approved:
            return PermissionResultAllow(updated_input=copy.deepcopy(approval.args))
        return PermissionResultDeny(message=why)

    async def ask(self, tool: str, args: dict[str, Any]) -> tuple[bool, str, Approval | None]:
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
        approval = Approval(secrets.token_hex(4), tool, frozen, text, asyncio.get_running_loop().create_future())
        self._pending[approval.id] = approval
        self._journal.write("gate", tool=tool, verdict="asked", approval=approval.id, args=frozen)
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

    def resolve(self, approval_id: str, approved: bool) -> Approval | None:
        """Record the owner's tap. Returns None if nothing with that id is waiting."""
        approval = self._pending.get(approval_id)
        if approval is None or approval.answer.done():
            return None
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
        if verdict == ALLOW:
            # If an approval for exactly this call is waiting, it is used up here too, so that it cannot be
            # spent later on a call that would otherwise have needed asking.
            key = _key(tool, args)
            if key in self._grants:
                self._grants.remove(key)
            return True
        if verdict == ASK:
            key = _key(tool, args)
            if key in self._grants:
                self._grants.remove(key)
                return True
        self._journal.write("gate", tool=tool, verdict="blocked", args=args)
        return False

    def guarded(self, tool: str, handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]):
        async def wrapped(args: dict[str, Any]) -> dict[str, Any]:
            if not self.guard(tool, args):
                return {"content": [{"type": "text", "text": "This was not approved by the owner, so it was not done."}], "is_error": True}
            return await handler(args)
        return wrapped
