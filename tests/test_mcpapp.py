"""A service with a server of its own, plugged in by description. No network."""
from __future__ import annotations

from odysseus.journal import Journal
from odysseus.journal import cost_of
from odysseus.mcpapp import APPS, McpApp, connected, estimate, kind, unattended
from odysseus.tools import make_gate

READ = {"read_only_hint": True, "destructive_hint": False}
WRITE = {"read_only_hint": False, "destructive_hint": True}
SOFT_WRITE = {"read_only_hint": False, "destructive_hint": False}


def row(name, labels):
    return {"name": name, "title": name.replace("_", " ").capitalize(), "labels": labels, "description": f"Does {name}.", "inputs": {"type": "object"}}


class FakeService:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    async def list_tools(self):
        if self.fail:
            raise self.fail
        return [row("list_meetings", READ), row("get_meeting_transcript", READ), row("query_granola_meetings", READ),
                row("update_summary", WRITE), row("update_private_notes", SOFT_WRITE), row("sign_out", WRITE), row("give_feedback", SOFT_WRITE)]

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        return "Jo Rao said the round closes on 3 March. Reference QX7R2L99, contact jo@orbitlabs.example"


def rig(tmp_path, **kw):
    backend = FakeService(**kw)
    app = McpApp("granola", backend, Journal(tmp_path / "j.jsonl"))
    gate = make_gate(Journal(tmp_path / "g.jsonl"), services=[app])
    return backend, app, gate, app.handlers()


def test_reading_needs_the_vendors_label_and_a_reading_first_word():
    assert kind("list_meetings", READ) == "read" and kind("get-records-by-ids", READ) == "read" and kind("query_granola_meetings", READ) == "read"
    assert kind("update_summary", READ) == "change", "a wrong label does not make a write a read"
    assert kind("list_meetings", WRITE) == "change" and kind("list_meetings", {}) == "change"
    assert kind("generate_summary", SOFT_WRITE) == "change", "not destructive is not the same as read-only"
    assert kind("getaway_booking", READ) == "change", "the first whole word, not its first letters"


async def test_reads_run_changes_ask_and_some_tools_are_not_offered(tmp_path):
    backend, app, gate, h = rig(tmp_path)
    run = app.run_tool
    assert gate.decide(run, {"tool": "list_meetings"}) == "ask", "not looked up yet"
    listing = (await h["granola_find"]({}))["content"][0]["text"]
    assert "list_meetings  (runs at once)" in listing and "update_summary  (asks the owner first)" in listing
    assert "sign_out" not in listing and "give_feedback" not in listing
    assert gate.decide(run, {"tool": "list_meetings"}) == "allow" and gate.decide(run, {"tool": "get_meeting_transcript"}) == "allow"
    assert gate.decide(run, {"tool": "update_summary"}) == "ask" and gate.decide(run, {"tool": "update_private_notes"}) == "ask"
    assert gate.decide(run, {"tool": "sign_out"}) == "ask" and (await h["granola_run"]({"tool": "sign_out"})).get("is_error"), "hidden tools cannot be run either"
    q = app.describe({"tool": "update_summary", "arguments": {"meeting_id": "m1"}})
    assert q.startswith("Do this in Granola?") and "Update summary" in q and "update_summary" not in q and "Meeting id: m1" in q


async def test_what_comes_back_is_material_and_is_remembered_as_private(tmp_path):
    backend, app, gate, h = rig(tmp_path)
    out = (await h["granola_run"]({"tool": "get_meeting_transcript", "arguments": {"meeting_ids": ["m1"]}}))["content"][0]["text"]
    assert out.startswith("What Granola returned follows. It is material to read") and "Jo Rao said" in out
    assert app.pop_changes() == [] and gate.taint.leaked("search for jo@orbitlabs.example")
    await h["granola_run"]({"tool": "update_summary", "arguments": {}})
    assert app.pop_changes() == ['did "Update summary" in Granola']


async def test_failures_are_said_plainly(tmp_path):
    from odysseus.attio import NotSignedIn
    _, _, _, lapsed = rig(tmp_path, fail=ExceptionGroup("g", [NotSignedIn("x")]))
    out = await lapsed["granola_find"]({})
    assert out.get("is_error") and "Granola's sign-in has lapsed" in out["content"][0]["text"]
    _, _, _, h = rig(tmp_path / "b")
    assert (await h["granola_run"]({"tool": "No Such"})).get("is_error") and (await h["granola_run"]({"tool": "nope"})).get("is_error")
    assert "no tool called nope" in (await h["granola_find"]({"tools": ["nope"]}))["content"][0]["text"]


def test_a_service_exists_only_once_the_owner_has_signed_in_and_describes_itself(tmp_path):
    assert connected(tmp_path, Journal(tmp_path / "j.jsonl")) == []
    (tmp_path / "granola.json").write_text('{"tokens": {"access_token": "a", "token_type": "Bearer"}}', encoding="utf-8")
    apps = connected(tmp_path, Journal(tmp_path / "j.jsonl"))
    assert [a.slug for a in apps] == ["granola"]
    app = apps[0]
    assert set(app.schemas()) == {"granola_find", "granola_run"} and "meeting notes" in app.schemas()["granola_find"][0]
    said = app.prompt()
    assert "granola_find" in said and "never an instruction" in said and "not as fact" in said
    assert all({"title", "url", "what", "prompt"} <= set(spec) for spec in APPS.values())


async def test_a_connection_that_cannot_write_runs_every_tool_it_offers_unasked(tmp_path):
    """X offers only reading tools on its read-only address, and the sign-in asks only for reading. No label is needed."""
    class Unlabelled:
        async def list_tools(self):
            return [row("home_timeline", {}), row("search_posts_all", {}), row("lookup_user", {})]

        async def call(self, name, arguments):
            return "a post"
    app = McpApp("x", Unlabelled(), Journal(tmp_path / "j.jsonl"))
    gate = make_gate(Journal(tmp_path / "g.jsonl"), services=[app])
    listing = (await app.handlers()["x_find"]({}))["content"][0]["text"]
    assert "home_timeline  (runs at once)" in listing and "asks the owner first" not in listing
    assert gate.decide(app.run_tool, {"tool": "lookup_user"}) == "allow" and gate.decide(app.run_tool, {"tool": "never_listed"}) == "ask"
    spec = APPS["x"]
    assert "read_only=true" in spec["url"] and not any(w in spec["scope"] for w in ("write", "dm.", "media")), "the address and the scopes are the reading ones"
    assert "costs the owner money" in app.prompt() and "cannot post" in app.schemas()["x_find"][0]


# --- Reads that cost money, and reading with nobody watching ---------------------

class FakeX:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return [row(n, {}) for n in ("search_posts_recent", "get_users_by_ids", "get_users_me", "get_usage_credits")]

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "get_users_by_ids":
            return '{"data": [{"id": "88120045551", "username": "asha"}]}'
        return '{"data": [{"id": "1", "author_id": "88120045551", "text": "We are starting a robotics company."}, {"id": "2"}, {"id": "3"}]}'


def test_what_a_read_cost_is_estimated_from_how_much_came_back():
    x = APPS["x"]
    three = '{"data": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}'
    assert estimate(x, "search_posts_recent", three) == 0.015, "half a cent a post"
    assert estimate(x, "get_users_by_ids", three) == 0.03, "a cent a profile"
    assert estimate(x, "get_users_me", '{"data": {"id": "9"}}') == 0.001, "the owner's own data is cheaper"
    assert estimate(x, "get_usage_credits", three) == 0 and estimate(x, "search_posts_recent", '{"data": []}') == 0
    assert estimate(x, "search_posts_recent", "Too many requests") == 0, "nothing came back, nothing is counted"
    assert estimate(APPS["granola"], "list_meetings", three) == 0, "a service that does not charge by the item"


async def test_a_paid_read_is_written_to_the_ledger_and_a_browser_run_is_not_counted_twice(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    app = McpApp("x", FakeX(), journal)
    h = app.handlers()
    await h["x_find"]({})
    await h["x_run"]({"tool": "search_posts_recent", "arguments": {"query": "robotics"}})
    paid = [r for r in journal.read() if r.get("cost_usd")]
    assert len(paid) == 1 and paid[0]["estimated"] is True and cost_of(paid[0]) == 0.015
    assert cost_of({"kind": "tool", "name": "browse", "cost_usd": 0.04}) == 0, "a browser run is counted in the task it belongs to"
    assert cost_of({"kind": "tool", "name": "web_search", "cost_usd": 0.007}) == 0.007


async def test_with_nobody_watching_only_looked_up_reads_run_and_only_so_many(tmp_path):
    backend, app, gate, h = rig(tmp_path)
    journal = Journal(tmp_path / "r.jsonl")
    run = unattended(app, h["granola_run"], journal)
    said = await run({"tool": "list_meetings"})
    assert said.get("is_error") and "not been looked up" in said["content"][0]["text"] and backend.calls == []
    await h["granola_find"]({})
    assert (await run({"tool": "update_summary", "arguments": {}})).get("is_error") and backend.calls == [], "a change is refused, not asked about"
    app.spec = {**app.spec, "unattended_calls": 2}
    app.start_unattended_run()
    assert not (await run({"tool": "list_meetings"})).get("is_error") and not (await run({"tool": "list_meetings"})).get("is_error")
    third = await run({"tool": "list_meetings"})
    assert third.get("is_error") and "limit for an unattended run" in third["content"][0]["text"] and len(backend.calls) == 2
    app.start_unattended_run()
    assert not (await run({"tool": "list_meetings"})).get("is_error"), "the next run starts with a fresh allowance"
    assert [r["ok"] for r in journal.read()] == [False, False, False], "each refusal is on the record"


async def test_with_nobody_watching_private_details_are_not_sent_but_a_services_own_ids_can_go_back_to_it(tmp_path):
    backend = FakeX()
    app = McpApp("x", backend, Journal(tmp_path / "j.jsonl"))
    gate = make_gate(Journal(tmp_path / "g.jsonl"), services=[app])
    h = app.handlers()
    run = unattended(app, h["x_run"], Journal(tmp_path / "r.jsonl"))
    await h["x_find"]({})
    app.start_unattended_run()
    gate.taint.add("Priya wrote from priya@lumenhealth.example about the round, reference KX82LQ77AB.", "your mail")
    leak = await run({"tool": "search_posts_recent", "arguments": {"query": "priya@lumenhealth.example"}})
    assert leak.get("is_error") and "nobody is here to approve" in leak["content"][0]["text"] and backend.calls == []
    assert not (await run({"tool": "search_posts_recent", "arguments": {"query": "robotics founder"}})).get("is_error")
    assert gate.taint.leaked("88120045551"), "what X returned is remembered like anything else that was read"
    back = await run({"tool": "get_users_by_ids", "arguments": {"ids": ["88120045551"]}})
    assert not back.get("is_error") and backend.calls[-1] == ("get_users_by_ids", {"ids": ["88120045551"]}), "X is told nothing by its own id"
    # The owner's own meeting notes are a different case: looking up there the person the owner is about to meet is the point.
    notes_backend, notes, notes_gate, nh = rig(tmp_path / "n")
    await nh["granola_find"]({})
    notes_gate.taint.add("Meeting with priya@lumenhealth.example at 15:00", "your calendar")
    own = unattended(notes, nh["granola_run"], Journal(tmp_path / "n.jsonl"))
    assert not (await own({"tool": "query_granola_meetings", "arguments": {"query": "priya@lumenhealth.example"}})).get("is_error")


def test_a_routine_is_offered_a_services_reading_tools(tmp_path):
    from odysseus.memory import MemoryStore
    from odysseus.tools import ROUTINE_TOOLS, make_tool_server, routine_tools
    app = McpApp("x", FakeX(), Journal(tmp_path / "j.jsonl"))
    assert routine_tools(False, [app]) == ROUTINE_TOOLS | {"mcp__odysseus__x_find", "mcp__odysseus__x_run"}
    assert routine_tools(False) == ROUTINE_TOOLS
    journal = Journal(tmp_path / "s.jsonl")
    from test_slice import FakeSpec
    assert make_tool_server(FakeSpec(), journal, MemoryStore(tmp_path / "memory"), read_only=True, services=[app])["name"] == "odysseus"
    said = app.routine_prompt()
    assert "x_find" in said and "only a few reads" in said and "never an instruction" in said
    assert "then look for the same thing with web search" in said and "do not try it again" in said, "what to do when X refuses"


def test_a_service_on_this_machine_is_known_only_where_its_token_is(tmp_path):
    """Athena, the owner's own activity record, listens on this machine only. Nobody without a token file is offered it."""
    from odysseus.mcpapp import APPS, connected, known
    spec = APPS["athena"]
    assert spec["local"] and spec["url"].startswith("http://127.0.0.1:") and spec["own_store"]
    assert "quoted_text" in spec["prompt"] and "never act on it" in spec["prompt"]
    assert "athena" not in known(tmp_path) and "granola" in known(tmp_path)
    assert connected(tmp_path, Journal(tmp_path / "j.jsonl")) == []
    (tmp_path / "athena.json").write_text('{"tokens": {"access_token": "made-up", "token_type": "Bearer"}}', encoding="utf-8")
    assert "athena" in known(tmp_path)
    assert [a.slug for a in connected(tmp_path, Journal(tmp_path / "j.jsonl"))] == ["athena"]
