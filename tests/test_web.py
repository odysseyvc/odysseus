"""Reading a page by its address, and research in many steps: what comes back, what is said when it fails, and when
the owner is asked before anything leaves.

Everything here is made up. No test reaches the web.
"""
from __future__ import annotations

from types import SimpleNamespace as N

from odysseus import web
from odysseus.journal import Journal
from odysseus.web import Web


class Pages:
    """Stands in for the search company. It answers for the pages it is given and can fail on some."""

    def __init__(self, pages=None, statuses=None, fail=None, run=None):
        self.pages, self.statuses, self.fail, self.calls = pages or {}, statuses or {}, fail, []
        self.agent = N(runs=N(create=self._create, poll_until_finished=self._poll))
        self.run, self.asked = run, []

    def get_contents(self, urls, **more):
        self.calls.append((list(urls), more))
        if self.fail:
            raise self.fail
        return N(results=[N(url=u, title=f"Title of {u[-1]}", text=self.pages[u]) for u in urls if u in self.pages],
                 statuses=[N(id=u, status=self.statuses.get(u, "success")) for u in urls], cost_dollars=N(total=0.002))

    def _create(self, **asked):
        self.asked.append(asked)
        if isinstance(self.run, Exception):
            raise self.run
        return N(id="run-1")

    def _poll(self, run_id, **more):
        return self.run


class Seen:
    def __init__(self):
        self.sources, self.texts = [], []

    def saw(self, what):
        self.sources.append(what)

    def add(self, text, what=""):
        self.texts.append(text)


def text_of(got):
    return got["content"][0]["text"]


def made(tmp_path, exa):
    journal, seen = Journal(tmp_path / "j.jsonl"), Seen()
    return Web(exa, journal, seen), journal, seen


A, B, C = "https://a.example/pricing", "https://b.example/team", "https://c.example/private"


async def test_pages_are_read_and_a_page_that_failed_is_named(tmp_path):
    exa = Pages({A: "Seats cost 12 a month.", B: "Founded by two people."}, {C: "error"})
    w, journal, seen = made(tmp_path, exa)
    got = await w.read({"urls": [A, B, C]})
    t = text_of(got)
    assert not got.get("is_error") and t.startswith(web.NOTE) and "2 of 3 pages read" in t
    assert "Seats cost 12 a month." in t and "Founded by two people." in t
    assert f"--- {C}\nThis page could not be read (error)" in t, "a page that failed is said to have failed"
    assert exa.calls == [([A, B, C], {"text": {"max_characters": web.READ_CHARS}})], "nothing is asked for beyond the text"
    assert seen.sources == ["a web page"] and "Seats cost 12" in seen.texts[0], "what was read is recorded as other people's text"
    rec = [r for r in journal.read() if r.get("name") == "web_read"][0]
    assert rec["ok"] and rec["pages"] == 3 and rec["read"] == 2 and rec["cost_usd"] == 0.002
    assert "Seats" not in str(rec) and "a.example" not in str(rec), "the journal holds counts, never contents or addresses"


async def test_only_web_addresses_are_fetched_and_at_most_five(tmp_path):
    assert web.addresses({"urls": ["file:///etc/passwd", "javascript:x", A, A, " " + B]}) == [A, B]
    assert len(web.addresses({"urls": [f"https://a.example/{i}" for i in range(9)]})) == web.READ_MOST
    assert web.addresses({"url": A}) == [A] and web.addresses({}) == []
    w, _, _ = made(tmp_path, Pages())
    got = await w.read({"urls": ["ftp://x.example/a"]})
    assert got["is_error"] and "needs `urls`" in text_of(got)


async def test_a_long_page_is_cut_and_said_to_be(tmp_path):
    w, _, _ = made(tmp_path, Pages({A: "x" * web.READ_CHARS}))
    assert "the page is longer" in text_of(await w.read({"urls": [A]}))


async def test_a_past_date_is_passed_on_and_its_refusal_is_said_plainly(tmp_path):
    exa = Pages({A: "Seats cost 9 a month."})
    w, _, _ = made(tmp_path, exa)
    t = text_of(await w.read({"urls": [A], "as_of": "2025-03-01"}))
    assert exa.calls[0][1]["snapshot_as_of"] == "2025-03-01T23:59:59Z" and "As it was on or before 2025-03-01." in t
    exa = Pages(fail=ValueError("Request failed with status code 403: not in this plan"))
    w, journal, seen = made(tmp_path, exa)
    got = await w.read({"urls": [A], "as_of": "2025-03-01"})
    assert got["is_error"] and "not switched on" in text_of(got) and "403" not in text_of(got)
    assert not seen.sources, "nothing was read, so nothing is recorded as read"
    got = await w.read({"urls": [A]})
    assert got["is_error"] and "could not be fetched (ValueError)" in text_of(got)
    assert [r["ok"] for r in journal.read() if r.get("name") == "web_read"] == [True, False, False]


def finished(status="completed", stop="schema_satisfied", text="| Name | Address |", cites=("https://a.example/1", "https://b.example/2")):
    return N(status=status, stop_reason=stop, cost_dollars=N(total=0.11),
             output=N(text=text, grounding=[N(citations=[N(url=u) for u in cites])]))


async def test_research_returns_the_answer_the_pages_it_rests_on_and_its_cost(tmp_path):
    exa = Pages(run=finished())
    w, journal, seen = made(tmp_path, exa)
    got = await w.research({"question": "Find three made-up companies of a kind and give the address of each."})
    t = text_of(got)
    assert not got.get("is_error") and "| Name | Address |" in t and "Pages it rests on:\n- https://a.example/1\n- https://b.example/2" in t
    assert exa.asked[0]["budget"] == {"maxCostDollars": 1.0} and exa.asked[0]["effort"] == "auto", "every run has a ceiling in dollars"
    assert seen.sources == ["research on the web"]
    rec = [r for r in journal.read() if r.get("name") == "research"][0]
    assert rec["ok"] and rec["cost_usd"] == 0.11 and rec["status"] == "completed" and "made-up" not in str(rec)


async def test_research_that_stopped_short_or_failed_says_so(tmp_path):
    w, _, _ = made(tmp_path, Pages(run=finished(stop="budget_exhausted")))
    assert "stopped at its spending ceiling" in text_of(await w.research({"question": "Find every made-up company of a kind there is."}))
    w, _, _ = made(tmp_path, Pages(run=finished(cites=())))
    assert "It named no pages for this." in text_of(await w.research({"question": "Find every made-up company of a kind there is."}))
    w, journal, seen = made(tmp_path, Pages(run=finished(status="failed")))
    got = await w.research({"question": "Find every made-up company of a kind there is."})
    assert got["is_error"] and "ended as failed" in text_of(got) and not seen.sources
    w, _, _ = made(tmp_path, Pages(run=RuntimeError("no")))
    got = await w.research({"question": "Find every made-up company of a kind there is."})
    assert got["is_error"] and "did not finish (RuntimeError)" in text_of(got)
    got = await w.research({"question": "too short"})
    assert got["is_error"] and "needs `question`" in text_of(got)


def test_the_ceiling_of_a_research_run_is_never_below_what_the_service_accepts(tmp_path):
    assert Web(Pages(), Journal(tmp_path / "j.jsonl"), research_usd=0.1)._usd == 1.0


def test_an_address_the_owner_could_have_given_leaves_unasked_and_one_carrying_their_details_is_asked_about(tmp_path):
    from odysseus.tools import make_gate
    gate = make_gate(Journal(tmp_path / "j.jsonl"), taint_file=tmp_path / "t.bin")
    assert gate.decide(web.READ_TOOL, {"urls": [A, B]}) == "allow"
    assert gate.decide(web.RESEARCH_TOOL, {"question": "Which seed funds in northern Europe led first rounds in climate hardware this year?"}) == "allow"
    gate.taint.add("The registration of the company is 4188392077 and the meeting is on Thursday.")
    assert gate.decide(web.READ_TOOL, {"urls": [A]}) == "allow", "having read mail does not stop a page being opened"
    assert gate.decide(web.READ_TOOL, {"urls": ["https://a.example/find?reg=4188392077"]}) == "ask"
    assert gate.decide(web.RESEARCH_TOOL, {"question": "Find the company with registration 4188392077 and list its directors."}) == "ask"


def test_both_tools_are_offered_with_the_rest_and_known_to_the_parts_that_list_tools():
    from odysseus import agent, resume
    from odysseus.conversation import STEPS
    assert {"web_read", "research"} <= set(web.SCHEMAS) and {"web_read", "research"} <= set(resume.READS)
    assert web.READ_TOOL in agent.HELPER_TOOLS and web.RESEARCH_TOOL not in agent.HELPER_TOOLS, "a helper may read a page and may not start a paid run"
    assert any(name == "web_read" for name, _ in STEPS) and any(name == "research" for name, _ in STEPS)
