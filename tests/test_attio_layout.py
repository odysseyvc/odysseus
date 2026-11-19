"""Reading Attio's own text format, and describing a workspace's layout from it. No network."""
from __future__ import annotations

import asyncio

from odysseus.attio import Attio
from odysseus.attio_layout import describe, parse
from odysseus.journal import Journal

BLOCKS = """attribute_definitions[2]:
  - attribute_id: aaa
    title: Deal stage
    description: null
    api_slug: stage
    is_required: true
    is_multiselect: false
    is_writable: true
    is_archived: false
    type: status
    statuses[2]{order,pipeline_stage_id,title,id}:
      1,p1,Sourced,s1
      2,p2,"Term sheet",s2
  - attribute_id: bbb
    title: Record ID
    api_slug: record_id
    is_writable: false
    is_archived: false
    type: text
has_more: false"""

TABLE = """attribute_definitions[2]{attribute_id,title,description,api_slug,is_required,is_multiselect,is_writable,is_archived,is_unique,type}:
  x1,Ownership,"Percent.",ownership,false,false,true,false,false,number
  x2,Entry ID,null,entry_id,false,false,false,false,true,text
has_more: false"""


def test_both_of_attios_formats_are_read_into_the_same_shape():
    a, b = parse(BLOCKS), parse(TABLE)
    assert a["has_more"] is False and [d["api_slug"] for d in a["attribute_definitions"]] == ["stage", "record_id"]
    assert a["attribute_definitions"][0]["statuses"] == [{"order": "1", "pipeline_stage_id": "p1", "title": "Sourced", "id": "s1"},
                                                         {"order": "2", "pipeline_stage_id": "p2", "title": "Term sheet", "id": "s2"}]
    assert a["attribute_definitions"][0]["description"] is None and a["attribute_definitions"][0]["is_required"] is True
    assert b["attribute_definitions"][0] == {"attribute_id": "x1", "title": "Ownership", "description": "Percent.", "api_slug": "ownership",
                                             "is_required": False, "is_multiselect": False, "is_writable": True, "is_archived": False,
                                             "is_unique": False, "type": "number"}


def test_short_lists_nested_blocks_and_nonsense_are_handled():
    got = parse('lists[1]:\n  - list_id: L\n    name: Portfolio\n    api_slug: portfolio\n    parent_objects[1]: companies\nhas_more: false')
    assert got["lists"] == [{"list_id": "L", "name": "Portfolio", "api_slug": "portfolio", "parent_objects": ["companies"]}]
    assert parse("workspace_name: Acme Capital\nteams[0]:\nowner:\n  id: 7\n  kind: member") == {"workspace_name": "Acme Capital", "teams": [], "owner": {"id": "7", "kind": "member"}}
    assert parse("") == {} and parse("not a key\n  ??\nok: 1") == {"ok": "1"}


ANSWERS = {
    "whoami": "workspace_member_id: M1\nname: Pat Owner\nworkspace_name: Acme Capital",
    "list-objects": "objects[1]{object_id,api_slug,singular_noun,plural_noun}:\n  o1,deals,Deal,Deals\nhas_more: false",
    "list-attribute-definitions": BLOCKS,
    "list-lists": "lists[1]:\n  - list_id: L\n    name: Portfolio\n    api_slug: portfolio\n    parent_objects[1]: companies\nhas_more: false",
    "list-list-attribute-definitions": TABLE,
}


class FakeBackend:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def call(self, name, arguments):
        self.calls.append(name)
        if self.fail:
            raise ConnectionError("down")
        return ANSWERS.get(name, "ok: true")

    async def list_tools(self):
        hint = {"read_only_hint": True, "destructive_hint": False}
        return [{"name": n, "title": n, "labels": hint if n.startswith(("list-", "whoami")) else {"read_only_hint": False, "destructive_hint": True},
                 "description": n, "inputs": {}} for n in ("whoami", "list-objects", "create-list")]


async def test_the_description_names_every_settable_field_with_its_choices_and_says_which_are_not_settable():
    text = await describe(FakeBackend().call)
    assert 'The workspace is "Acme Capital". The owner is signed in as Pat Owner, workspace member M1' in text
    assert "Object `deals` (Deals). Fields you can set:\n  stage (status, required): Deal stage Choices, in order: Sourced | Term sheet" in text
    assert "Filled in by Attio, not by you: record_id" in text
    assert 'List `portfolio` ("Portfolio"), a list of companies.' in text and "  ownership (number): Ownership. Percent." in text


async def test_the_model_is_given_the_layout_with_the_tools_and_it_is_read_once(tmp_path):
    backend = FakeBackend()
    attio = Attio(backend, Journal(tmp_path / "j.jsonl"))
    h = attio.handlers()
    first = (await h["attio_find"]({}))["content"][0]["text"]
    assert "How this workspace is laid out" in first and "stage (status, required)" in first
    n = len(backend.calls)
    assert "stage (status, required)" in (await h["attio_find"]({}))["content"][0]["text"] and len(backend.calls) == n, "not read again while fresh"
    await h["attio_run"]({"tool": "create-list", "arguments": {}})
    await h["attio_find"]({})
    assert backend.calls.count("list-objects") == 2, "read again after the workspace's shape was changed"


async def test_an_old_layout_is_still_given_while_a_new_one_is_read_and_a_failed_read_does_not_break_the_tools(tmp_path):
    backend = FakeBackend()
    attio = Attio(backend, Journal(tmp_path / "j.jsonl"))
    now = [0.0]
    attio.clock = lambda: now[0]
    h = attio.handlers()
    await h["attio_find"]({})
    now[0] = 31 * 60
    assert "stage (status, required)" in (await h["attio_find"]({}))["content"][0]["text"], "the old one is used at once"
    await asyncio.sleep(0.05)
    assert backend.calls.count("list-objects") == 2, "and a fresh one was read behind it"
    broken = Attio(FakeBackend(fail=True), Journal(tmp_path / "k.jsonl"))
    out = await broken.handlers()["attio_find"]({})
    assert "is_error" not in out and "How this workspace is laid out" not in out["content"][0]["text"] and "whoami" in out["content"][0]["text"]


def test_a_list_with_no_name_is_read_too():
    got = parse("[1]:\n  - record_id: r1\n    attributes:\n      name: Orbit Labs\n      stage: Term sheet\n      tags[2]: a,b")
    assert got == {"": [{"record_id": "r1", "attributes": {"name": "Orbit Labs", "stage": "Term sheet", "tags": ["a", "b"]}}]}


async def test_a_workspace_whose_database_works_out_who_knows_whom_is_told_to_read_that_and_others_are_told_nothing():
    from evals.crm import COLLEAGUE, FakeAttio
    from odysseus.attio_layout import KNOWS, read, render
    crm = FakeAttio()
    rid = crm.add("people", name="Priya Nair")
    crm.fill(rid, strongest_connection_strength="Strong", strongest_connection_user={"workspace_membership_id": COLLEAGUE["workspace_membership_id"]})
    shape = await read(crm.call)
    text = render(shape)
    assert "Who knows whom." in text and "On companies and people:" in text and "Do not count emails yourself" in text
    assert text.count("Who knows whom.") == 1 and render(shape, limit=900).endswith("for them."), "the note survives a cut layout"
    row = await crm.call("list-records", {"object": "people"})
    assert "strongest_connection_strength: Strong" in row and COLLEAGUE["workspace_membership_id"] in row
    assert "Sam Rivera" in await crm.call("list-workspace-members", {}), "the id is turned into a name with a read"
    for obj in shape["objects"].values():
        obj["attrs"] = [a for a in obj["attrs"] if a["api_slug"] not in KNOWS]
    assert "Who knows whom" not in render(shape), "a workspace without the fields is not told of them"
