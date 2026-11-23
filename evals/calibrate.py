"""Is the judge right? The owner marks a sample of replies blind, and the judge's marks are compared with theirs.

    .venv/Scripts/python -m evals.calibrate --make                 write var/calibrate/label.html from finished runs
    .venv/Scripts/python -m evals.calibrate var/calibrate/labels.json   compare the owner's marks with the judge's

A model that marks prose (grade.py, fund.py) is an instrument, and an instrument nobody has checked measures nothing. Two
of its statements fail about half the time whatever the instructions say, and the score alone cannot tell whether the
replies or the statements are at fault. The maker's guidance puts this first for any model grader: compare it with a
person's marks before trusting it.

--make draws a sample of judged replies from finished runs, spread evenly over the judged cases, and writes one page. For
each reply the page shows what was asked, what the assistant could see, the reply, and each statement with three answers:
yes, no, cannot tell. It does not show what the judge said. The marks are kept in the browser as they are made and saved
to a file with one button. Every company and person in the cases is made up, so the page holds nothing private.

Given that file, this prints for each statement and overall: how often judge and owner agree, Cohen's kappa (agreement
beyond chance), and the two kinds of disagreement apart, because they are not equally bad. A judge that passes what the
owner fails hides faults; one that fails what the owner passes sends work after faults that are not there.
"""
from __future__ import annotations

import html
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import fund

OUT = Path("var/calibrate")
SAMPLE = 42                 # replies in the page: about six statements each, so some 250 marks


def sample(variants: tuple[str, ...] = ("baseline", "fewer2", "effort"), n: int = SAMPLE, seed: int = 5) -> list[dict[str, Any]]:
    """Judged replies from finished runs, the same number from each case as far as they go, in a shuffled order."""
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for v in variants:
        for r in fund.load_rows(fund.ROOT / v):
            if (r.get("judged") or {}).get("marks") and r.get("reply"):
                by[r["prompt_id"]].append({"key": f"{v}:{r['prompt_id']}:{r['rep']}", "case": r["prompt_id"], "asked": r["prompt"], "known": r.get("known") or "",
                                           "reply": r["reply"], "statements": list(r["judged"]["marks"]), "judge": list(r["judged"]["marks"].values())})
    rng = random.Random(seed)
    for rows in by.values():
        rng.shuffle(rows)
    picked: list[dict[str, Any]] = []
    while len(picked) < n and any(by.values()):
        for case in sorted(by):
            if by[case] and len(picked) < n:
                picked.append(by[case].pop())
    rng.shuffle(picked)
    return picked


def page(items: list[dict[str, Any]]) -> str:
    """One page, with everything inside it. The judge's marks are not in it."""
    shown = [{k: i[k] for k in ("key", "case", "asked", "known", "reply", "statements")} for i in items]
    data = json.dumps(shown, ensure_ascii=False).replace("</", "<\\/")
    return """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Marking the judge</title><style>
:root { --bg: #f6f4ef; --ink: #171512; --soft: #6b665d; --line: #d9d4c8; --card: #ffffff; --on: #171512; --onink: #f6f4ef }
@media (prefers-color-scheme: dark) { :root { --bg: #0a0908; --ink: #f6f4ef; --soft: #a39d91; --line: #2a2723; --card: #151310; --on: #f6f4ef; --onink: #0a0908 } }
* { box-sizing: border-box } body { margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif }
main { max-width: 820px; margin: 0 auto; padding: 20px 16px 120px } h1 { font-size: 22px; margin: 8px 0 4px } p.lead { color: var(--soft); margin: 0 0 20px }
.item { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 16px; margin: 0 0 18px }
.tag { color: var(--soft); font-size: 13px } details { margin: 8px 0 } summary { cursor: pointer; color: var(--soft); font-size: 14px }
pre { white-space: pre-wrap; word-wrap: break-word; font: 14px/1.5 ui-monospace, Consolas, monospace; margin: 8px 0; padding: 12px; background: var(--bg); border-radius: 8px; max-height: 420px; overflow: auto }
.st { border-top: 1px solid var(--line); padding: 12px 0 4px } .st p { margin: 0 0 8px }
.ans { display: flex; gap: 8px; flex-wrap: wrap } .ans button { font: inherit; padding: 6px 14px; border-radius: 999px; border: 1px solid var(--line); background: transparent; color: var(--ink); cursor: pointer }
.ans button.on { background: var(--on); color: var(--onink); border-color: var(--on) }
.bar { position: fixed; left: 0; right: 0; bottom: 0; background: var(--card); border-top: 1px solid var(--line); padding: 12px 16px; display: flex; gap: 12px; align-items: center; justify-content: center; flex-wrap: wrap }
.bar button { font: inherit; padding: 8px 18px; border-radius: 8px; border: 1px solid var(--on); background: var(--on); color: var(--onink); cursor: pointer }
</style></head><body><main><h1>Marking the judge</h1>
<p class="lead">For each reply, say whether each statement is true of it. Be strict: partly true is No. If you cannot tell from what is shown, say so. Your marks are kept in this browser as you go; when you are done, or have done enough, press Save and send me the file. Every company and person here is made up.</p>
<div id="items"></div></main><div class="bar"><span id="count"></span><button id="save">Save my marks</button></div>
<script>
const ITEMS = __DATA__;
let marks = {}; try { marks = JSON.parse(localStorage.getItem("judge-marks") || "{}") } catch (e) {}
const root = document.getElementById("items");
function esc(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML }
function count() { let n = 0, all = 0; ITEMS.forEach(i => i.statements.forEach((s, k) => { all++; if (marks[i.key + "#" + k]) n++ })); document.getElementById("count").textContent = n + " of " + all + " marked" }
ITEMS.forEach((item, n) => {
  const el = document.createElement("div"); el.className = "item";
  el.innerHTML = '<div class="tag">Reply ' + (n + 1) + ' of ' + ITEMS.length + '</div><details><summary>What was asked</summary><pre>' + esc(item.asked) + '</pre></details>'
    + (item.known ? '<details><summary>What the assistant could see</summary><pre>' + esc(item.known) + '</pre></details>' : '') + '<pre>' + esc(item.reply) + '</pre>';
  item.statements.forEach((s, k) => {
    const id = item.key + "#" + k, st = document.createElement("div"); st.className = "st";
    st.innerHTML = '<p>' + esc(s) + '</p><div class="ans"></div>';
    [["yes", "Yes"], ["no", "No"], ["unknown", "Cannot tell"]].forEach(([v, label]) => {
      const b = document.createElement("button"); b.type = "button"; b.textContent = label; if (marks[id] === v) b.className = "on";
      b.onclick = () => { marks[id] = v; try { localStorage.setItem("judge-marks", JSON.stringify(marks)) } catch (e) {} st.querySelectorAll("button").forEach(x => x.className = ""); b.className = "on"; count() };
      st.querySelector(".ans").appendChild(b) });
    el.appendChild(st) });
  root.appendChild(el) });
count();
document.getElementById("save").onclick = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(marks, null, 1)], {type: "application/json"})); a.download = "labels.json"; a.click() };
</script></body></html>""".replace("__DATA__", data)


def kappa(pairs: list[tuple[bool, bool]]) -> float:
    """Cohen's kappa for two yes/no markers: agreement beyond what their own rates of yes would give by chance."""
    n = len(pairs)
    if not n:
        return float("nan")
    agree = sum(a == b for a, b in pairs) / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    chance = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if chance == 1 else (agree - chance) / (1 - chance)


def compare(marks: dict[str, str], items: list[dict[str, Any]]) -> dict[str, Any]:
    """The owner's marks against the judge's, statement by statement. A mark of "cannot tell" is counted and left out of the rest."""
    per: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
    unknown = 0
    for i in items:
        for k, s in enumerate(i["statements"]):
            mine = marks.get(f"{i['key']}#{k}")
            if mine == "unknown":
                unknown += 1
            elif mine in ("yes", "no"):
                per[s].append((i["judge"][k], mine == "yes"))
    everything = [p for v in per.values() for p in v]

    def figures(pairs: list[tuple[bool, bool]]) -> dict[str, Any]:
        n = len(pairs)
        passed_wrongly = sum(1 for j, o in pairs if j and not o)          # the judge said yes, the owner no: a fault hidden
        failed_wrongly = sum(1 for j, o in pairs if not j and o)          # the judge said no, the owner yes: a fault that is not there
        return {"marked": n, "agree": round(sum(j == o for j, o in pairs) / n, 3) if n else None, "kappa": round(kappa(pairs), 3) if n else None,
                "judge_passed_owner_failed": passed_wrongly, "judge_failed_owner_passed": failed_wrongly,
                "owner_yes": sum(o for _, o in pairs), "judge_yes": sum(j for j, _ in pairs)}
    return {"all": figures(everything), "cannot_tell": unknown, "statements": {s: figures(v) for s, v in sorted(per.items(), key=lambda kv: kappa(kv[1]))}}


def report(c: dict[str, Any]) -> str:
    a = c["all"]
    if not a["marked"]:
        return "No marks of yes or no were found in that file."
    lines = [f"{a['marked']} statements marked yes or no; {c['cannot_tell']} marked cannot tell.",
             f"Judge and owner agree on {a['agree']:.0%}; kappa {a['kappa']:.2f}.",
             f"The judge passed what the owner failed {a['judge_passed_owner_failed']} times (faults hidden), and failed what the owner passed "
             f"{a['judge_failed_owner_passed']} times (faults that are not there).", "", "By statement, least agreement first:"]
    for s, f in c["statements"].items():
        lines.append(f"  {f['agree']:.0%} agree, kappa {f['kappa']:+.2f}, {f['marked']} marked, hid {f['judge_passed_owner_failed']}, invented {f['judge_failed_owner_passed']}:  {s[:110]}")
    return "\n".join(lines)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    OUT.mkdir(parents=True, exist_ok=True)
    if "--make" in sys.argv:
        items = sample()
        (OUT / "items.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")       # with the judge's marks, for the comparison
        (OUT / "label.html").write_text(page(items), encoding="utf-8")
        print(f"{len(items)} replies, {sum(len(i['statements']) for i in items)} statements, from {len({i['case'] for i in items})} cases. Open {OUT / 'label.html'}.")
        return 0
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    marks = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    items = json.loads((OUT / "items.json").read_text(encoding="utf-8"))
    out = compare(marks, items)
    (OUT / "comparison.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(report(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
