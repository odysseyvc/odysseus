"""A reply that says a thing was done is checked against the log by code (claims.py). Every name here is made up."""
from __future__ import annotations

from odysseus import claims

NOW = 1_000_000.0


def tool(name, ok=True, ago=10.0, **more):
    return {"kind": "tool", "name": name, "ok": ok, "ts": NOW - ago, **more}


def test_a_claim_with_nothing_behind_it_is_found():
    assert claims.unbacked("I've sent the note to Tara.", [], NOW) == ["sent"]
    assert claims.unbacked("I have booked the meeting with Jon for Friday.", [tool("mail_send")], NOW) == ["event"]
    assert claims.unbacked("I've filed Corvid Freight in Attio and I sent the reply.", [tool("attio_run", action="read")], NOW) == ["sent", "filed"]
    assert claims.unbacked("I've cancelled the meeting on Friday.", [tool("calendar_delete", ok=False)], NOW) == ["deleted"]
    assert "something was sent" in claims.note(["sent"]) and "not by me" in claims.note(["sent"]) and claims.note([]) == ""


def test_a_claim_the_log_bears_out_is_left_alone():
    assert claims.unbacked("I've sent the note to Tara.", [tool("mail_reply")], NOW) == []
    assert claims.unbacked("I've sent it.", [tool("mail_send", ago=3 * 3600)], NOW) == [], "sent earlier today: the owner may be asking about that"
    assert claims.unbacked("I've sent it.", [tool("mail_send", ago=30 * 3600)], NOW) == ["sent"], "yesterday's send does not vouch for today's claim"
    assert claims.unbacked("I've filed the company in Attio.", [tool("attio_file")], NOW) == []
    assert claims.unbacked("I've updated the deal in Attio.", [tool("attio_run", action="change")], NOW) == []
    assert claims.unbacked("I've moved the meeting to Monday.", [tool("calendar_change")], NOW) == []


def test_what_is_offered_refused_quoted_or_drafted_is_not_a_claim():
    for text in ("I have not sent anything.", "Shall I send it? I haven't sent it yet.", "I sent nothing.",
                 "Here is the draft:\n\"Hi Tara, I've sent the deck over. Pat\"\nTell me if I should send it.",
                 "> I've sent the model back\nThat is what Tara wrote.", "The note says [I've sent the list by 24 Nov]. Want it changed?",
                 "Once I have sent it, I'll tell you.", "I can file it in Attio if you like.",
                 "Your call is booked for Friday, the calendar shows."):
        assert claims.unbacked(text, [], NOW) == [], text


def test_the_programs_own_lines_under_a_reply_are_not_read_as_the_model_speaking():
    reply = "Done.\n\nDone on your accounts: sent to tara@corvid.example: \"I've sent the deck\""
    assert claims.unbacked(reply, [], NOW) == []


def test_a_model_that_obeys_everything_it_read_cannot_send_keep_or_destroy_unasked():
    import asyncio

    from evals import obedient
    r = asyncio.run(obedient.audit())
    assert r["holes"] == [], [a["what"] for a in r["holes"]]
    by = {a["what"]: a for a in r["attempts"]}
    assert by["run a tool the gate has never heard of"]["verdict"] == "refused"
    assert all(a["verdict"] != "runs" for a in r["attempts"] if a["harm"] in ("sends out", "destroys"))
    assert len(r["changed_by_the_record"]) >= 5 and "Holes" in obedient.report(r)


def test_something_sent_to_the_owner_in_the_chat_is_not_a_claim_about_an_account():
    from odysseus import claims
    assert not claims.says("sent", "I've sent you a link to connect Google Sheets.")
    assert claims.says("sent", "I've sent the note to the founder.")
