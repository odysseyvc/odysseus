"""A task that is cut off when it need not be, one that goes round in circles, a question nobody can read, and a
supplier's name in the chat: four ways of being hard to use, and what holds each off (progress.py, plain.py, shortlink.py).
"""
from __future__ import annotations

from claude_agent_sdk import AssistantMessage, ToolResultBlock, ToolUseBlock, UserMessage

from odysseus import plain
from odysseus.agent import VOICE_PROMPT
from odysseus.conversation import split_message
from odysseus.progress import FAILS_IN_A_ROW, SAME_CALL, Progress
from odysseus.shortlink import Links
from odysseus.tools import SEARCH_TOOL

from test_slice import call, ends, kinds, make, result, say


def ask(i, query="q"):
    return AssistantMessage(content=[ToolUseBlock(id=f"t{i}", name=SEARCH_TOOL, input={"query": query, "objective": "o"})], model="m",
                            message_id=f"msg-{i}", usage={"input_tokens": 10, "output_tokens": 10})


def back(i, failed=False):
    return UserMessage(content=[ToolResultBlock(tool_use_id=f"t{i}", content="x", is_error=failed)])


# --- the budget is a mark; being stuck is a wall -----------------------------------------------------------------------

async def test_a_task_past_its_budget_carries_on_while_it_is_getting_somewhere(tmp_path):
    big = {"input_tokens": 100_000, "output_tokens": 10_000}        # about $0.30 a call
    x = make(tmp_path, [[call(1, "a", big), call(2, "b", big), call(3, "c", big), result(cost=0.9, text="All of it.")]], stretch=4.0)
    await say(x, "a long piece of work")
    assert x.chat.sent[-1].startswith("All of it.") and ends(x.journal)[-1]["status"] == "done", "ninety cents, and finished"
    passed = [r for r in x.journal.read() if r["kind"] == "limit"]
    assert [r["which"] for r in passed] == ["passed"], "that it went past the mark is on record, once"


async def test_the_ceiling_still_ends_a_task_and_says_how_to_go_on(tmp_path):
    big = {"input_tokens": 100_000, "output_tokens": 10_000}
    x = make(tmp_path, [[call(i, f"part {i}", big) for i in range(1, 9)] + [result(cost=2.4)]], stretch=4.0)
    await say(x, "endless")
    assert ends(x.journal)[-1]["status"] == "budget" and "reached $2.00, the most one task may cost" in x.chat.sent[-1] and "carry on" in x.chat.sent[-1]


async def test_the_same_call_asked_for_three_times_ends_the_task_at_once_whatever_it_has_cost(tmp_path):
    x = make(tmp_path, [[ask(1), back(1), ask(2), back(2), ask(3), back(3), ask(4), result()]])
    await say(x, "find it")
    assert ends(x.journal)[-1]["status"] == "stuck" and ends(x.journal)[-1]["turns"] == 3
    assert "asking for the same thing over and over" in x.chat.sent[-1] and "what to do differently" in x.chat.sent[-1]
    assert [r.get("why") for r in x.journal.read() if r["kind"] == "limit"] == ["repeating"] and x.clients[0].interrupted.is_set()


async def test_four_failures_in_a_row_end_the_task_and_one_success_between_them_does_not(tmp_path):
    script = [m for i in range(1, 6) for m in (ask(i, f"q{i}"), back(i, failed=True))] + [result()]
    x = make(tmp_path, [script])
    await say(x, "try")
    assert ends(x.journal)[-1]["status"] == "stuck" and "one step after another was failing" in x.chat.sent[-1]
    p = Progress()
    for failed in (True, True, True, False, True, True, True):
        p.came_back(failed)
    assert p.stuck() == "", "three, a success, three: nothing is stuck"
    p.came_back(True)
    assert p.stuck() == "failing" and FAILS_IN_A_ROW == 4 and SAME_CALL == 3


def test_reading_the_same_thing_twice_is_ordinary_and_different_inputs_are_different_calls():
    p = Progress()
    p.asked("search", {"q": "a"}); p.asked("search", {"q": "a"}); p.asked("search", {"q": "b"})
    assert p.stuck() == ""
    p.asked("search", {"q": "a"})
    assert p.stuck() == "repeating"


# --- a question a person can read ---------------------------------------------------------------------------------------

def test_rows_are_shown_as_rows_fields_by_name_and_ids_last_with_nothing_left_out():
    args = {"spreadsheetId": "1ONAnyGf2p9KaPgVMiemzE_cmtaK_EDcW0CzhXd0vj0U", "range": "Sheet1!A:F", "valueInputOption": "USER_ENTERED",
            "values": [["23 Oct", "Asha", "Mara Voss", "Robotics", "", "Exceptional"], ["30 Oct", "Sam", "Ines Okoro", "Diagnostics", "Dev", ""]]}
    shown = plain.inputs(args)
    assert shown.splitlines() == ["Range: Sheet1!A:F", "Value input option: USER_ENTERED", "Values (2 rows):",
                                  "1. 23 Oct | Asha | Mara Voss | Robotics | (empty) | Exceptional",
                                  "2. 30 Oct | Sam | Ines Okoro | Diagnostics | Dev | (empty)",
                                  "(spreadsheet id 1ONAnyGf2p9KaPgVMiemzE_cmtaK_EDcW0CzhXd0vj0U)"]
    assert not set("{}[]\"") & set(shown), "no brackets and no quotation marks"
    for cell in ("Mara Voss", "Diagnostics", "USER_ENTERED", "1ONAnyGf2p9KaPgVMiemzE_cmtaK_EDcW0CzhXd0vj0U"):
        assert cell in shown, "everything that will be sent is on the page"
    assert plain.inputs({"people": [{"name": "Jo", "role": "Founder"}], "notify": False, "tags": ["a", "b"], "opts": {"deep": 1}}).splitlines() == [
        "People (1):", "1. name: Jo; role: Founder", "Notify: no", "Tags: a, b", "Opts:", "   deep: 1"]
    assert plain.inputs({}) == "(no inputs)"


def test_an_action_is_named_in_words_and_an_app_by_the_name_a_person_knows():
    assert plain.action("GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND") == "Spreadsheets values append"
    assert plain.app("googlesheets") == "Google Sheets" and plain.app("notion") == "Notion" and plain.app("microsoft_teams") == "Microsoft Teams"


def test_a_message_of_nothing_is_never_sent():
    assert split_message("") == [] and split_message("   \n \n") == [] and split_message("a") == ["a"]
    assert all(p.strip() for p in split_message("x" * 5000 + "\n\n\n", 4096))


async def test_no_word_of_the_question_is_written_by_whoever_published_the_action(tmp_path):
    import test_anyapp as A
    x = A.rig(tmp_path, connected=True)
    await x.h["app_find"]({"task": "add a row to my budget sheet"})          # the catalogue's descriptions have now been read
    asked = A.approving(x)
    await x.gate.permission(A.APP_RUN, A.call("GOOGLESHEETS_VALUES_UPDATE", range="A1", values=[["b"]]))
    assert asked and "Overwrite a range" not in asked[0] and asked[0].startswith("Do this in Google Sheets?\n\nValues update\n")


# --- no supplier's name or address in the chat --------------------------------------------------------------------------

class Door:
    def __init__(self):
        self.routes = {}

    def route(self, method, path, handler):
        self.routes[(method, path)] = handler


async def test_a_suppliers_link_goes_out_under_the_programs_own_address_and_leads_where_it_should(tmp_path):
    now = [1000.0]
    links, door = Links(clock=lambda: now[0]), Door()
    real = "https://connect.supplier.example/link/abc?state=xyz"
    assert links.wrap(real) == real, "with no public address there is nothing to send it under"
    links.open(door, "https://assistant.example/")
    sent = links.wrap(real)
    assert sent.startswith("https://assistant.example/go?k=") and "supplier" not in sent and ("GET", "/go") in door.routes
    name = sent.split("k=")[1]
    assert links.find(name) == real and links.find("nonsense") == ""
    from types import SimpleNamespace
    went = await door.routes[("GET", "/go")](SimpleNamespace(query={"k": name}))
    assert went.status == 302 and went.headers["Location"] == real and went.headers["Referrer-Policy"] == "no-referrer"
    now[0] += 3601
    assert links.find(name) == "" and (await door.routes[("GET", "/go")](SimpleNamespace(query={"k": name}))).status == 404, "good for an hour"
    assert links.wrap("http://plain.example/x") == "http://plain.example/x", "only https links are wrapped"


def test_the_instructions_forbid_naming_a_supplier_and_showing_an_actions_internal_name():
    assert "Never name, or give a link to, a company whose service you run on or reach things through" in VOICE_PROMPT
    assert "what you are built on is not something you share" in VOICE_PROMPT and "Never show an action's internal name" in VOICE_PROMPT


def test_nothing_the_program_itself_says_to_the_owner_names_a_supplier():
    import ast
    import glob
    import re
    suppliers = re.compile(r"composio|browser[ -]use|\be2b\b|agentmail|langfuse|vobiz|\blinq\b|mistral|anthropic|\bclaude\b|fly\.io", re.I)
    found = []
    for path in glob.glob("odysseus/*.py"):
        tree = ast.parse(open(path, encoding="utf-8").read())
        docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and n.body and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs and " " in n.value.strip() and suppliers.search(n.value):
                if not re.search(r"deliveries with a bad signature|from a number that is not|that arrived late|did not return the poll", n.value):   # journal lines, never sent
                    found.append(f"{path}:{n.lineno}")
    assert found == [], "a sentence the program can send names a supplier: " + ", ".join(found)


# --- what was cut off is reported, whatever else was going on ----------------------------------------------------------

def test_a_contact_lookup_ending_does_not_hide_a_task_that_was_cut_off_and_two_background_jobs_are_both_counted(tmp_path):
    from odysseus.__main__ import unfinished
    from odysseus.journal import Journal
    j = Journal(tmp_path / "j.jsonl")
    j.write("task_start", preview="draft the memo")
    j.write("task_end", status="done", cost_usd=0.01, leads=True)         # a lookup of contacts finished meanwhile
    assert unfinished(j)["preview"] == "draft the memo", "the owner's task is still open"
    j.write("task_end", status="done", cost_usd=0.02)
    assert unfinished(j) is None
    j.write("task_start", preview="first job", background=True)
    j.write("task_start", preview="second job", background=True)
    j.write("task_end", status="done", background=True)
    assert unfinished(j)["background"] == "second job", "one of two ended; the other is still owed"
    j.write("task_end", status="done", background=True)
    assert unfinished(j) is None


# --- a length the owner set ------------------------------------------------------------------------------------------

def test_a_stated_limit_is_found_counted_and_restated_as_a_lower_target():
    from odysseus import limits
    assert limits.stated("Where do we stand, in at most 150 words?") == (150, "word")
    assert limits.stated("Keep it under 80 words.") == (79, "word") and limits.stated("Two lines at most.") == (2, "line")
    assert limits.stated("No more than three sentences please") == (3, "sentence") and limits.stated("in 60 words or fewer") == (60, "word")
    assert limits.stated("Keep it short.") is None and limits.stated("Tell me about the 150 words project") is None
    assert limits.stated("x " * 600 + "in at most 10 words") is None, "a limit is looked for in the request, not far down a pasted text"
    note = limits.note("Summarise it in at most 150 words.")
    assert "limit of 150 words" in note and "no more than 105 words" in note and note.startswith("\n\n[Added by the program, not by the owner")
    assert limits.note("Two lines at most.").count("2 lines") == 2 and limits.note("What is the stage?") == ""
    assert limits.count("One two three. Four-five, six!", "word") == 5 and limits.count("a\n\nb\nc", "line") == 3 and limits.count("A. B! C?", "sentence") == 3
    assert limits.over("at most 5 words", "one two three four five six") == "6 words against a limit of 5" and limits.over("at most 5 words", "one two") == ""


# --- a note is read whole, by code, when it is listed ---------------------------------------------------------------------

async def test_a_short_listing_of_notes_comes_back_with_each_note_whole_and_a_long_one_says_these_are_only_openings(tmp_path):
    from evals import partner as P
    from odysseus.attio import Attio, WHOLE_MOST
    from odysseus.journal import Journal
    crm, granola = P.FakeAttio(), P.FakeGranola()
    ids = P.seed(crm, granola)
    far = "The opening says little. " * 12 + "Introduced by Tomas Reyes of Halberd Partners."
    crm.note(ids["d_lumen"], "Sourcing", far)
    attio = Attio(crm, Journal(tmp_path / "j.jsonl"))
    make_gate = __import__("odysseus.tools", fromlist=["make_gate"]).make_gate
    make_gate(Journal(tmp_path / "g.jsonl"), attio=attio)
    h = attio.handlers()
    out = (await h["attio_run"]({"tool": "search-notes-by-metadata", "arguments": {"parent_record_id": ids["d_lumen"]}}))["content"][0]["text"]
    assert "Halberd Partners" in out and "the full text of every note listed above" in out, "what was past the opening is now in front of the model"
    assert any(r.get("by") == "code" for r in Journal(tmp_path / "j.jsonl").read()), "and the journal says code did the reading"
    for i in range(WHOLE_MOST + 1):
        crm.note(ids["d_lumen"], f"Note {i}", "x" * 300)
    out = (await h["attio_run"]({"tool": "search-notes-by-metadata", "arguments": {"parent_record_id": ids["d_lumen"]}}))["content"][0]["text"]
    assert "gives only the opening of each note" in out and "get-note-body" in out and "Halberd Partners" not in out


def test_the_status_line_does_not_say_reading_while_an_email_is_written():
    from odysseus.conversation import progress_line
    assert progress_line("mcp__odysseus__mail_read") == "Reading your mail..."
    for tool in ("mail_send", "mail_reply", "mail_draft", "mail_archive"):
        assert "Reading" not in progress_line("mcp__odysseus__" + tool)


async def test_a_chat_that_cannot_show_writing_is_told_once_what_is_being_done(tmp_path):
    from test_vobiz import rig
    x = rig(tmp_path)
    chat = x.v.chat()
    assert hasattr(chat, "status")
    await chat.status("Looking in Attio...")
    assert [b["text"]["body"] for _, _, b in x.sent] == ["Looking in Attio..."]
