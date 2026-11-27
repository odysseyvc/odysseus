"""More than one account for a service: two mailboxes read together, a thing found in the account it came from, and
nothing sent from an account nobody named.

The connector here is a stand-in with made-up mail. No test calls a real service.
"""
from __future__ import annotations

import pytest

from odysseus.apps import GMAIL, SCHEMAS, AppError, Apps, describe_send
from odysseus.journal import Journal


class TwoBoxes:
    """A connector with two mailboxes, called work and personal, and one calendar."""

    def __init__(self, boxes=("work", "personal")):
        self.calls, self.links = [], []
        self.mail = {"ca_work": [{"messageId": "m-work-1", "threadId": "t-work-1", "sender": "Tara <tara@fund.example>", "subject": "Term sheet", "preview": {"body": "Draft attached."}}],
                     "ca_personal": [{"messageId": "m-home-1", "threadId": "t-home-1", "sender": "Sam <sam@home.example>", "subject": "Dinner", "preview": {"body": "Friday at eight?"}}]}
        self.ids = [("ca_" + b, b) for b in boxes]

    def accounts(self, toolkit):
        if toolkit != GMAIL:
            return [{"id": "ca_cal", "alias": ""}]
        return [{"id": i, "alias": "" if name == "work" else name} for i, name in self.ids]

    def execute(self, slug, arguments, account=None):
        self.calls.append((slug, account))
        if slug == "GMAIL_GET_PROFILE":
            return {"emailAddress": "me@fund.example" if account in (None, "ca_work") else "me@home.example"}
        if slug == "GMAIL_FETCH_EMAILS":
            return {"messages": self.mail[account or "ca_work"]}
        if slug == "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID":
            for m in self.mail[account or "ca_work"]:
                if m["messageId"] == arguments["message_id"]:
                    return {**m, "messageText": m["preview"]["body"]}
            raise RuntimeError("Requested entity was not found.")
        if slug == "GMAIL_SEND_EMAIL":
            return {"id": "sent-1"}
        return {}

    def connected(self):
        return {GMAIL: True, "googlecalendar": True}

    def connect_link(self, toolkit, alias=None):
        self.links.append((toolkit, alias))
        return f"https://sign.example/{toolkit}"

    def lookup(self, name):
        return [("notion", "Notion", True)] if name.lower() == "notion" else []


def rig(tmp_path, **kw):
    b = TwoBoxes(**kw)
    apps = Apps(b, Journal(tmp_path / "j.jsonl"))
    apps.retry_wait_s = 0
    return b, apps, apps.handlers()


def said(result):
    return result["content"][0]["text"]


async def test_a_search_that_names_no_account_reads_them_all_and_says_which_is_which(tmp_path):
    b, apps, h = rig(tmp_path)
    got = said(await h["mail_search"]({"query": "newer_than:2d"}))
    assert "In the account me@fund.example:" in got and "In the account personal:" in got, "the unnamed one is known by its own address"
    assert "Term sheet" in got and "Dinner" in got
    assert sorted(a for s, a in b.calls if s == "GMAIL_FETCH_EMAILS") == ["ca_personal", "ca_work"]
    only = said(await h["mail_search"]({"query": "x", "account": "personal"}))
    assert "Dinner" in only and "Term sheet" not in only and "In the account" not in only


async def test_a_message_is_read_from_the_account_it_was_found_in(tmp_path):
    b, apps, h = rig(tmp_path)
    await h["mail_search"]({"query": "x"})
    b.calls.clear()
    assert "Friday at eight" in said(await h["mail_read"]({"message_id": "m-home-1"}))
    assert [a for s, a in b.calls if s == "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID"] == ["ca_personal"], "asked once, of the right mailbox"
    # an id never seen before is looked for in each, since reading changes nothing
    b2, apps2, h2 = rig(tmp_path)
    assert "Friday at eight" in said(await h2["mail_read"]({"message_id": "m-home-1"}))


async def test_nothing_is_sent_from_an_account_nobody_named(tmp_path):
    b, apps, h = rig(tmp_path)
    got = await h["mail_send"]({"to": ["tara@fund.example"], "subject": "Hello", "body": "Hi"})
    assert got.get("is_error") and "2 accounts" in said(got) and "do not choose" in said(got)
    assert not [1 for s, _ in b.calls if s == "GMAIL_SEND_EMAIL"]
    await h["mail_send"]({"to": ["tara@fund.example"], "subject": "Hello", "body": "Hi", "account": "personal"})
    assert [a for s, a in b.calls if s == "GMAIL_SEND_EMAIL"] == ["ca_personal"]
    wrong = await h["mail_send"]({"to": ["tara@fund.example"], "subject": "Hello", "body": "Hi", "account": "the other one"})
    assert wrong.get("is_error") and "me@fund.example, personal" in said(wrong)
    assert describe_send({"to": ["a@b.example"], "subject": "S", "body": "B", "account": "personal"}).startswith("Send this email from your personal account?")
    assert describe_send({"to": ["a@b.example"], "subject": "S", "body": "B"}).startswith("Send this email from your account?")


async def test_with_one_account_everything_is_as_it_was(tmp_path):
    b, apps, h = rig(tmp_path, boxes=("work",))
    got = said(await h["mail_search"]({"query": "x"}))
    assert "Term sheet" in got and "In the account" not in got
    await h["mail_send"]({"to": ["tara@fund.example"], "subject": "Hello", "body": "Hi"})
    assert all(a is None for _, a in b.calls), "no account is named to the connector when there is only one"

    class Old:                                  # a connector that knows nothing of accounts still works
        def execute(self, slug, arguments):
            return {"messages": []}
    old = Apps(Old(), Journal(tmp_path / "k.jsonl")).handlers()
    assert not (await old["mail_search"]({"query": "x"})).get("is_error")


async def test_adding_an_account_makes_a_link_under_the_owners_name_for_it(tmp_path):
    b, apps, h = rig(tmp_path)
    said_ = await apps.add_account("Gmail", "Personal!")
    assert b.links == [(GMAIL, "personal")] and "https://sign.example/gmail" in said_ and "'personal'" in said_
    assert "short name" in await apps.add_account("gmail", "")
    assert "do not know a service" in await apps.add_account("nosuch", "two")
    await apps.add_account("notion", "side")
    assert b.links[-1] == ("notion", "side"), "any service the connector knows, not only mail"
    listed = await apps.accounts_text()
    assert "Mail: me@fund.example, personal" in listed and "Calendar:" in listed


def test_every_mail_and_calendar_tool_can_be_told_which_account():
    for name, (_, shape) in SCHEMAS.items():
        assert "account" in shape["properties"], name
        assert "account" not in shape.get("required", [])


async def test_picking_needs_one_clear_match(tmp_path):
    accounts = [{"id": "1", "name": "work"}, {"id": "2", "name": "work-old"}, {"id": "3", "name": "personal"}]
    assert Apps._pick(accounts, "Work")["id"] == "1" and Apps._pick(accounts, "pers")["id"] == "3"
    with pytest.raises(AppError):
        Apps._pick(accounts, "wor-")
    with pytest.raises(AppError):
        Apps._pick(accounts, "")
