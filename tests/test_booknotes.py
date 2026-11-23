"""Reading notes (booknotes.py): messages parsed into notes, notes appended without touching earlier ones, the file
checked against the site's schema, and publishing held at the gate.

GitHub is a stand-in here: a dict of files with a sha for each, refusing a write whose sha is stale, as the real
contents API does.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta

import pytest
import yaml

from odysseus import booknotes as bn
from odysseus.gate import Gate
from odysseus.journal import Journal

NOW = datetime(2026, 11, 24, 21, 5, tzinfo=bn.IST)


# --- parsing --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("message, book, where, kind, text, quote", [
    ("reading Poor Charlie's Almanack p.120: incentives explain more than intelligence",
     "Poor Charlie's Almanack", "p. 120", "thought", "incentives explain more than intelligence", ""),
    ('reading Sapiens: A Brief History p.12: "Money is a system of mutual trust" - so is language',
     "Sapiens: A Brief History", "p. 12", "quote", "so is language", "Money is a system of mutual trust"),
    ("reading Sapiens 40%: why did foragers work less?", "Sapiens", "40%", "question", "why did foragers work less?", ""),
    ("reading Sapiens page 5 disagree: the wheat thesis overreaches", "Sapiens", "p. 5", "disagree", "the wheat thesis overreaches", ""),
    ("reading Sapiens p.88: I don't buy the wheat argument", "Sapiens", "p. 88", "disagree", "I don't buy the wheat argument", ""),
    ("reading Acquired 1:02:33: reminds me of Munger on incentives", "Acquired", "1:02:33", "connection", "reminds me of Munger on incentives", ""),
    ("reading Sapiens loc 1234 quote: Culture forbids only what is unnatural.", "Sapiens", "loc. 1234", "quote", "", "Culture forbids only what is unnatural."),
    ("reading Sapiens: question: what did foragers eat", "Sapiens", "", "question", "what did foragers eat", ""),
    ("reading Sapiens ch. 3: “Gossip built cooperation.”", "Sapiens", "ch. 3", "quote", "", "Gossip built cooperation."),
    ("reading Sapiens: connection: same idea as Dunbar's number", "Sapiens", "", "connection", "same idea as Dunbar's number", ""),
])
def test_messages_become_notes(message, book, where, kind, text, quote):
    cmd = bn.parse(message, NOW)
    assert cmd is not None and cmd.action == "note"
    assert (cmd.book, cmd.note.where, cmd.note.kind, cmd.note.text, cmd.note.quote) == (book, where, kind, text, quote)
    assert cmd.note.at.utcoffset() == timedelta(hours=5, minutes=30)


def test_message_time_is_given_the_india_offset():
    from datetime import timezone
    cmd = bn.parse("reading Sapiens: a thought", datetime(2026, 11, 24, 15, 35, 12, 999, tzinfo=timezone.utc))
    assert bn._at(cmd.note.at) == "2026-11-24T21:05:12+05:30"


@pytest.mark.parametrize("message, action, book, text", [
    ("keep that private", "private", "", ""),
    ("Keep it private.", "private", "", ""),
    ("publish Sapiens", "publish", "Sapiens", ""),
    ("finished Sapiens: humans run on shared fictions", "finish", "Sapiens", "humans run on shared fictions"),
    ("finished Sapiens", "finish", "Sapiens", ""),
    ("why Sapiens: everyone I respect cites it", "why", "Sapiens", "everyone I respect cites it"),
])
def test_other_commands(message, action, book, text):
    cmd = bn.parse(message, NOW)
    assert (cmd.action, cmd.book, cmd.text) == (action, book, text)


@pytest.mark.parametrize("message", ["reading the deck now", "what are you reading: anything?", "read Sapiens: x", "reading Sapiens:",
                                     "please keep that private from Ana", "hello"])
def test_other_messages_are_not_taken(message):
    assert bn.parse(message, NOW) is None


def test_slug_follows_the_contract():
    assert bn.slugify("Poor Charlie's Almanack") == "poor-charlies-almanack"
    assert bn.slugify("Sapiens: A Brief History of Humankind") == "sapiens-a-brief-history-of-humankind"
    assert bn.slugify("Café Society — 2nd ed.") == "cafe-society-2nd-ed"


# --- the file ---------------------------------------------------------------------------------------------------------

HAND_WRITTEN = """title: Poor Charlie's Almanack
author: Charlie Munger
kind: book          # book, article, paper, essay, podcast, video
status: reading     # reading, finished, paused, abandoned
started: 2026-11-24
finished:           # date, when done
public: false
why: Why I picked it up.            # before reading
topics: [ideas]     # ai, venture, science, ideas
notes:
  - at: 2026-11-24T21:05:00+05:30   # ISO time with offset
    where: p. 120                    # page, %, chapter or timestamp
    kind: quote                      # thought, quote, question, disagree, connection
    quote: >-
      The passage, word for word,
      over two lines.
    text: What I think about it.
  - at: 2026-11-24T21:40:00+05:30
    kind: question
    text: An open question.
    resolved: false                  # questions stay under "Still wondering" until true
"""


def _note(**kw) -> bn.Note:
    return bn.Note(at=NOW + timedelta(hours=1), **({"text": "Later thought: it's 12:30 and # not a comment"} | kw))


def test_append_keeps_every_earlier_byte():
    after = bn.append_note(HAND_WRITTEN, _note(where="p. 140"))
    assert after.startswith(HAND_WRITTEN)
    book = bn.validate(after)
    assert len(book["notes"]) == 3 and book["notes"][-1]["text"] == "Later thought: it's 12:30 and # not a comment"
    assert book["notes"][-1]["where"] == "p. 140" and bn._at(book["notes"][-1]["at"]) == "2026-11-24T22:05:00+05:30"
    assert bn.earlier_kept(HAND_WRITTEN, after, 2)


def test_append_many_awkward_notes_in_a_row():
    raw = HAND_WRITTEN
    awkward = ["multi\nline\n\nwith a gap", "'quoted'", "- a dash", "key: value", "yes", "123", "@at", "“curly”", "null"]
    for i, text in enumerate(awkward):
        before = raw
        raw = bn.append_note(raw, _note(text=text, where="12:30" if i % 2 else "p. 9"))
        assert raw.startswith(before)
        assert bn.earlier_kept(before, raw, 2 + i)
    notes = bn.validate(raw)["notes"]
    assert [n["text"] for n in notes[2:]] == awkward
    assert {n["where"] for n in notes[2:]} == {"12:30", "p. 9"}


def test_append_to_a_file_with_no_notes_or_an_empty_list():
    for raw in ["title: X\nstarted: 2026-11-24\n", "title: X\nstarted: 2026-11-24\nnotes: []\n", "title: X\nstarted: 2026-11-24\nnotes:\n"]:
        book = bn.validate(bn.append_note(raw, _note()))
        assert len(book["notes"]) == 1


def test_append_when_notes_is_not_the_last_field():
    raw = "title: X\nstarted: 2026-11-24\nnotes:\n    - at: 2026-11-24T10:00:00+05:30\n      text: first\npublic: false\n"
    after = bn.append_note(raw, _note(text="second"))
    book = bn.validate(after)
    assert [n["text"] for n in book["notes"]] == ["first", "second"] and book["public"] is False
    assert bn.earlier_kept(raw, after, 1)


def test_new_book_is_private_and_fits_the_schema():
    raw = bn.new_book("Poor Charlie's Almanack", "Charlie Munger", NOW.date(), _note(kind="quote", quote="Invert, always invert", text=""))
    book = bn.validate(raw)
    assert book["public"] is False and book["status"] == "reading" and str(book["started"]) == "2026-11-24"
    assert book["notes"][0] == {"at": NOW + timedelta(hours=1), "kind": "quote", "quote": "Invert, always invert"}


def test_keep_that_private_changes_only_the_last_note():
    raw = bn.append_note(HAND_WRITTEN, _note())
    after = bn.mark_last_private(raw)
    assert bn.earlier_kept(raw, after, 2)
    assert bn.validate(after)["notes"][-1]["private"] is True
    assert bn.mark_last_private(after) == after          # twice is the same as once
    flipped = after.replace("private: true", "private: false")
    assert bn.mark_last_private(flipped) == after


def test_set_fields_leaves_the_notes_alone():
    after = bn.set_fields(HAND_WRITTEN, {"status": "finished", "finished": NOW.date(), "takeaway": "Incentives: everything.", "public": True})
    book = bn.validate(after)
    assert (book["status"], str(book["finished"]), book["takeaway"], book["public"]) == ("finished", "2026-11-24", "Incentives: everything.", True)
    assert book["why"] == "Why I picked it up."
    assert bn.earlier_kept(HAND_WRITTEN, after, 2)
    with pytest.raises(ValueError):
        bn.set_fields(HAND_WRITTEN, {"notes": []})


@pytest.mark.parametrize("bad, words", [
    ("title: X\n", "started"),
    ("title: X\nstarted: 2026-11-24\nstatus: done\n", "status"),
    ("title: X\nstarted: 2026-11-24\nnotes:\n  - at: 2026-11-24T10:00:00+05:30\n    kind: thought\n", "text or a quote"),
    ("title: X\nstarted: 2026-11-24\nnotes:\n  - at: 2026-11-24T10:00:00+05:30\n    where: 120\n    text: t\n", "where"),
    ("title: X\nstarted: 2026-11-24\ntopics: [cooking]\n", "topics"),
    ("title: X\nstarted: 2026-11-24\npublic: 'yes'\n", "public"),
    ("title: [unclosed\n", "YAML"),
])
def test_validation_refuses_what_the_site_would_refuse(bad, words):
    with pytest.raises(bn.BookError, match=words):
        bn.validate(bad)


def test_earlier_kept_catches_a_rewritten_note():
    edited = HAND_WRITTEN.replace("An open question.", "An open question!")
    assert not bn.earlier_kept(HAND_WRITTEN, bn.append_note(edited, _note()), 2)


# --- the shelf, against a stand-in repository ------------------------------------------------------------------------

class FakeRepo:
    def __init__(self, files: dict[str, str] | None = None):
        self.files = dict(files or {})
        self.commits: list[tuple[str, str]] = []
        self.move_before_next_put = None

    @staticmethod
    def _sha(text: str) -> str:
        return hashlib.sha1(text.encode()).hexdigest()

    async def list(self, folder):
        return [p.split("/")[-1] for p in self.files if p.startswith(folder + "/")]

    async def get(self, path):
        return (self.files[path], self._sha(self.files[path])) if path in self.files else None

    async def put(self, path, text, sha, message):
        if self.move_before_next_put is not None:
            self.files[path], self.move_before_next_put = self.move_before_next_put(self.files[path]), None
        if (path in self.files) != (sha is not None) or (sha is not None and sha != self._sha(self.files[path])):
            raise bn.Conflict()
        self.files[path] = text
        self.commits.append((path, message))
        return self._sha(text + message)


class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        return self.t


def shelf(tmp_path, files=None):
    repo, clock = FakeRepo(files), Clock()
    return bn.Shelf(repo, Journal(tmp_path / "journal.jsonl"), state=tmp_path / "reading.json", now=clock), repo, clock


PATH = f"{bn.FOLDER}/poor-charlies-almanack.yaml"


async def test_first_note_creates_a_private_book(tmp_path):
    s, repo, _ = shelf(tmp_path)
    reply = await s.add_note(bn.parse("reading poor charlie's almanack by Charlie Munger p.120: incentives first", NOW))
    book = yaml.safe_load(repo.files[PATH])
    assert book["title"] == "Poor Charlie's Almanack" and book["author"] == "Charlie Munger" and book["public"] is False
    assert repo.commits == [(PATH, "reading: Poor Charlie's Almanack")]
    assert "New book, private" in reply and "note 1" in reply


async def test_later_notes_find_the_book_by_part_of_its_title(tmp_path):
    s, repo, _ = shelf(tmp_path, {PATH: HAND_WRITTEN})
    reply = await s.add_note(bn.parse("reading poor charlie p.200: second thought", NOW))
    assert repo.files[PATH].startswith(HAND_WRITTEN) and "note 3" in reply
    assert repo.commits == [(PATH, "reading: Poor Charlie's Almanack")]


async def test_two_books_that_fit_are_not_guessed_between(tmp_path):
    s, repo, _ = shelf(tmp_path, {f"{bn.FOLDER}/sapiens.yaml": "x", f"{bn.FOLDER}/sapiens-graphic.yaml": "x", f"{bn.FOLDER}/homo-deus.yaml": "x"})
    s2, _, _ = shelf(tmp_path, {f"{bn.FOLDER}/sapiens-vol-1.yaml": "x", f"{bn.FOLDER}/sapiens-vol-2.yaml": "x"})
    assert "More than one" in await s2.add_note(bn.parse("reading sapiens: a thought", NOW))
    assert (await s.find("sapiens")).slug == "sapiens"           # an exact match wins over a longer one


async def test_a_note_written_while_the_file_moved_is_retried_on_the_new_file(tmp_path):
    s, repo, _ = shelf(tmp_path, {PATH: HAND_WRITTEN})
    # The stand-in moves the file once, just before the first write; the retry reads it again and succeeds.
    repo.move_before_next_put = lambda raw: bn.append_note(raw, _note(text="added on the laptop"))
    await s.add_note(bn.parse("reading poor charlie: from the phone", NOW))
    texts = [n["text"] for n in yaml.safe_load(repo.files[PATH])["notes"]]
    assert texts[-2:] == ["added on the laptop", "from the phone"] and texts[0] == "What I think about it."


async def test_keep_that_private_marks_the_note_just_filed_and_only_that(tmp_path):
    s, repo, clock = shelf(tmp_path, {PATH: HAND_WRITTEN})
    assert await s.private_last() == ""          # nothing filed lately: the words are about something else
    await s.add_note(bn.parse("reading poor charlie: too raw to publish", NOW))
    before = repo.files[PATH]
    assert "stays private" in await s.private_last()
    notes = yaml.safe_load(repo.files[PATH])["notes"]
    assert notes[-1]["private"] is True and not any(n.get("private") for n in notes[:-1])
    assert bn.earlier_kept(before, repo.files[PATH], 2)
    # After a restart the note is still known; after half an hour it is not.
    again = bn.Shelf(repo, Journal(tmp_path / "journal.jsonl"), state=tmp_path / "reading.json", now=clock)
    assert again._last() is not None
    clock.t = NOW + timedelta(minutes=31)
    assert again._last() is None


async def test_keep_that_private_refuses_if_a_newer_note_came_in(tmp_path):
    s, repo, _ = shelf(tmp_path, {PATH: HAND_WRITTEN})
    await s.add_note(bn.parse("reading poor charlie: mine", NOW))
    repo.files[PATH] = bn.append_note(repo.files[PATH], _note(text="someone else's edit"))
    with pytest.raises(bn.BookError, match="newer note"):
        await s.private_last()


async def test_finished_and_why(tmp_path):
    s, repo, clock = shelf(tmp_path, {PATH: HAND_WRITTEN})
    clock.t = NOW + timedelta(days=3)
    reply = await s.set_finished("poor charlie", "Incentives explain most things.")
    book = yaml.safe_load(repo.files[PATH])
    assert (book["status"], str(book["finished"]), book["takeaway"]) == ("finished", "2026-11-27", "Incentives explain most things.")
    assert "27 Nov 2026" in reply
    reply = await s.set_why("poor charlie", "A friend's recommendation")
    assert yaml.safe_load(repo.files[PATH])["why"] == "A friend's recommendation" and "Why I picked it up." in reply
    assert bn.earlier_kept(HAND_WRITTEN, repo.files[PATH], 2)
    assert await s.set_why("the quarterly report", "x") == ""      # no such book: not ours to answer


async def test_publish_shows_what_goes_public_and_leaves_private_notes_out(tmp_path):
    raw = bn.mark_last_private(bn.append_note(HAND_WRITTEN, _note(text="a secret thought")))
    s, repo, _ = shelf(tmp_path, {PATH: raw})
    question, args = await s.publish_question("poor charlie")
    assert "2 notes will be public; 1 private note stays off the site." in question
    assert "a secret thought" not in question and "An open question." in question
    assert "/reading/poor-charlies-almanack" in question
    assert "Publish" in await s.publish(args["book"], args["sha"])
    assert yaml.safe_load(repo.files[PATH])["public"] is True
    assert "already public" in await s.publish_question("poor charlie")


async def test_publish_refuses_if_the_book_changed_after_the_question(tmp_path):
    s, repo, _ = shelf(tmp_path, {PATH: HAND_WRITTEN})
    _, args = await s.publish_question("poor charlie")
    await s.add_note(bn.parse("reading poor charlie: added while the question was open", NOW))
    assert "changed after I asked" in await s.publish(args["book"], args["sha"])
    assert yaml.safe_load(repo.files[PATH])["public"] is False


# --- the desk: the conversation's side, with the real gate ----------------------------------------------------------

class Chat:
    def __init__(self):
        self.sent, self.asks = [], []
        self.sent_event = asyncio.Event()

    async def send(self, text):
        self.sent.append(text)
        self.sent_event.set()

    async def ask(self, text, approval_id):
        self.asks.append((text, approval_id))


async def test_publish_waits_for_the_owners_tap(tmp_path):
    s, repo, _ = shelf(tmp_path, {PATH: HAND_WRITTEN})
    journal = Journal(tmp_path / "journal.jsonl")
    gate = Gate(journal)
    desk = bn.Desk(s, gate, journal)
    chat = Chat()
    assert await desk.take("publish poor charlie", chat)
    for _ in range(50):
        if chat.asks:
            break
        await asyncio.sleep(0.01)
    text, approval_id = chat.asks[0]
    assert text.startswith('Publish "Poor Charlie\'s Almanack" by Charlie Munger')
    assert yaml.safe_load(repo.files[PATH])["public"] is False        # nothing happens before the tap
    gate.resolve(approval_id, True)
    await asyncio.wait_for(chat.sent_event.wait(), 2)
    assert yaml.safe_load(repo.files[PATH])["public"] is True and chat.sent[-1].startswith("Published")


async def test_declined_publish_leaves_the_book_private(tmp_path):
    s, repo, _ = shelf(tmp_path, {PATH: HAND_WRITTEN})
    journal = Journal(tmp_path / "journal.jsonl")
    gate = Gate(journal)
    desk, chat = bn.Desk(s, gate, journal), Chat()
    await desk.take("publish poor charlie", chat)
    for _ in range(50):
        if chat.asks:
            break
        await asyncio.sleep(0.01)
    gate.resolve(chat.asks[0][1], False)
    await asyncio.wait_for(chat.sent_event.wait(), 2)
    assert chat.sent == ["Left private."] and yaml.safe_load(repo.files[PATH])["public"] is False


async def test_desk_passes_on_what_is_not_a_reading_command(tmp_path):
    s, repo, _ = shelf(tmp_path, {PATH: HAND_WRITTEN})
    journal = Journal(tmp_path / "journal.jsonl")
    desk, chat = bn.Desk(s, Gate(journal), journal), Chat()
    assert not await desk.take("what's on my calendar?", chat)
    assert not await desk.take("finished the deck: sent it to Ana", chat)        # no such book
    assert not await desk.take("keep that private", chat)                        # nothing filed lately
    assert not await desk.take("publish the memo", chat)
    assert chat.sent == [] and repo.commits == []


async def test_desk_files_a_note_and_says_so(tmp_path):
    s, repo, _ = shelf(tmp_path)
    journal = Journal(tmp_path / "journal.jsonl")
    desk, chat = bn.Desk(s, Gate(journal), journal), Chat()
    assert await desk.take("reading Sapiens p.12: is money a story?", chat, NOW)
    assert "Filed a question at p. 12 in Sapiens" in chat.sent[0]
    note = yaml.safe_load(repo.files[f"{bn.FOLDER}/sapiens.yaml"])["notes"][0]
    assert note["kind"] == "question" and note["at"] == NOW


async def test_conversation_hands_reading_messages_to_the_desk(tmp_path):
    from test_slice import OWNER, make
    x = make(tmp_path)
    s, repo, _ = shelf(tmp_path)
    x.conv.reading = bn.Desk(s, None, x.journal)
    await x.conv.handle(OWNER, "reading Sapiens p.12: money is trust", x.chat)
    assert f"{bn.FOLDER}/sapiens.yaml" in repo.files and "Filed a thought" in x.chat.sent[0]
    assert not x.clients            # no model was started
    await x.conv.handle(OWNER, "what is on today?", x.chat)
    await x.conv.wait()
    assert x.clients                # anything else goes to the model as before
