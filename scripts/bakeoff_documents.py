"""One document brief, made on the model maker's hosted agent service, to compare with the route this program uses.

    python scripts/bakeoff_documents.py DIR        runs the brief once and writes the files it made into DIR
    python scripts/bakeoff_documents.py --brief    prints the brief, to give to the assistant in the chat for the other side

The hosted service runs the agent loop and a container with no way out to the internet, and has a grader that sends the
work back against a rubric until it passes or the tries run out. This program's own route is the conversation's model
writing code that runs on a rented machine, with a trial look at the pages before sending (sandbox.py). The brief is the
same for both and is made up: no real company, person or figure. The run is capped at two dollars by the service.
It costs real money; the session is deleted afterwards unless --keep-session is given.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BRIEF = """Make a two-page investment memo as a PDF, on A4, about this made-up company. Use only what is given here.

Company: Corvid Freight. Software that plans loads for small road-haulage fleets of 5 to 50 trucks.
Founders: Tara Menon (ran dispatch at a 300-truck haulier for six years) and Jon Aske (built routing software at a parcel carrier).
Round: raising 3 million at 12 million pre-money. 1.8 million is committed by a lead; 1.2 million is open.
Figures to show in cards across the top: 212k revenue last month; 41 paying fleets; 118% net revenue retention; 140k monthly burn; 14 months of runway.
Monthly revenue for the last six months, in thousands, oldest first: 96, 118, 131, 160, 187, 212. Show these as a chart.
Use of funds: 55% engineering, 30% sales, 15% operations. Show this as a table with the amounts worked out from the open 3 million.
Risks, as the notes give them: two customers are 31% of revenue; fleets under ten trucks churn at twice the rate of larger ones; a larger rival gives planning away with its telematics hardware.
Not given: gross margin, the cap table, customer acquisition cost.
Leave a boxed section headed "Recommendation" empty for the reader to fill in by hand.
Put a page number in the footer of each page."""

RUBRIC = """# Memo rubric
- Exactly one PDF named memo.pdf is in /mnt/session/outputs/
- The PDF is A4 and has exactly two pages
- The five figures (212k, 41, 118%, 140k, 14 months) each appear in a card or tile near the top of page one, with a label
- A chart shows the six monthly revenue figures 96, 118, 131, 160, 187, 212 in that order
- A use-of-funds table shows 55%, 30% and 15% with the amounts 1.65 million, 0.9 million and 0.45 million
- The round is stated: 3 million at 12 million pre-money, 1.8 million committed, 1.2 million open
- All three risks from the brief appear, and no risk that is not in the brief
- A list headed "Not given" names gross margin, the cap table and customer acquisition cost
- A boxed section headed "Recommendation" is present and empty
- Each page has its page number in the footer
- No text overlaps other text, runs off the page or is cut off, and no table is split from its heading
- Body text is between 9.5 and 11.5 points; there is one accent colour besides black and grey
- Nothing is stated about the company that the brief does not give
"""

SYSTEM = ("You make documents on a machine with no internet access. Write the document as one HTML file with its CSS inside and print it to "
          "a PDF with the browser installed on the machine; draw any chart inline. Before you finish, turn the PDF's pages into pictures and "
          "look at them, and correct what looks wrong. Put the finished file in /mnt/session/outputs/. Write nothing the brief does not give.")
MODEL = "claude-sonnet-5-5"
CAP_CENTS = "200"


def main() -> int:
    if "--brief" in sys.argv:
        print(BRIEF)
        return 0
    if len(sys.argv) < 2 or sys.argv[1].startswith("--"):
        print(__doc__)
        return 2
    import anthropic
    from odysseus.config import load
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic(api_key=load().anthropic_api_key)

    environment = client.beta.environments.create(name=f"bakeoff-documents-{int(time.time())}",
                                                  config={"type": "cloud", "networking": {"type": "limited"}})      # no host allowed: nothing can leave
    agent = client.beta.agents.create(name="Document maker (bake-off)", model=MODEL, system=SYSTEM, tools=[{
        "type": "agent_toolset_20260401",
        # Unattended, in a container with no way out and nothing of anyone's in it: its own tools run without asking.
        "default_config": {"enabled": True, "permission_policy": {"type": "always_allow"}},
        "configs": [{"name": "web_fetch", "enabled": False}, {"name": "web_search", "enabled": False}]}])
    session = client.beta.sessions.create(agent={"type": "agent", "id": agent.id, "version": agent.version}, environment_id=environment.id,
                                          title="Bake-off: a two-page memo",
                                          budget={"type": "limit", "max_list_cost": {"amount": CAP_CENTS, "currency": "USD"}})
    began, tools, grades, ended = time.monotonic(), 0, [], "stream closed"
    try:
        with client.beta.sessions.events.stream(session_id=session.id) as stream:
            client.beta.sessions.events.send(session_id=session.id, events=[{
                "type": "user.define_outcome", "description": BRIEF, "rubric": {"type": "text", "content": RUBRIC}, "max_iterations": 3}])
            for event in stream:
                kind = event.type
                if kind in ("agent.tool_use", "agent.mcp_tool_use"):
                    tools += 1
                    if getattr(event, "evaluated_permission", "") == "ask":       # nobody is watching: a paused call is refused
                        client.beta.sessions.events.send(session_id=session.id, events=[{"type": "user.tool_confirmation", "tool_use_id": event.id, "result": "deny"}])
                elif kind == "span.outcome_evaluation_end":
                    grades.append((event.iteration, event.result, " ".join(str(event.explanation or "").split())[:700]))
                    print(f"  graded try {event.iteration + 1} at {time.monotonic() - began:.0f}s: {event.result}", flush=True)
                elif kind == "session.status_idle":
                    if event.stop_reason.type != "requires_action":
                        ended = event.stop_reason.type
                        break
                elif kind == "session.status_terminated":
                    ended = "terminated"
                    break
        seconds = time.monotonic() - began
        time.sleep(3)                                                             # the files made take a moment to be listed
        made = []
        for f in client.beta.files.list(scope_id=session.id, betas=["managed-agents-2026-04-01"]).data:
            client.beta.files.download(f.id).write_to_file(str(out / Path(f.filename).name))
            made.append((Path(f.filename).name, f.size_bytes))
        final = client.beta.sessions.retrieve(session.id)
        print(f"ended: {ended}; {seconds:.0f}s by the clock; tool calls {tools}; files made {made}")
        print(f"usage as the service reports it: {final.usage}")
        for i, result, why in grades:
            print(f"try {i + 1}: {result}. {why}")
        (out / "grades.txt").write_text("\n".join(f"try {i + 1}: {r}. {w}" for i, r, w in grades), encoding="utf-8")
    finally:
        if "--keep-session" not in sys.argv:
            for what, undo in (("session", lambda: client.beta.sessions.delete(session_id=session.id)),):
                try:
                    undo()
                except Exception as e:
                    print(f"the {what} could not be deleted ({type(e).__name__}); delete it in the console")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
