"""The daily review (resurface.py): which three notes, the open questions, the message, and once a day."""
import asyncio
import json
from datetime import datetime

import pytest

from odysseus.booknotes import FOLDER, IST, PROBLEMS
from odysseus.resurface import Mine, Review, message, notes_of, open_questions, pick, plain, seeded, shuffled

# Computed by the Studio's own generator (src/pages/studio/index.astro, `seeded`) under Node, for this seed.
STUDIO_2026_11_24 = [0.500592421275, 0.058162128786, 0.483825928997, 0.531984862871, 0.347445898922]

BOOK = """title: Poor Charlie's Almanack
author: Charles Munger
kind: book
status: reading
notes:
  - at: 2026-10-01T09:00:00+05:30
    kind: thought
    text: Incentives explain more than intelligence does.
  - at: 2026-10-02T09:00:00+05:30
    kind: quote
    quote: Show me the incentive and I will show you the outcome.
  - at: 2026-10-03T09:00:00+05:30
    kind: question
    quote: Invert, always invert.
    text: Does inversion work for <hiring> & firing?
    private: true
  - at: 2026-11-23T09:00:00+05:30
    kind: disagree
    text: Too neat.
"""

PROBLEM = """title: Cheap evals
status: open
notes:
  - at: 2026-09-01T09:00:00+05:30
    kind: idea
    text: Grade with code first.
  - at: 2026-11-20T09:00:00+05:30
    kind: question
    text: What is the floor?
  - at: 2026-08-01T09:00:00+05:30
    kind: question
    text: Settled already.
    resolved: true
"""

NOW = datetime(2026, 11, 24, 8, 0, tzinfo=IST)


class Files:
    def __init__(self, files):
        self.files, self.reads = files, 0

    async def list(self, folder):
        return sorted(p.split("/")[-1] for p in self.files if p.startswith(folder + "/"))

    async def get(self, path):
        self.reads += 1
        return (self.files[path], "sha") if path in self.files else None


class Journal:
    def __init__(self):
        self.rows = []

    def write(self, kind, **kw):
        self.rows.append((kind, kw))


def everything():
    return notes_of("poor-charlies-almanack", BOOK, FOLDER) + notes_of("cheap-evals", PROBLEM, PROBLEMS)


def test_generator_is_the_studios():
    r = seeded("2026-11-24")
    assert [round(r(), 12) for _ in range(5)] == STUDIO_2026_11_24


def test_shuffle_is_fisher_yates_and_keeps_everything():
    items = list(range(10))
    out = shuffled(items, seeded("2026-11-24"))
    assert sorted(out) == items and out != items and items == list(range(10))
    assert shuffled(items, seeded("2026-11-24")) == out


def test_notes_are_read_with_title_kind_and_dates():
    notes = everything()
    assert len(notes) == 7
    first = notes[0]
    assert (first.where, first.slug, first.folder, first.kind) == ("Poor Charlie's Almanack", "poor-charlies-almanack", FOLDER, "thought")
    assert first.when() == datetime(2026, 10, 1, 9, 0, tzinfo=IST)
    assert notes[-1].resolved and notes[4].folder == PROBLEMS


def test_bad_yaml_gives_nothing():
    assert notes_of("x", "title: [unclosed", FOLDER) == []
    assert notes_of("x", "- just a list", FOLDER) == []


def test_pick_uses_only_own_words_older_than_a_week():
    picks = pick(everything(), NOW)
    assert len(picks) == 3
    for n in picks:
        assert n.text and n.when() < datetime(2026, 11, 17, 8, 0, tzinfo=IST)
    assert "Show me the incentive" not in " ".join(n.quote for n in picks if not n.text)   # a bare quote is never picked


def test_pick_is_the_same_all_day_and_changes_with_the_day():
    notes = [Mine(where="B", slug="b", folder=FOLDER, kind="thought", at=f"2026-01-{d:02d}T09:00:00+05:30", text=f"n{d}") for d in range(1, 29)]
    morning = [n.text for n in pick(notes, NOW)]
    assert morning == [n.text for n in pick(notes, NOW.replace(hour=23, minute=59))]
    days = {tuple(n.text for n in pick(notes, NOW.replace(day=d))) for d in range(1, 29)}
    assert len(days) > 20


def test_pick_falls_back_to_all_notes_when_few_are_old():
    notes = notes_of("b", """title: B
notes:
  - at: 2026-11-23T09:00:00+05:30
    text: new one
  - at: 2026-11-22T09:00:00+05:30
    text: new two
  - at: 2026-10-01T09:00:00+05:30
    text: old
""", FOLDER)
    assert sorted(n.text for n in pick(notes, NOW)) == ["new one", "new two", "old"]


def test_open_questions_newest_first_without_resolved():
    qs = open_questions(everything())
    assert [q.text for q in qs] == ["What is the floor?", "Does inversion work for <hiring> & firing?"]


def test_message_escapes_links_and_italicises_quotes():
    notes = everything()
    q = notes[2]
    text = message([q], open_questions(notes), "https://site.example/studio/")
    assert "<i>Invert, always invert.</i>" in text
    assert "&lt;hiring&gt; &amp; firing?" in text and "<hiring>" not in text
    assert """<a href="https://site.example/studio/#/b/poor-charlies-almanack">Poor Charlie's Almanack</a>""" in text
    assert '#/p/cheap-evals">Cheap evals</a>' in text
    assert "Question · " in text and "3 Oct 2026" in text
    assert "<b>Open questions</b>" in text


def test_message_without_studio_has_no_links():
    text = message(pick(everything(), NOW), [], "")
    assert "<a " not in text and "Open questions" not in text


def test_plain_strips_markup():
    text = message(everything()[2:3], [], "https://site.example/studio/")
    p = plain(text)
    assert "<" not in p.replace("<hiring>", "") and "(https://site.example/studio/#/b/poor-charlies-almanack)" in p
    assert "<hiring> & firing" in p


def review(tmp_path, files, now, sent=None, fail=False):
    out = sent if sent is not None else []

    async def send(text):
        if fail:
            raise ConnectionError("down")
        out.append(text)
    return Review(Files(files), send, Journal(), state_file=tmp_path / "review.json", at="08:00", studio="https://s.example/studio/",
                  now=lambda: now), out


FILES = {f"{FOLDER}/poor-charlies-almanack.yaml": BOOK, f"{PROBLEMS}/cheap-evals.yaml": PROBLEM, f"{FOLDER}/.gitkeep": ""}


def test_sends_once_a_day_after_the_hour(tmp_path):
    r, sent = review(tmp_path, FILES, NOW.replace(hour=7, minute=59))
    assert asyncio.run(r.tick()) == "early" and sent == []
    r, sent = review(tmp_path, FILES, NOW)
    assert asyncio.run(r.tick()) == "sent" and len(sent) == 1
    assert asyncio.run(r.tick()) == "done" and len(sent) == 1
    assert json.loads((tmp_path / "review.json").read_text())["sent"] == "2026-11-24"
    r, sent2 = review(tmp_path, FILES, NOW.replace(day=25))
    assert asyncio.run(r.tick()) == "sent" and len(sent2) == 1


def test_day_and_hour_are_india_time(tmp_path):
    from datetime import timezone
    utc = datetime(2026, 11, 24, 2, 0, tzinfo=timezone.utc)       # 07:30 in India
    r, sent = review(tmp_path, FILES, utc)
    assert asyncio.run(r.tick()) == "early"
    r, sent = review(tmp_path, FILES, utc.replace(hour=2, minute=30))
    assert asyncio.run(r.tick()) == "sent"


def test_nothing_to_send_sends_nothing(tmp_path):
    empty = {f"{FOLDER}/b.yaml": "title: B\nnotes:\n  - at: 2026-01-01T09:00:00+05:30\n    kind: quote\n    quote: only a quote\n"}
    r, sent = review(tmp_path, empty, NOW)
    assert asyncio.run(r.tick()) == "empty" and sent == []
    assert asyncio.run(r.tick()) == "done"


def test_a_failed_send_is_tried_again(tmp_path):
    r, _ = review(tmp_path, FILES, NOW, fail=True)
    with pytest.raises(ConnectionError):
        asyncio.run(r.tick())
    r, sent = review(tmp_path, FILES, NOW)
    assert asyncio.run(r.tick()) == "sent" and len(sent) == 1


def test_an_unreachable_repository_is_tried_again(tmp_path):
    class Down(Files):
        async def list(self, folder):
            raise ConnectionError("down")
    r = Review(Down({}), lambda t: None, Journal(), state_file=tmp_path / "review.json", now=lambda: NOW)
    with pytest.raises(ConnectionError):
        asyncio.run(r.tick())
    assert not (tmp_path / "review.json").exists()


def test_settings_default_and_off(tmp_path):
    from odysseus.config import load
    base = {"TELEGRAM_BOT_TOKEN": "x", "OWNER_TELEGRAM_ID": "1", "ANTHROPIC_API_KEY": "x", "EXA_API_KEY": "x"}
    s = load(base, env_file=tmp_path / "none")
    assert s.review_at == "08:00" and s.reading_studio == ""
    s = load({**base, "ODYSSEUS_REVIEW_AT": "Off", "ODYSSEUS_READING_STUDIO": "https://s.example/studio/"}, env_file=tmp_path / "none")
    assert s.review_at == "off" and s.reading_studio == "https://s.example/studio/"
