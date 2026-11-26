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
    assert plain.action("GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND", "Appends rows to the end of a sheet. Uses the API.") == "Appends rows to the end of a sheet"
    assert plain.app("googlesheets") == "Google Sheets" and plain.app("notion") == "Notion" and plain.app("microsoft_teams") == "Microsoft Teams"


def test_a_message_of_nothing_is_never_sent():
    assert split_message("") == [] and split_message("   \n \n") == [] and split_message("a") == ["a"]
    assert all(p.strip() for p in split_message("x" * 5000 + "\n\n\n", 4096))


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
