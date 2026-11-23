"""The reading queue, must-reads and open problems (booknotes.py), beside the reading notes in test_booknotes.py.

The site's Studio writes the same files as the assistant, so the stand-in repository here can also move a file under
a write, more than once, as a second writer would.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
import yaml

from odysseus import booknotes as bn
from odysseus.gate import Gate
from odysseus.journal import Journal

from test_booknotes import NOW, Chat, FakeRepo, HAND_WRITTEN, PATH, shelf

PROBLEM = """title: "Industrial demand generation for SAPL"
status: open
added: 2026-11-24
public: false
why: "Factories buy on trust."
links: ["reading/poor-charlies-almanack"]
notes:
  - at: 2026-11-24T10:00:00+05:30
    kind: idea
    text: Start with the trade fairs.
"""
PPATH = f"{bn.PROBLEMS}/industrial-demand-generation-for-sapl.yaml"


# --- parsing ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("message, action, book, url", [
    ("save https://example.com/essays/why-cities-grow", "save", "", "https://example.com/essays/why-cities-grow"),
    ("save The Power Law by Sebastian Mallaby", "save", "The Power Law by Sebastian Mallaby", ""),
    ("to read: Seeing Like a State", "save", "Seeing Like a State", ""),
    ("to read https://arxiv.org/abs/2401.00001 the scaling paper", "save", "the scaling paper", "https://arxiv.org/abs/2401.00001"),
])
def test_save_and_to_read(message, action, book, url):
    cmd = bn.parse(message, NOW)
    assert (cmd.action, cmd.book, cmd.url) == (action, book, url)


@pytest.mark.parametrize("message", ["save that", "save it to memory", "save this for later", "save my draft", "please remember this"])
def test_save_of_something_else_goes_to_the_model(message):
    assert bn.parse(message, NOW) is None


def test_must_read():
    cmd = bn.parse("must read Poor Charlie: the best book on thinking clearly", NOW)
    assert (cmd.action, cmd.book, cmd.text) == ("must", "Poor Charlie", "the best book on thinking clearly")
    assert bn.parse("Must-read Poor Charlie: x", NOW).action == "must"


@pytest.mark.parametrize("message, book, kind, text, url", [
    ("problem Platform for MODY idea: screen relatives of known cases", "Platform for MODY", "idea", "screen relatives of known cases", ""),
    ("problem SAPL demand question: who signs the purchase order?", "SAPL demand", "question", "who signs the purchase order?", ""),
    ("problem SAPL demand resource: https://example.com/report.pdf", "SAPL demand", "resource", "", "https://example.com/report.pdf"),
    ("problem SAPL demand link: good survey https://example.com/s", "SAPL demand", "resource", "good survey", "https://example.com/s"),
    ("problem SAPL demand progress: met two plant heads", "SAPL demand", "progress", "met two plant heads", ""),
])
def test_problem_entries(message, book, kind, text, url):
    cmd = bn.parse(message, NOW)
    assert (cmd.action, cmd.book, cmd.kind, cmd.text, cmd.url) == ("entry", book, kind, text, url)
    assert cmd.at == NOW


def test_new_problem():
    cmd = bn.parse("problem Cheaper glucose monitors: half of India cannot afford one", NOW)
    assert (cmd.action, cmd.book, cmd.text) == ("problem", "Cheaper glucose monitors", "half of India cannot afford one")


def test_link_kinds():
    assert bn.link_kind("https://www.youtube.com/watch?v=x") == "video"
    assert bn.link_kind("https://arxiv.org/abs/1") == "paper"
    assert bn.link_kind("https://example.com/a.pdf") == "paper"
    assert bn.link_kind("https://podcasts.apple.com/us/podcast/x") == "podcast"
    assert bn.link_kind("https://example.com/post") == "article"
    assert bn._title_from_address("https://example.com/essays/why-cities-grow") == "Why Cities Grow"


# --- the schema ------------------------------------------------------------------------------------------------------

def test_new_fields_are_checked():
    ok = "title: X\nstatus: queued\nadded: 2026-11-24\npublic: true\nrecommended: true\npitch: Read it.\nlinks: [problems/x-y, writing/z]\n"
    assert bn.validate(ok)["status"] == "queued"
    for bad, words in [("title: X\nrecommended: 'yes'\n", "recommended"), ("title: X\nlinks: [elsewhere/x]\n", "links"),
                       ("title: X\nadded: soon\n", "added"), ("title: X\npitch: [1]\n", "pitch")]:
        with pytest.raises(bn.BookError, match=words):
            bn.validate(bad)


def test_quote_cap():
    long = bn.append_note("title: X\n", bn.Note(at=NOW, kind="quote", quote="w" * 601))
    with pytest.raises(bn.BookError, match="600"):
        bn.validate(long)
    bn.validate(bn.append_note("title: X\n", bn.Note(at=NOW, kind="quote", quote="w" * 600)))


def test_problem_schema():
    assert bn.validate(PROBLEM, bn.PROBLEMS)["status"] == "open"
    for bad, words in [(PROBLEM.replace("status: open", "status: done"), "status"),
                       (PROBLEM.replace("kind: idea", "kind: thought"), "kind"),
                       (PROBLEM + "  - at: 2026-11-24T11:00:00+05:30\n    kind: resource\n    url: ftp://x\n", "web address"),
                       (PROBLEM + "  - at: 2026-11-24T11:00:00+05:30\n    kind: idea\n", "text or a link")]:
        with pytest.raises(bn.BookError, match=words):
            bn.validate(bad, bn.PROBLEMS)


# --- the queue -------------------------------------------------------------------------------------------------------

async def test_save_a_title_queues_a_private_book(tmp_path):
    s, repo, _ = shelf(tmp_path)
    reply = await s.save(bn.parse("save the power law by Sebastian Mallaby", NOW))
    book = yaml.safe_load(repo.files[f"{bn.FOLDER}/the-power-law.yaml"])
    assert (book["title"], book["author"], book["status"], str(book["added"]), book["public"]) == ("The Power Law", "Sebastian Mallaby", "queued", "2026-11-24", False)
    assert "started" not in book and "notes" not in book
    assert "To read" in reply and repo.commits[-1][1] == "reading: The Power Law"


async def test_save_a_link_takes_the_pages_title(tmp_path):
    repo = FakeRepo()
    seen = []

    async def title_of(url):
        seen.append(url)
        return "Why Cities Grow: A Long Essay"
    s = bn.Shelf(repo, Journal(tmp_path / "j.jsonl"), now=lambda: NOW, title_of_link=title_of)
    await s.save(bn.parse("save https://example.com/essays/why-cities-grow", NOW))
    book = yaml.safe_load(repo.files[f"{bn.FOLDER}/why-cities-grow-a-long-essay.yaml"])
    assert (book["title"], book["kind"], book["url"], book["status"]) == ("Why Cities Grow: A Long Essay", "article", "https://example.com/essays/why-cities-grow", "queued")
    assert seen == ["https://example.com/essays/why-cities-grow"]


async def test_save_twice_does_not_make_a_second_file(tmp_path):
    s, repo, _ = shelf(tmp_path, {PATH: HAND_WRITTEN})
    assert "already on your shelf (reading)" in await s.save(bn.parse("save Poor Charlie's Almanack", NOW))
    assert repo.commits == []


async def test_first_note_on_a_queued_book_starts_it(tmp_path):
    s, repo, clock = shelf(tmp_path)
    await s.save(bn.parse("save The Power Law", NOW))
    clock.t = NOW + timedelta(days=2)
    reply = await s.add_note(bn.parse("reading power law p.3: power laws hide in plain sight", NOW + timedelta(days=2)))
    book = yaml.safe_load(repo.files[f"{bn.FOLDER}/the-power-law.yaml"])
    assert (book["status"], str(book["started"]), str(book["added"])) == ("reading", "2026-11-26", "2026-11-24")
    assert len(book["notes"]) == 1 and "from To read to Reading" in reply
    await s.add_note(bn.parse("reading power law p.9: second", NOW + timedelta(days=2)))
    assert "To read" not in (await s.add_note(bn.parse("reading power law p.10: third", NOW + timedelta(days=2))))


async def test_a_studio_file_with_no_notes_takes_a_note(tmp_path):
    studio = 'title: "Seeing Like a State"\nkind: book\nstatus: queued\nadded: 2026-11-20\npublic: false\n'
    s, repo, _ = shelf(tmp_path, {f"{bn.FOLDER}/seeing-like-a-state.yaml": studio})
    await s.add_note(bn.parse("reading seeing like a state ch. 1: legibility is a kind of map", NOW))
    raw = repo.files[f"{bn.FOLDER}/seeing-like-a-state.yaml"]
    book = bn.validate(raw)
    assert book["status"] == "reading" and book["notes"][0]["where"] == "ch. 1" and raw.startswith('title: "Seeing Like a State"\n')


# --- must read, through the gate ---------------------------------------------------------------------------------------

async def _asked(chat):
    for _ in range(100):
        if chat.asks:
            return chat.asks[-1]
        await asyncio.sleep(0.01)
    raise AssertionError("nothing was asked")


async def test_must_read_shows_before_and_after_and_waits_for_the_tap(tmp_path):
    raw = bn.mark_last_private(HAND_WRITTEN)
    s, repo, _ = shelf(tmp_path, {PATH: raw})
    journal = Journal(tmp_path / "journal.jsonl")
    gate = Gate(journal)
    desk, chat = bn.Desk(s, gate, journal), Chat()
    assert await desk.take("must read poor charlie: the clearest book on thinking I know", chat)
    text, approval_id = await _asked(chat)
    assert "Must read: no → yes" in text and "Public: no → yes" in text
    assert "Pitch: (none) → the clearest book on thinking I know" in text
    assert "this also publishes it" in text and "1 private note stays off the site" in text
    assert yaml.safe_load(repo.files[PATH]).get("recommended") is None          # nothing before the tap
    gate.resolve(approval_id, True)
    await asyncio.wait_for(chat.sent_event.wait(), 2)
    book = bn.validate(repo.files[PATH])
    assert (book["recommended"], book["public"], book["pitch"]) == (True, True, "the clearest book on thinking I know")
    assert bn.earlier_kept(raw, repo.files[PATH], 2) and "Must read shelf" in chat.sent[-1]


async def test_must_read_of_an_unknown_book_says_so(tmp_path):
    s, repo, _ = shelf(tmp_path)
    journal = Journal(tmp_path / "journal.jsonl")
    desk, chat = bn.Desk(s, Gate(journal), journal), Chat()
    assert await desk.take("must read Antifragile: everyone should", chat)
    assert "Save it first" in chat.sent[0] and not chat.asks and repo.commits == []


async def test_declined_must_read_changes_nothing(tmp_path):
    s, repo, _ = shelf(tmp_path, {PATH: HAND_WRITTEN})
    journal = Journal(tmp_path / "journal.jsonl")
    gate = Gate(journal)
    desk, chat = bn.Desk(s, gate, journal), Chat()
    await desk.take("must read poor charlie: yes", chat)
    _, approval_id = await _asked(chat)
    gate.resolve(approval_id, False)
    await asyncio.wait_for(chat.sent_event.wait(), 2)
    assert repo.files[PATH] == HAND_WRITTEN and repo.commits == []


# --- problems --------------------------------------------------------------------------------------------------------

async def test_new_problem_is_private_and_open(tmp_path):
    s, repo, _ = shelf(tmp_path)
    reply = await s.problem_new(bn.parse("problem Cheaper glucose monitors: half the patients cannot afford one", NOW))
    raw = repo.files[f"{bn.PROBLEMS}/cheaper-glucose-monitors.yaml"]
    book = bn.validate(raw, bn.PROBLEMS)
    assert (book["status"], book["public"], str(book["added"]), book["why"]) == ("open", False, "2026-11-24", "half the patients cannot afford one")
    assert repo.commits[-1][1] == "problems: Cheaper glucose monitors" and "New open problem" in reply
    assert "already a problem" in await s.problem_new(bn.parse("problem Cheaper glucose monitors: again", NOW))


async def test_entries_append_to_an_existing_problem(tmp_path):
    s, repo, _ = shelf(tmp_path, {PPATH: PROBLEM})
    await s.problem_entry(bn.parse("problem industrial demand question: who signs the PO?", NOW))
    reply = await s.problem_entry(bn.parse("problem industrial demand resource: survey https://example.com/s.pdf", NOW))
    after = repo.files[PPATH]
    assert after.startswith(PROBLEM) and bn.earlier_kept(PROBLEM, after, 1)
    notes = bn.validate(after, bn.PROBLEMS)["notes"]
    assert notes[1] == {"at": NOW, "kind": "question", "text": "who signs the PO?"}
    assert notes[2] == {"at": NOW, "kind": "resource", "text": "survey", "url": "https://example.com/s.pdf"}
    assert "entry 3" in reply
    # "keep that private" now means that entry
    assert "entry" in await s.private_last()
    assert bn.validate(repo.files[PPATH], bn.PROBLEMS)["notes"][-1]["private"] is True


async def test_entry_for_a_missing_problem_says_how_to_start_one(tmp_path):
    s, repo, _ = shelf(tmp_path)
    reply = await s.problem_entry(bn.parse("problem Teleportation idea: tunnels", NOW))
    assert "Start one with: problem Teleportation: why it matters." in reply and repo.commits == []


# --- two writers -------------------------------------------------------------------------------------------------------

async def test_a_file_moved_twice_by_the_studio_still_gets_the_note(tmp_path):
    s, repo, _ = shelf(tmp_path, {PATH: HAND_WRITTEN})
    moves = [0]
    real_put = repo.put

    async def put(path, text, sha, message):
        if moves[0] < 2:                    # the Studio writes the same file twice, each just before our write
            moves[0] += 1
            repo.files[path] = bn.append_note(repo.files[path], bn.Note(at=NOW, text=f"from the studio {moves[0]}"))
        return await real_put(path, text, sha, message)
    repo.put = put
    await s.add_note(bn.parse("reading poor charlie: from the phone", NOW))
    texts = [n.get("text") for n in yaml.safe_load(repo.files[PATH])["notes"]]
    assert texts[-3:] == ["from the studio 1", "from the studio 2", "from the phone"]


async def test_a_file_that_never_stops_moving_is_reported(tmp_path):
    s, repo, _ = shelf(tmp_path, {PATH: HAND_WRITTEN})
    real_put = repo.put

    async def put(path, text, sha, message):
        repo.files[path] = repo.files[path] + "  - at: 2026-11-24T23:00:00+05:30\n    text: x\n"
        return await real_put(path, text, sha, message)
    repo.put = put
    with pytest.raises(bn.BookError, match="kept changing"):
        await s.add_note(bn.parse("reading poor charlie: from the phone", NOW))
