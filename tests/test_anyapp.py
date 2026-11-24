"""Tests for reaching any app in the connector's catalogue from the chat.

The connector is replaced by a stand-in. These tests cover the sorting of
actions into reading, changing and reaching; when the owner is asked; how an
app gets connected from the chat; and what the owner is shown.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from odysseus.agent import AgentRunner, conversation_prompt
from odysseus.anyapp import APP_CONNECT, APP_FIND, APP_NOTE, APP_RUN, AnyApp, classify, toolkit_of
from odysseus.apps import AppError
from odysseus.config import Settings
from odysseus.conversation import Conversation
from odysseus.journal import Journal, Ledger
from odysseus.memory import MemoryStore
from odysseus.tools import make_gate, make_tool_server, routine_tools

from test_slice import OWNER, FakeChat, FakeClient, FakeSpec, result

APPEND = "GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND"
SCHEMA = {"type": "object", "required": ["spreadsheetId", "values"], "properties": {"spreadsheetId": {"type": "string"}}}


class FakeConnector:
    def __init__(self, connected=False):
        self.connected, self.calls, self.links = connected, [], 0

    def find(self, task):
        return {"results": [{"primary_tool_slugs": [APPEND], "related_tool_slugs": ["GOOGLESHEETS_BATCH_GET", "GMAIL_FETCH_EMAILS", "GOOGLESHEETS_VALUES_UPDATE"]}],
                "tool_schemas": {APPEND: {"description": "Append values to a spreadsheet.", "input_schema": SCHEMA},
                                 "GOOGLESHEETS_BATCH_GET": {"description": "Read ranges.", "input_schema": SCHEMA},
                                 "GOOGLESHEETS_VALUES_UPDATE": {"description": "Overwrite a range.", "input_schema": None}},
                "toolkit_connection_statuses": [{"toolkit": "googlesheets", "has_active_connection": self.connected}]}

    def schemas(self, slugs):
        return {s: {"description": "Overwrite a range.", "input_schema": {"type": "object", "required": ["range"]}} for s in slugs}

    def lookup(self, name):
        table = {"sheets": [("googlesheets", "Google Sheets", self.connected), ("smartsheet", "Smartsheet", False)],
                 "googlesheets": [("googlesheets", "Google Sheets", self.connected)],
                 "notion": [("notion", "Notion", False), ("notion_mcp", "Notion MCP", False)], "gmail": [("gmail", "Gmail", True)]}
        return table.get(name.lower().replace(" ", "").replace("google", "google"), table.get(name.lower().split()[-1], []))

    def connect_link(self, toolkit):
        self.links += 1
        return f"https://connect.example/link/{toolkit}"

    def execute(self, slug, arguments):
        self.calls.append((slug, arguments))
        if not self.connected:
            raise AppError("No active connection found for toolkit(s) 'googlesheets' in this session.")
        return {"updatedRange": "Sheet1!A7:C7", "note": "Your verification code is 482913"}


def rig(tmp_path, **kw):
    journal = Journal(tmp_path / "journal.jsonl")
    backend = FakeConnector(**kw)
    anyapp = AnyApp(backend, journal)
    gate = make_gate(journal, anyapp=anyapp)
    h = anyapp.handlers()
    return SimpleNamespace(journal=journal, backend=backend, anyapp=anyapp, gate=gate, h=h, run=gate.guarded(APP_RUN, h["app_run"]))


def approving(x, approve=True):
    asked = []
    async def asker(approval):
        asked.append(approval.text)
        x.gate.resolve(approval.id, approve)
    x.gate.set_asker(asker)
    return asked


def call(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


# --- Sorting actions by their names ---------------------------------------------------

@pytest.mark.parametrize("slug, kind", [
    ("GOOGLESHEETS_BATCH_GET", "read"), ("GOOGLESHEETS_GET_SHEET_NAMES", "read"), ("GOOGLEDRIVE_FIND_FILE", "read"),
    ("NOTION_SEARCH_NOTION_PAGE", "read"), ("GITHUB_LIST_REPOSITORY_ISSUES", "read"),
    (APPEND, "change"), ("GOOGLESHEETS_VALUES_UPDATE", "change"), ("GOOGLESHEETS_SEARCH_AND_REPLACE", "reach"), ("GOOGLESHEETS_CLEAR_VALUES", "reach"),
    ("GMAILISH_MARK_AS_READ", "change"), ("TRACKER_SET_STATUS", "change"), ("SHEETS_READ_RANGE", "read"), ("ACME_RUN_WORKFLOW", "reach"),
    ("NOTION_CREATE_NOTION_PAGE", "change"), ("GOOGLESHEETS_BATCH_UPDATE", "change"), ("ACME_FROBNICATE_WIDGET", "change"),
    ("SLACK_SEND_MESSAGE", "reach"), ("GOOGLEDRIVE_ADD_FILE_SHARING_PREFERENCE", "reach"), ("GOOGLEDRIVE_DELETE_FILE", "reach"),
    ("GITHUB_CREATE_AN_ISSUE_COMMENT", "reach"), ("STRIPE_CREATE_REFUND", "reach"), ("NOTION_GET_AND_DELETE_BLOCK", "reach"),
])
def test_actions_are_sorted_by_the_words_in_their_names_erring_towards_asking(slug, kind):
    assert classify(slug) == kind


def test_the_app_is_the_first_word_of_the_tool_name():
    assert toolkit_of(APPEND) == "googlesheets" and toolkit_of("NOTION_CREATE_NOTION_PAGE") == "notion"


# --- Finding tools and connecting an app from the chat ----------------------------------

async def test_app_find_lists_tools_with_inputs_and_says_what_is_not_connected(tmp_path):
    x = rig(tmp_path)
    text = (await x.h["app_find"]({"task": "append a row to my budget sheet"}))["content"][0]["text"]
    assert APPEND in text and "NOT connected: call app_connect first" in text and '"spreadsheetId"' in text
    assert "GMAIL_FETCH_EMAILS" not in text, "mail keeps its own, stricter tools"
    assert '"required": ["range"]' in text, "inputs missing from the search are fetched"
    assert "(app: googlesheets" in text and "; change)" in text and "; read)" in text
    assert (await x.h["app_find"]({"task": " "}))["is_error"] is True


async def test_connecting_an_app_sends_the_owner_a_link_that_the_model_never_sees(tmp_path):
    x = rig(tmp_path)
    text = (await x.h["app_connect"]({"app": "Google Sheets"}))["content"][0]["text"]
    assert "not connected yet" in text and "https://" not in text, "the link does not pass through the model"
    assert x.anyapp.pop_links() == [("Google Sheets", "https://connect.example/link/googlesheets")] and x.anyapp.pop_links() == []


async def test_connect_handles_already_connected_ambiguous_unknown_and_mail(tmp_path):
    x = rig(tmp_path, connected=True)
    assert "already connected" in (await x.h["app_connect"]({"app": "Google Sheets"}))["content"][0]["text"] and x.backend.links == 0
    assert "More than one app matches" in (await x.h["app_connect"]({"app": "notion"}))["content"][0]["text"] or \
           "not connected yet" in (await x.h["app_connect"]({"app": "notion"}))["content"][0]["text"]
    assert (await x.h["app_connect"]({"app": "Frobnicator"}))["is_error"] is True
    assert "handled by the mail" in (await x.h["app_connect"]({"app": "gmail"}))["content"][0]["text"]


# --- The gate ------------------------------------------------------------------------------

async def test_reading_an_app_needs_no_question(tmp_path):
    x = rig(tmp_path, connected=True)
    read = call("GOOGLESHEETS_BATCH_GET", spreadsheet_id="s1", ranges=["A1:C5"])
    assert isinstance(await x.gate.permission(APP_RUN, read), PermissionResultAllow)
    text = (await x.run(read))["content"][0]["text"]
    assert text.startswith(APP_NOTE) and "482913" not in text, "what an app returns is labelled, and codes are removed"
    assert x.anyapp.pop_changes() == []


async def test_every_change_in_an_app_is_asked_about_by_itself(tmp_path):
    x = rig(tmp_path, connected=True)
    first, second = call(APPEND, spreadsheetId="s1", values=[["a"]]), call("GOOGLESHEETS_VALUES_UPDATE", range="A1", values=[["b"]])
    assert (await x.run(first))["is_error"] is True and x.backend.calls == [], "no approval, no change"
    asked = approving(x)
    out = await x.gate.permission(APP_RUN, first)
    assert asked[0].startswith("Do this in Googlesheets?") and APPEND in asked[0] and '"spreadsheetId": "s1"' in asked[0]
    await x.run(out.updated_input)
    assert (await x.run(dict(second)))["is_error"] is True, "one approval does not cover the next change in the same app"
    out = await x.gate.permission(APP_RUN, second)
    assert len(asked) == 2 and "GOOGLESHEETS_VALUES_UPDATE" in asked[1], "the second change is asked about too"
    await x.run(out.updated_input)
    assert [c[0] for c in x.backend.calls] == [APPEND, "GOOGLESHEETS_VALUES_UPDATE"]
    assert x.anyapp.pop_changes() == [f"ran {APPEND} in Googlesheets", "ran GOOGLESHEETS_VALUES_UPDATE in Googlesheets"]
    # An action whose name is on no list (the hole in the earlier design: one approval used to cover it) asks as well.
    unlisted = call("GOOGLESHEETS_VOID_SPREADSHEET", spreadsheetId="s1")
    assert x.gate.decide(APP_RUN, unlisted) == "ask"
    x.gate.set_asker(None)
    assert isinstance(await x.gate.permission(APP_RUN, unlisted), PermissionResultDeny)
    assert (await x.run(dict(first)))["is_error"] is True, "a used approval does not come back"


async def test_a_read_in_an_app_that_carries_details_from_the_mailbox_is_asked_about(tmp_path):
    x = rig(tmp_path, connected=True)
    from odysseus.taint import Taint
    x.anyapp.taint = Taint()
    search = call("SERPAPI_SEARCH", query="who owns account 4471-2290-18 at Northside")
    assert x.gate.decide(APP_RUN, search) == "allow", "nothing private is known yet"
    x.anyapp.taint.add("Your account 4471-2290-18 is overdue.")
    assert x.gate.decide(APP_RUN, search) == "ask"
    asked = approving(x)
    await x.gate.permission(APP_RUN, search)
    assert asked[0].startswith("Send these details to Serpapi?") and "4471229018" in asked[0]


async def test_sending_sharing_and_deleting_ask_every_time_even_after_changes_are_approved(tmp_path):
    x = rig(tmp_path, connected=True)
    asked = approving(x)
    await x.gate.permission(APP_RUN, call(APPEND, spreadsheetId="s1", values=[["a"]]))
    share = call("GOOGLEDRIVE_ADD_FILE_SHARING_PREFERENCE", file_id="f1", email="stranger@example.net")
    out = await x.gate.permission(APP_RUN, share)
    assert len(asked) == 2 and asked[1].startswith("Do this in Googledrive?") and "stranger@example.net" in asked[1]
    await x.run(out.updated_input)
    out2 = await x.gate.permission(APP_RUN, dict(share))
    assert len(asked) == 3, "the same share is asked about again"


async def test_malformed_and_mail_requests_are_refused_without_troubling_the_owner(tmp_path):
    x = rig(tmp_path, connected=True)
    asked = approving(x)
    for bad in ({"tool": "rm -rf", "arguments": {}}, {"tool": "GMAIL_SEND_EMAIL", "arguments": {"to": "x@y.z"}},
                {"tool": APPEND, "arguments": "not an object"}):
        assert isinstance(await x.gate.permission(APP_RUN, bad), PermissionResultDeny)
    assert asked == [] and x.backend.calls == []


async def test_a_connector_failure_is_reported_to_the_model(tmp_path):
    x = rig(tmp_path, connected=False)
    out = await x.run(call("GOOGLESHEETS_BATCH_GET", spreadsheet_id="s1"))
    assert out["is_error"] is True and "No active connection" in out["content"][0]["text"]


# --- In a conversation -----------------------------------------------------------------------

async def test_the_link_and_the_changes_are_added_to_the_reply_by_code(tmp_path):
    x = rig(tmp_path)
    settings = Settings(telegram_token="t", owner_id=OWNER, anthropic_api_key="k", exa_api_key="e", data_dir=tmp_path)

    class Client(FakeClient):
        async def query(self, prompt):
            await super().query(prompt)
            await x.h["app_connect"]({"app": "Google Sheets"})

    runner = AgentRunner(settings, x.journal, object(), client_factory=lambda options=None: Client([[result(text="I've sent you a link to connect Google Sheets.")]], options=options))
    conv = Conversation(settings, x.journal, Ledger(x.journal), runner, MemoryStore(tmp_path / "memory"), gate=x.gate, anyapp=x.anyapp)
    chat = FakeChat()
    await conv.handle(OWNER, "add a row to my budget sheet", chat)
    await conv.wait()
    assert chat.sent[-1] == ("I've sent you a link to connect Google Sheets.\n\nTo connect Google Sheets, open this link and approve, "
                             "then tell me it's done:\nhttps://connect.example/link/googlesheets")


# --- Routines, the gate's list, and the instructions -------------------------------------------

def test_routines_get_none_of_this_and_changes_never_skip_the_gate(tmp_path):
    for name in (APP_FIND, APP_CONNECT, APP_RUN):
        assert name not in routine_tools(with_apps=True)
    x = rig(tmp_path)
    always = set(x.gate.always_allowed())
    assert {APP_FIND, APP_CONNECT} <= always and APP_RUN not in always
    assert make_tool_server(FakeSpec(), x.journal, MemoryStore(tmp_path / "memory"), gate=x.gate, anyapp=x.anyapp)["name"] == "odysseus"


def test_the_instructions_tell_the_model_not_to_refuse():
    text = conversation_prompt(True, with_anyapp=True)
    assert "do not say you cannot" in text and "Call app_find" in text and "Never write a connection link yourself" in text
    assert "app_find" not in conversation_prompt(True)


# --- the connector's own labels, used with the name ---------------------------------------------------------

PIPELINE = "ATTIO_POST_V2_LISTS_LIST_ENTRIES_QUERY"


def test_a_label_can_excuse_only_the_word_post_and_only_in_a_name_that_reads():
    assert classify(PIPELINE) == "reach", "by name alone, POST asks"
    assert classify(PIPELINE, {"readOnlyHint"}) == "read", "name reads but for POST, and the connector says read-only"
    assert classify("SLACK_POST_MESSAGE", {"readOnlyHint"}) == "reach", "MESSAGE is still a hard word, whatever the label says"
    assert classify("ATTIO_POST_V2_NOTES", {"readOnlyHint"}) == "reach", "nothing in the name reads, so the label is not listened to at all"
    assert classify("GOOGLESHEETS_POST_UPDATE", {"readOnlyHint"}) == "reach", "a vendor's label cannot turn a risky name into a small change"
    assert classify("ATTIO_DELETE_NOTE", {"readOnlyHint"}) == "reach", "a wrong label does not make a deletion a read"


def test_a_label_can_make_a_reading_name_into_a_change_but_never_the_reverse():
    assert classify("ATTIO_GET_COMPANY") == "read"
    assert classify("ATTIO_GET_COMPANY", {"updateHint"}) == "change"
    assert classify("ATTIO_LIST_NOTES", {"destructiveHint"}) == "reach"
    assert classify("ATTIO_CREATE_NOTE", {"readOnlyHint"}) == "change"
    assert classify("ATTIO_GET_COMPANY", {"readOnlyHint", "createHint"}) == "change", "contradictory labels ask"


async def test_labels_are_learned_when_tools_are_found_and_a_failure_to_learn_changes_nothing(tmp_path):
    from types import SimpleNamespace
    from odysseus.journal import Journal
    found = {"results": [{"primary_tool_slugs": [PIPELINE, "ATTIO_GET_COMPANY"]}],
             "tool_schemas": {PIPELINE: {"input_schema": {"x": 1}}, "ATTIO_GET_COMPANY": {"input_schema": {"x": 1}}}}
    calls = []

    def labels(slugs):
        calls.append(list(slugs))
        return {PIPELINE: ["readOnlyHint", "lists"], "ATTIO_GET_COMPANY": ["updateHint"]}
    app = AnyApp(SimpleNamespace(find=lambda task: found, labels=labels), Journal(tmp_path / "j.jsonl"))
    run = {"tool": PIPELINE, "arguments": {"list": "dealflow"}}
    assert app.decide(run) == "ask", "not yet looked up: the name alone decides"
    out = await app.handlers()["app_find"]({"task": "list my pipeline"})
    assert f"{PIPELINE}  (app: attio; read)" in out["content"][0]["text"]
    assert app.decide(run) == "allow" and app.decide({"tool": "ATTIO_GET_COMPANY", "arguments": {}}) == "ask"
    await app.handlers()["app_find"]({"task": "list my pipeline"})
    assert calls == [[PIPELINE, "ATTIO_GET_COMPANY"]], "asked once per action"

    def broken(slugs):
        raise RuntimeError("down")
    plain = AnyApp(SimpleNamespace(find=lambda task: found, labels=broken), Journal(tmp_path / "k.jsonl"))
    await plain.handlers()["app_find"]({"task": "list my pipeline"})
    assert plain.decide(run) == "ask" and plain.decide({"tool": "ATTIO_GET_COMPANY", "arguments": {}}) == "allow"


# --- Asking as little as possible (the owner's choice, 19 November 2026) -------------

def auto_rig(tmp_path):
    journal = Journal(tmp_path / "journal.jsonl")
    backend = FakeConnector(connected=True)
    anyapp = AnyApp(backend, journal)
    gate = make_gate(journal, anyapp=anyapp, auto_small=True)
    h = anyapp.handlers()
    return SimpleNamespace(journal=journal, backend=backend, anyapp=anyapp, gate=gate, h=h, run=gate.guarded(APP_RUN, h["app_run"]))


async def test_with_less_asking_an_edit_in_the_owners_own_google_file_runs_and_is_reported(tmp_path):
    x = auto_rig(tmp_path)
    asked = approving(x)
    row = call(APPEND, spreadsheetId="s1", values=[["a"]])
    assert x.gate.decide(APP_RUN, row) == "allow"
    assert "is_error" not in await x.run(row) and asked == [] and x.backend.calls == [(APPEND, {"spreadsheetId": "s1", "values": [["a"]]})]
    told = x.anyapp.pop_changes()
    assert told[0].startswith(f"ran {APPEND} in Googlesheets without asking (") and '"spreadsheetId": "s1"' in told[0]
    assert x.gate.decide(APP_RUN, call("GOOGLEDOCS_CREATE_DOCUMENT", title="Memo")) == "allow"


def test_with_less_asking_everything_that_reaches_someone_or_cannot_be_put_back_still_asks(tmp_path):
    x = auto_rig(tmp_path)
    for slug in ("GOOGLEDRIVE_ADD_FILE_SHARING_PREFERENCE", "GOOGLEDRIVE_DELETE_FILE", "GOOGLESHEETS_CLEAR_VALUES", "GOOGLEDRIVE_MOVE_FILE",
                 "GOOGLEDOCS_REPLACE_ALL_TEXT", "GOOGLESHEETS_FROBNICATE",        # a word on no list is not an edit
                 "NOTION_CREATE_PAGE", "SLACK_UPDATE_MESSAGE", "GITHUB_CREATE_ISSUE", "PASTEBIN_CREATE_PASTE"):     # not the owner's own files
        assert x.gate.decide(APP_RUN, call(slug, id="1")) == "ask", slug
    x.anyapp._labels["GOOGLESHEETS_UPDATE_SHEET"] = frozenset({"destructiveHint"})
    assert x.gate.decide(APP_RUN, call("GOOGLESHEETS_UPDATE_SHEET", id="1")) == "ask", "an edit the connector labels destructive"


def test_without_the_owners_choice_every_change_asks_as_before(tmp_path):
    x = rig(tmp_path, connected=True)
    assert x.gate.decide(APP_RUN, call(APPEND, spreadsheetId="s1", values=[["a"]])) == "ask"
