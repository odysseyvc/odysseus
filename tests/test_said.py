"""Things the program does itself, asked for in plain words: one tool, the same code the commands ran, and the gate.

Everything here is made up. No model is called.
"""
from __future__ import annotations

from odysseus import said
from odysseus.agent import conversation_prompt
from odysseus.gate import ALLOW, ASK
from odysseus.journal import Journal
from odysseus.tools import MANAGE_TOOL, make_gate


def rig(tmp_path, host=True, fail=False):
    ran, journal = [], Journal(tmp_path / "j.jsonl")

    async def run(text):
        if fail:
            raise RuntimeError("down")
        ran.append(text)
        return ["Done: " + text.split()[0]]
    return ran, journal, said.handler(run, journal, host)


async def test_a_thing_asked_for_in_words_runs_the_same_command_and_the_model_is_not_shown_the_result(tmp_path):
    ran, journal, manage = rig(tmp_path)
    got = await manage({"what": "invite", "detail": "whatsapp  Priya\nfrom the fund"})
    assert ran == ["/invite whatsapp Priya from the fund"], "one line, as if typed"
    said_to_model = got["content"][0]["text"]
    assert "told the owner" in said_to_model and "wa.me" not in said_to_model and not got.get("is_error")
    await manage({"what": "people"})
    await manage({"what": "browser_sign_in", "detail": "done"})
    assert ran[1:] == ["/people", "/browser done"]
    assert [r["what"] for r in journal.read() if r.get("name") == "manage"] == ["invite", "people", "browser_sign_in"]


async def test_only_what_is_on_the_list_and_only_for_who_may(tmp_path):
    ran, journal, manage = rig(tmp_path, host=False)
    for what in ("invite", "people", "remove_person", "usage", "stop", "new", "/invite", ""):
        got = await manage({"what": what, "detail": "x"})
        assert got.get("is_error") and ran == [], what
    await manage({"what": "memory"})
    assert ran == ["/memory"]
    assert "invite" not in said.schema(False)[1]["properties"]["what"]["enum"] and "invite" in said.schema(True)[1]["properties"]["what"]["enum"]
    assert "invitation" not in said.schema(False)[0] and said.command("nothing") == ""


async def test_a_failure_is_said_and_not_hidden(tmp_path):
    ran, journal, manage = rig(tmp_path, fail=True)
    got = await manage({"what": "people"})
    assert got.get("is_error") and "could not" in got["content"][0]["text"]


def test_showing_is_unasked_changing_is_unasked_only_from_the_owners_own_words_and_some_things_always_ask(tmp_path):
    gate = make_gate(Journal(tmp_path / "j.jsonl"))
    assert gate.decide(MANAGE_TOOL, {"what": "people"}) == ALLOW
    assert gate.decide(MANAGE_TOOL, {"what": "invite", "detail": "Priya"}) == ALLOW
    assert gate.decide(MANAGE_TOOL, {"what": "remove_person", "detail": "123"}) == ASK
    assert gate.decide(MANAGE_TOOL, {"what": "disconnect", "detail": "attio"}) == ASK
    assert gate.decide(MANAGE_TOOL, {"what": "something else"}) == ASK
    assert gate.decide(MANAGE_TOOL, {"what": "sharing_with_colleagues", "detail": "off"}) == ALLOW, "a change, from the owner's own words"
    gate.taint.saw("an email from someone else")          # the task has now read words the owner did not write
    assert gate.decide(MANAGE_TOOL, {"what": "sharing_with_colleagues", "detail": "off"}) == ASK
    assert gate.decide(MANAGE_TOOL, {"what": "report_wrong_answer", "detail": "x"}) == ASK
    assert gate.decide(MANAGE_TOOL, {"what": "people"}) == ALLOW, "showing the owner their own list is still no question"
    assert said.describe({"what": "invite", "detail": "whatsapp Priya"}) == "Make an invitation for one new person?\n\nwhatsapp Priya"


def test_the_model_is_told_to_act_on_words_and_never_to_name_a_command():
    told = conversation_prompt(True)
    assert "call manage" in told and "Never tell the owner to type a command" in told and "Never say such a thing is not built" in told
    for old in ("/invite", "/people", "/help", "/connect", "/report", "/remove"):
        assert old not in told, old
    about = said.schema(True)[0]
    assert "/" not in about.replace("and/or", ""), "the tool's own description names no command either"


async def test_new_conversation_is_said_in_words(tmp_path):
    from odysseus.conversation import NEW_WORDS
    assert "new conversation" in NEW_WORDS and "/new" in NEW_WORDS


async def test_a_connection_asked_for_in_words_begins_when_the_task_has_ended(tmp_path):
    ran, journal, _ = rig(tmp_path)
    later = []

    async def run(text):
        ran.append(text)
    manage = said.handler(run, journal, True, later=later.append)
    got = await manage({"what": "connect", "detail": "attio"})
    assert ran == [] and later == ["/connect attio"] and "on its way" in got["content"][0]["text"]
    await manage({"what": "add_account", "detail": "gmail personal"})
    assert ran == ["/account gmail personal"], "a link for one more account needs no rebuilding, so it is made at once"


def test_a_thing_whose_whole_effect_is_a_link_to_the_owner_is_never_asked_about(tmp_path):
    """Asked in real use, the invitation's question went unanswered for ten minutes, lapsed, and was asked again."""
    gate = make_gate(Journal(tmp_path / "j.jsonl"))
    gate.taint.saw("an email from someone else")
    for what in ("invite", "connect", "add_account", "browser_sign_in"):
        assert gate.decide(MANAGE_TOOL, {"what": what, "detail": "x"}) == ALLOW, what
    assert gate.decide(MANAGE_TOOL, {"what": "sharing_with_colleagues", "detail": "off"}) == ASK
    assert gate.decide(MANAGE_TOOL, {"what": "remove_person", "detail": "1"}) == ASK


async def test_the_model_is_shown_what_the_program_said_with_every_link_taken_out(tmp_path):
    """The program once refused ("no WhatsApp number here") and the model, told only "done", said the link was in the chat."""
    journal = Journal(tmp_path / "j.jsonl")

    async def refused(text):
        return ["There is no WhatsApp number on this copy to invite anyone to. I can make a Telegram invitation instead."]

    async def made(text):
        return ["Send this link to one person. It works once.\n\nhttps://wa.me/918040001234?text=join%20abcDEF123_secret-code"]

    async def silent(text):
        return []
    got = (await said.handler(refused, journal, True)({"what": "invite"}))["content"][0]["text"]
    assert "no WhatsApp number" in got and "do not claim otherwise" in got
    got = (await said.handler(made, journal, True)({"what": "invite"}))["content"][0]["text"]
    assert "wa.me" not in got and "abcDEF123" not in got and "secret-code" not in got and "a link, which the owner now has" in got
    got = (await said.handler(silent, journal, True)({"what": "invite"}))["content"][0]["text"]
    assert "told the owner nothing" in got
    assert len(said.told(["x" * 5000])) < 1600
