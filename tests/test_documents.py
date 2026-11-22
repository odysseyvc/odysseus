"""Making documents on the throwaway machine, and looking at them first (sandbox.py). The machine is a stand-in.

Covered: that the tool is described with documents only when the machine can make them, that a trial sends and keeps
nothing and shows the model the pages, that the run after it sends as before, and that a machine without the image
refuses a trial instead of sending files nobody has looked at.
"""
from __future__ import annotations

import base64
from types import SimpleNamespace

from odysseus.files import Shelf
from odysseus.journal import Journal, Ledger
from odysseus.sandbox import LOOK_BYTES, LOOK_CODE, LOOK_PAGES, OUT_DIR, SCHEMAS, CodeRunner, E2B, Ran, schemas
from odysseus.taint import Taint

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200


class Machine:
    """A machine that can make documents: it takes `look`, and says so."""
    documents = True

    def __init__(self, ran):
        self.ran, self.calls = ran, []

    async def run(self, code, files, seconds, look=False):
        self.calls.append((code, len(files), seconds, look))
        return self.ran if look else Ran(stdout=self.ran.stdout, made=self.ran.made, seconds=self.ran.seconds)


class Plain:
    """The company's standard machine: no `look` argument at all."""

    def __init__(self):
        self.calls = []

    async def run(self, code, files, seconds):
        self.calls.append(code)
        return Ran(stdout="ok\n", made=[("deck.pptx", b"PK...")], seconds=2.0)


def rig(tmp_path, machine):
    journal = Journal(tmp_path / "j.jsonl")
    ledger, shelf, sent = Ledger(journal), Shelf(tmp_path / "files"), []

    async def send(entry):
        sent.append(entry)
        return ""
    runner = CodeRunner(machine, journal, ledger, 3.0, Taint())
    return SimpleNamespace(run=runner.handlers(shelf, send)["run_code"], runner=runner, machine=machine, journal=journal, ledger=ledger, shelf=shelf, sent=sent)


def text(out):
    return out["content"][0]["text"]


def deck(**more):
    return Ran(stdout="6 slides written\n", made=[("update.pptx", b"PK-deck")], seconds=20.0,
               looked=[{"name": "update.pptx", "pages": 6, "picture": JPEG, "problem": "", **more}])


def test_the_tool_speaks_of_documents_only_when_the_machine_can_make_them():
    plain, full = schemas(False)["run_code"], schemas(True)["run_code"]
    assert plain == SCHEMAS["run_code"] and "look" not in plain[1]["properties"] and "python-pptx" not in plain[0]
    assert "printed by the browser on the machine" in full[0] and "--print-to-pdf=/home/user/out/name.pdf" in full[0] and "@page { size: A4" in full[0]
    assert "python-pptx" in full[0] and "soffice" in full[0] and "look=true" in full[0] and full[1]["properties"]["look"]["type"] == "boolean"
    assert full[1]["required"] == ["code"], "a trial is offered, never demanded"
    assert "look" not in SCHEMAS["run_code"][1]["properties"], "describing it for documents does not change the plain description"
    assert E2B("k").documents is False and E2B("k", "an-image").documents is True


async def test_a_trial_sends_and_keeps_nothing_and_shows_the_pages(tmp_path):
    x = rig(tmp_path, Machine(deck()))
    out = await x.run({"code": "make()", "look": True})
    assert x.machine.calls == [("make()", 0, 120, True)]
    assert x.sent == [] and x.shelf.all() == [], "nothing reaches the owner or the shelf from a trial"
    assert "THIS WAS A TRIAL" in text(out) and "update.pptx: 6 pages; picture below" in text(out) and "without `look`" in text(out)
    picture = out["content"][1]
    assert picture["type"] == "image" and picture["mimeType"] == "image/jpeg" and base64.b64decode(picture["data"]) == JPEG
    row = x.journal.read()[-1]
    assert row["look"] is True and row["files_out"] == 0 and row["made"] == 1 and row["pictures"] == 1
    assert x.ledger.spent_today() > 0, "a trial is paid for like any run"


async def test_the_run_after_the_trial_sends_the_file_as_before(tmp_path):
    x = rig(tmp_path, Machine(deck()))
    await x.run({"code": "make()", "look": True})
    out = await x.run({"code": "make()"})
    assert [c[3] for c in x.machine.calls] == [True, False]
    assert [e["name"] for e in x.sent] == ["update.pptx"] and len(x.shelf.all()) == 1
    assert len(out["content"]) == 1 and "Sent to the owner in this chat and kept: update.pptx" in text(out)


async def test_look_must_be_true_itself_and_a_word_does_not_make_a_trial(tmp_path):
    x = rig(tmp_path, Machine(deck()))
    await x.run({"code": "make()", "look": "false"})
    assert x.machine.calls[-1][3] is False and len(x.sent) == 1, "anything but true is an ordinary run"


async def test_a_file_that_could_not_be_pictured_says_why_and_shows_no_picture(tmp_path):
    x = rig(tmp_path, Machine(deck(picture=b"", pages=0, problem="it could not be opened to make pictures of it: source file could not be loaded")))
    out = await x.run({"code": "make()", "look": True})
    assert len(out["content"]) == 1 and "pages not counted; it could not be opened" in text(out)
    assert "Look at each picture" not in text(out), "it is not told to look at pictures it was not given"


async def test_a_picture_too_large_is_left_out_and_a_long_file_is_said_to_be_cut(tmp_path):
    ran = Ran(stdout="", made=[("a.pdf", b"%PDF"), ("b.pdf", b"%PDF")], seconds=5.0,
              looked=[{"name": "a.pdf", "pages": 40, "picture": JPEG, "problem": ""},
                      {"name": "b.pdf", "pages": 2, "picture": b"0" * (LOOK_BYTES + 1), "problem": ""}])
    out = await rig(tmp_path, Machine(ran)).run({"code": "make()", "look": True})
    assert f"a.pdf: 40 pages; picture below, first {LOOK_PAGES} pages" in text(out) and "b.pdf: 2 pages; its picture was too large to show" in text(out)
    assert len(out["content"]) == 2


async def test_a_trial_that_wrote_no_file_says_so_and_an_error_is_an_error(tmp_path):
    out = await rig(tmp_path, Machine(Ran(stdout="", error="NameError: prs is not defined\n...", seconds=1.0))).run({"code": "prs.save()", "look": True})
    assert out["is_error"] and f"wrote no file to {OUT_DIR}/" in text(out) and "NameError" in text(out)


async def test_a_machine_without_the_image_refuses_a_trial_and_runs_nothing(tmp_path):
    x = rig(tmp_path, Plain())
    out = await x.run({"code": "make()", "look": True})
    assert out["is_error"] and "not available on this machine" in text(out) and x.machine.calls == [] and x.sent == []
    assert x.runner.documents is False and x.run.documents is False
    ok = await x.run({"code": "make()"})
    assert not ok.get("is_error") and x.machine.calls == ["make()"], "an ordinary run on it is unchanged"


def test_the_code_that_makes_the_pictures_is_fixed_and_reads_only_the_out_folder():
    compile(LOOK_CODE, "look", "exec")
    assert repr(OUT_DIR) in LOOK_CODE and "/home/user/in" not in LOOK_CODE, "it looks at what was made, not at what went in"
    assert "requests" not in LOOK_CODE and "urllib" not in LOOK_CODE
