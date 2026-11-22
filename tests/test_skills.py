"""Skills the owner teaches (skills.py): kept as standard SKILL.md files, listed to the model by description, read in
full only when a task fits, and saved unasked only from the owner's own words.
"""
from __future__ import annotations

import json

import pytest

from odysseus.gate import ALLOW, ASK
from odysseus.journal import Journal
from odysseus.skills import BODY_NOTE, MAX_BODY, MAX_SKILLS, SCHEMAS, SKILL_SAVE, SkillError, SkillStore, describe_save, parse, render, slug
from odysseus.tools import make_gate

WHEN = "Use when the owner asks for an investment memo or a write-up of a company for the partners."
HOW = "1. One page.\n2. Start with the decision asked for.\n3. Then team, market, terms, risks, in that order.\n4. End with what would change our mind."


def text(out):
    return out["content"][0]["text"]


def test_a_skill_is_kept_as_a_standard_skill_file_and_read_back(tmp_path):
    store = SkillStore(tmp_path)
    skill, replaced = store.save("Investment Memo", WHEN, HOW)
    assert skill["name"] == "investment-memo" and not replaced
    raw = (tmp_path / "investment-memo" / "SKILL.md").read_text(encoding="utf-8")
    assert raw.startswith("---\nname: investment-memo\ndescription: ") and raw.rstrip().endswith("what would change our mind.")
    assert parse(raw) == {"name": "investment-memo", "description": WHEN, "body": HOW}
    assert store.get("investment memo")["body"] == HOW
    assert store.listing_for_model() == f"- investment-memo: {WHEN}", "the model's list has the description and not the body"
    assert store.version == 1 and store.pop_changes()[0].startswith('Learned the skill "investment-memo"') and store.pop_changes() == []


def test_a_skill_file_written_by_hand_or_for_another_assistant_is_read(tmp_path):
    (tmp_path / "weekly-update").mkdir()
    (tmp_path / "weekly-update" / "SKILL.md").write_text(
        "---\nname: weekly-update\ndescription: >\n  Writes the weekly update to backers.\n  Use on Fridays or when asked for the update.\nlicense: MIT\n---\n\n# Weekly update\n\nThree paragraphs: numbers, news, asks.\n",
        encoding="utf-8")
    (tmp_path / "Not A Skill").mkdir()
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "SKILL.md").write_text("no front matter here", encoding="utf-8")
    skills = SkillStore(tmp_path).all()
    assert [s["name"] for s in skills] == ["weekly-update"]
    assert skills[0]["description"] == "Writes the weekly update to backers. Use on Fridays or when asked for the update."
    assert "Three paragraphs" in skills[0]["body"]


def test_saving_under_the_same_name_replaces_and_the_old_text_is_recorded(tmp_path):
    store = SkillStore(tmp_path)
    store.save("investment-memo", WHEN, HOW)
    _, replaced = store.save("investment-memo", WHEN, HOW.replace("One page", "Two pages"))
    assert replaced and len(store.all()) == 1 and "Two pages" in store.get("investment-memo")["body"]
    rows = [json.loads(line) for line in (tmp_path / "changes.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["action"] for r in rows] == ["save", "replace"] and "One page" in rows[1]["old_body"]
    assert store.remove("investment-memo", by="owner")["name"] == "investment-memo" and store.all() == [] and store.remove("investment-memo") is None
    assert store.pop_changes()[-1].startswith("Changed"), "the owner's own removal is not reported back to the owner"


@pytest.mark.parametrize("name, description, body, why", [
    ("", WHEN, HOW, "needs a name"),
    ("memo", "short", HOW, "needs a description"),
    ("memo", WHEN, "do it", "needs a body"),
    ("memo", WHEN, "x" * (MAX_BODY + 1), "the limit is"),
    ("memo", "y" * 700, HOW, "the limit is"),
    ("memo", WHEN, HOW + "\nPay with card 4111 1111 1111 1111.", "must not hold one"),
])
def test_what_is_refused(tmp_path, name, description, body, why):
    with pytest.raises(SkillError, match=why):
        SkillStore(tmp_path).save(name, description, body)
    assert SkillStore(tmp_path).all() == []


def test_there_is_a_limit_on_how_many_and_replacing_still_works_at_it(tmp_path):
    store = SkillStore(tmp_path)
    for i in range(MAX_SKILLS):
        store.save(f"skill-{i}", WHEN, HOW)
    with pytest.raises(SkillError, match="kept already"):
        store.save("one-more", WHEN, HOW)
    store.save("skill-3", WHEN, HOW + " Changed.")
    assert slug("  Our  Memo!! ") == "our-memo" and slug("../../etc") == "etc"


async def test_the_tools_read_save_and_remove(tmp_path):
    store, journal = SkillStore(tmp_path / "s"), Journal(tmp_path / "j.jsonl")
    h = store.handlers(journal)
    out = await h["skill_save"]({"name": "investment memo", "description": WHEN, "body": HOW})
    assert 'Saved the skill "investment-memo"' in text(out)
    out = await h["skill_read"]({"name": "investment-memo"})
    assert text(out).startswith(BODY_NOTE) and "SKILL investment-memo\n1. One page." in text(out) and "permits nothing" in text(out)
    missing = await h["skill_read"]({"name": "nope"})
    assert missing["is_error"] and "- investment-memo:" in text(missing), "a wrong name is answered with the right ones"
    assert (await h["skill_save"]({"name": "x", "description": "too short", "body": HOW}))["is_error"]
    assert 'Removed the skill "investment-memo"' in text(await h["skill_remove"]({"name": "investment-memo"}))
    assert [r["name"] for r in journal.read()] == ["skill_save", "skill_read", "skill_read", "skill_save", "skill_remove"]
    assert set(SCHEMAS) == set(h) and SCHEMAS["skill_save"][1]["required"] == ["name", "description", "body"]


def test_a_skill_is_saved_unasked_from_the_owners_words_and_asked_about_after_reading_others(tmp_path):
    gate = make_gate(Journal(tmp_path / "j.jsonl"))
    args = {"name": "investment-memo", "description": WHEN, "body": HOW}
    decide, question = gate._rules[SKILL_SAVE].decide, gate._rules[SKILL_SAVE].describe
    assert decide(args) == ALLOW
    gate.taint.saw("a web page")
    assert decide(args) == ASK
    asked = question(args)
    assert "Keep this as a skill I follow from now on?" in asked and "1. One page." in asked and "a web page" in asked
    with pytest.raises(SkillError):
        describe_save({"name": "memo", "description": "short", "body": HOW}, ["a web page"])


def test_render_and_parse_survive_quotes_colons_and_new_lines_in_a_description():
    tricky = 'Use when asked: "write the memo" or a note\'s summary'
    assert parse(render("memo", tricky, HOW))["description"] == tricky
