"""One approval covers the rest of a job on one thing: a long list written to the owner's own sheet in several pieces is
asked about once, not once a piece. Anything of a kind that is asked about every time is never covered.

Everything here is made up. No model is called.
"""
from __future__ import annotations

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from odysseus.gate import ALLOW, Gate
from odysseus.journal import Journal


def rig(tmp_path, answer="approved"):
    gate, asked = Gate(Journal(tmp_path / "j.jsonl"), timeout_s=5), []
    gate.rule("write", ALLOW, lambda a: f"Write to {a.get('file')}?")

    async def asker(approval):
        asked.append(approval.text)
        approval.answer.set_result(answer)

    async def always(tool, args, question, request, nth):
        return "I am asking because this follows something other people wrote."
    gate.set_escalator(always, lambda tool, args: True)
    gate.set_scope(lambda tool, args: "" if args.get("replace") else f"sheet:{args.get('file')}")
    gate.begin_task("main", asker, "The owner said: write the list to my sheet")
    return gate, asked


async def test_after_one_yes_the_same_file_is_not_asked_about_again_in_that_task(tmp_path):
    gate, asked = rig(tmp_path)
    for piece in range(4):
        assert isinstance(await gate.permission("write", {"file": "A", "rows": piece}), PermissionResultAllow)
    assert len(asked) == 1, "asked once, for the first piece"
    covered = [r for r in Journal(tmp_path / "j.jsonl").read() if r.get("verdict") == "covered"]
    assert len(covered) == 3, "and each later piece is on record as covered, not as unasked"


async def test_another_file_and_a_kind_that_always_asks_are_not_covered(tmp_path):
    gate, asked = rig(tmp_path)
    await gate.permission("write", {"file": "A"})
    await gate.permission("write", {"file": "B"})
    assert len(asked) == 2, "a yes for one file says nothing about another"
    await gate.permission("write", {"file": "A", "replace": True})
    assert len(asked) == 3, "replacing has no scope, so it is put to the owner whatever was approved before"


async def test_a_no_covers_nothing_and_a_new_task_starts_clean(tmp_path):
    gate, asked = rig(tmp_path, answer="declined")
    assert isinstance(await gate.permission("write", {"file": "A"}), PermissionResultDeny)
    assert isinstance(await gate.permission("write", {"file": "A"}), PermissionResultDeny) and len(asked) == 2
    gate2, asked2 = rig(tmp_path / "two")
    await gate2.permission("write", {"file": "A"})
    gate2.new_task("The owner said: something else")
    await gate2.permission("write", {"file": "A"})
    assert len(asked2) == 2, "what was approved in one task is not carried into the next"


def test_only_a_small_change_to_the_owners_own_file_has_a_scope(tmp_path):
    from odysseus.anyapp import AnyApp
    from odysseus.tools import make_gate

    class B:
        def execute(self, slug, arguments): return {}
    anyapp = AnyApp(B(), Journal(tmp_path / "a.jsonl"))
    async def escalate(tool, args, question, request, nth):
        return ""
    gate = make_gate(Journal(tmp_path / "g.jsonl"), anyapp=anyapp, auto_small=True, escalate=escalate)
    run = "mcp__odysseus__app_run"
    assert gate._scope(run, {"tool": "GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND", "arguments": {"spreadsheetId": "S1", "values": [[1]]}}) == "googlesheets:S1"
    assert gate._scope(run, {"tool": "GOOGLESHEETS_VALUES_UPDATE", "arguments": {"spreadsheet_id": "S1"}}) == "googlesheets:S1"
    assert gate._scope(run, {"tool": "GOOGLEDRIVE_ADD_FILE_SHARING_PREFERENCE", "arguments": {"file_id": "S1"}}) == "", "sharing is asked about every time"
    assert gate._scope(run, {"tool": "GOOGLESHEETS_DELETE_SHEET", "arguments": {"spreadsheet_id": "S1"}}) == ""
    assert gate._scope(run, {"tool": "SLACK_SEND_MESSAGE", "arguments": {"file_id": "S1"}}) == "", "another app, and a send"
    assert gate._scope("mcp__odysseus__mail_send", {"to": ["a@b.example"]}) == ""
