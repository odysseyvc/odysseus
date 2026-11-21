"""Running code on a throwaway machine (sandbox.py). The machine is a stand-in; no service is called.

Covered: what goes in and what comes back, the limits, that a made file is kept and sent to the owner only, that the
run is counted in the ledger, that output derived from a file is recorded as other people's words, and the gate rule.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from odysseus.files import Shelf
from odysseus.journal import Journal, Ledger
from odysseus.sandbox import FILES_IN, FILES_OUT, IN_DIR, MATERIAL, MAX_CODE, MAX_OUTPUT, NOTE, OUT_DIR, SCHEMAS, USD_PER_S, CodeRunner, Ran, _safe
from odysseus.taint import Taint
from odysseus.tools import make_gate


class Machine:
    def __init__(self, ran=None, fail=None):
        self.ran, self.fail, self.calls = ran or Ran(stdout="42\n", seconds=3.0), fail, []

    async def run(self, code, files, seconds):
        self.calls.append((code, files, seconds))
        if self.fail:
            raise self.fail
        return self.ran


def rig(tmp_path, daily=3.0, **kw):
    journal = Journal(tmp_path / "j.jsonl")
    ledger, taint, shelf, machine, sent = Ledger(journal), Taint(), Shelf(tmp_path / "files"), Machine(**kw), []

    async def send(entry):
        sent.append(entry)
        return ""
    run = CodeRunner(machine, journal, ledger, daily, taint).handlers(shelf, send)["run_code"]
    return SimpleNamespace(run=run, machine=machine, journal=journal, ledger=ledger, taint=taint, shelf=shelf, sent=sent)


def text(out):
    return out["content"][0]["text"]


async def test_code_runs_and_what_it_printed_comes_back_counted(tmp_path):
    x = rig(tmp_path)
    out = await x.run({"code": "print(6 * 7)"})
    assert not out.get("is_error") and text(out).startswith(NOTE) and "Printed:\n42" in text(out)
    assert x.machine.calls == [("print(6 * 7)", [], 120)]
    row = x.journal.read()[-1]
    assert row["name"] == "run_code" and row["ok"] and row["seconds"] == 3.0 and row["cost_usd"] == pytest.approx(3 * USD_PER_S, abs=1e-5)
    assert x.ledger.spent_today() == pytest.approx(row["cost_usd"]), "the ledger counts the run from its log line"
    assert x.taint.sources == set(), "code run on nothing is the model's own work, not something read"


async def test_kept_files_go_in_by_id_and_the_output_is_recorded_as_derived_from_them(tmp_path):
    x = rig(tmp_path, ran=Ran(stdout="total 41883920 for Corvid Freight seed round investors list\n", seconds=2.0))
    a = x.shelf.add(b"a,b\n1,2\n", "cap table.csv", "text/csv")
    b = x.shelf.add(b"other", "../../etc/cap table.csv", "text/csv")
    out = await x.run({"code": "import pandas", "files": [a["id"], b["id"]]})
    (_, files, _), = x.machine.calls
    assert [n for n, _ in files] == ["cap table.csv", "_cap table.csv"] and files[0][1] == b"a,b\n1,2\n", "names are made safe and kept apart"
    assert f"under {IN_DIR}: cap table.csv, _cap table.csv" in text(out)
    assert MATERIAL in x.taint.sources and x.taint.leaked("who has 41883920"), "what came out of a file is private like the file"
    assert _safe("..\\..\\x/../boot.ini") == "boot.ini" and _safe("///") == "file"


async def test_a_file_the_code_made_is_kept_and_sent_to_the_owner(tmp_path):
    made = [(f"chart{i}.png", b"\x89PNG" + bytes([i])) for i in range(FILES_OUT + 2)]
    x = rig(tmp_path, ran=Ran(stdout="", made=made, seconds=1.0))
    out = await x.run({"code": "plot()"})
    assert len(x.sent) == FILES_OUT and [e["name"] for e in x.sent] == ["chart0.png", "chart1.png", "chart2.png"]
    kept = x.shelf.get(x.sent[0]["id"])
    assert x.shelf.data(kept) == made[0][1] and kept["source"] == "code" and kept["origin"] == "made by code I ran"
    assert "Sent to the owner in this chat and kept: chart0.png (id " in text(out) and "Printed:\n(nothing)" in text(out)
    assert x.journal.read()[-1]["files_out"] == FILES_OUT


async def test_what_is_refused_before_any_machine_is_started(tmp_path):
    x = rig(tmp_path)
    for args, why in (({"code": "  "}, "no code"), ({"code": "x" * (MAX_CODE + 1)}, "Too long"),
                      ({"code": "x", "files": ["a"] * (FILES_IN + 1)}, "At most"), ({"code": "x", "files": ["nope"]}, "No kept file")):
        out = await x.run(args)
        assert out.get("is_error") and why.lower() in text(out).lower(), why
    spent = rig(tmp_path / "spent", daily=0.01)
    spent.ledger.add(0.01)
    assert "budget" in text(await spent.run({"code": "print(1)"}))
    assert x.machine.calls == [] and spent.machine.calls == []


async def test_an_error_in_the_code_or_in_the_service_is_said_plainly(tmp_path):
    x = rig(tmp_path, ran=Ran(stdout="start\n" + "y" * (MAX_OUTPUT + 500), stderr="a warning", error="ZeroDivisionError: division by zero\nTraceback...", seconds=1.0))
    out = await x.run({"code": "1/0"})
    assert out.get("is_error") and "ended with an error:\nZeroDivisionError" in text(out) and "Warnings:\na warning" in text(out)
    assert "[Cut:" in text(out) and OUT_DIR in text(out) and x.journal.read()[-1]["error"] == "ZeroDivisionError"
    down = rig(tmp_path / "down", fail=ConnectionError("no route"))
    out = await down.run({"code": "print(1)"})
    assert out.get("is_error") and "could not be run: ConnectionError" in text(out)
    assert down.journal.read()[-1]["ok"] is False and down.ledger.spent_today() == 0


def test_it_runs_unasked_and_is_not_offered_to_unattended_runs(tmp_path):
    from odysseus.tools import ROUTINE_TOOLS, routine_tools
    gate = make_gate(Journal(tmp_path / "j.jsonl"), taint_file=tmp_path / "t.bin")
    assert gate._rules["mcp__odysseus__run_code"].constant == "allow"
    assert "mcp__odysseus__run_code" not in ROUTINE_TOOLS | routine_tools(True)
    assert "no internet" in SCHEMAS["run_code"][0] and SCHEMAS["run_code"][1]["required"] == ["code"]
