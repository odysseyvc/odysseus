"""Compare company-data vendors on companies the firm already knows.

No published benchmark says how well any of these vendors covers Indian companies before they raise. The firm's own
records do: look up each company the firm knows in every connected vendor, and count what comes back.

    .venv/Scripts/python scripts/bakeoff.py testset        # write the test set from the firm's Attio (read only)
    .venv/Scripts/python scripts/bakeoff.py tools          # what each connected vendor offers, to check the lookups chosen
    .venv/Scripts/python scripts/bakeoff.py run [--limit N]

Everything it reads and writes stays in private/bakeoff/, which git ignores: the test set is the firm's own pipeline.
Connect a vendor first, from the chat: /connect harmonic (and specter, crunchbase, crustdata, evertrace, dealroom).

What a row of the result means, per vendor and company:
  found     the answer names the company's own website, or its exact name
  people    the answer names a founder or team member the firm has on record for that company
  funding   the answer mentions a round, an amount raised or an investor
  chars     how much came back, as a crude measure of depth
A lookup is one read of the vendor, by website if the company has one and by name otherwise. Free tiers allow about a
hundred reads a day, so a run stops at the limit given and can be continued the next day; rows already done are kept.
"""
from __future__ import annotations

import asyncio
import csv
import json
import re
import sys
import warnings
from pathlib import Path

warnings.simplefilter("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus import mcpapp  # noqa: E402
from odysseus.attio import Attio, FileTokens, McpBackend  # noqa: E402
from odysseus.attio_layout import parse  # noqa: E402
from odysseus.config import load  # noqa: E402
from odysseus.journal import Journal  # noqa: E402

OUT = Path("private/bakeoff")
VENDORS = ("harmonic", "specter", "crunchbase", "crustdata", "evertrace", "dealroom")
# Which tool looks a company up, and which input takes the website or the name. Filled per vendor once its tools have
# been seen (`tools`); until then `choose` picks by the names the tools and their inputs carry.
LOOKUPS: dict[str, tuple[str, str]] = {}
_BY_SITE = ("domain", "website", "website_url", "url", "company_domain", "domains", "website_domain")
_BY_TEXT = ("query", "q", "name", "company_name", "search", "keywords", "identifier", "company")
_MONEY = re.compile(r"\b(raised|funding|round|seed|series [a-e]|investor|valuation|\$\s?\d|usd\s?\d|₹\s?\d)", re.I)


def texts(v) -> list[str]:
    if isinstance(v, dict):
        return [t for x in v.values() for t in texts(x)]
    if isinstance(v, list):
        return [t for x in v for t in texts(x)]
    return [] if v is None or isinstance(v, bool) else [str(v)]


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"^https?://(www\.)?", "", str(s).lower())).strip()


def choose(tools: dict[str, dict]) -> tuple[str, str] | None:
    """The tool and input most likely to look one company up, from names alone. Checked by eye with `tools`."""
    best = None
    for name, row in tools.items():
        low = name.lower()
        if "compan" not in low and "organization" not in low and "entity" not in low:
            continue
        if not re.search(r"search|lookup|find|enrich|get|typeahead|resolve|snapshot", low):
            continue
        props = ((row.get("inputs") or row.get("input_schema") or {}).get("properties") or {})
        field = next((p for p in _BY_SITE if p in props), None) or next((p for p in _BY_TEXT if p in props), None)
        if field is None:
            continue
        score = (2 if field in _BY_SITE else 1) + (1 if re.search(r"enrich|lookup|typeahead|resolve|snapshot", low) else 0) - (1 if "list" in low else 0)
        if best is None or score > best[0]:
            best = (score, name, field)
    return (best[1], best[2]) if best else None


def judge(answer: str, company: dict[str, str]) -> dict[str, object]:
    low, flat = answer.lower(), norm(answer)
    site, name = company.get("domain", "").lower(), norm(company.get("name", ""))
    found = bool((site and site in low) or (name and len(name) >= 4 and re.search(rf"\b{re.escape(name)}\b", flat)))
    people = [p for p in (x.strip() for x in company.get("people", "").split(";")) if p]
    return {"found": found, "people": bool(found and any(norm(p) in flat for p in people)) if people else "",
            "funding": bool(found and _MONEY.search(answer)), "chars": len(answer) if found else 0}


async def testset() -> int:
    s = load()
    attio = Attio(McpBackend(FileTokens(Path(s.data_dir) / "attio.json")), Journal(OUT / "journal.jsonl"))
    await attio._catalogue()
    rows = (parse(await attio._call(attio._b.call("list-records", {"object": "companies", "limit": 50}))).get("records") or [])
    people = (parse(await attio._call(attio._b.call("list-records", {"object": "people", "limit": 50}))).get("records") or [])
    await attio._b.close()
    by_company: dict[str, list[str]] = {}
    for p in people:
        a = p.get("attributes") or p
        who = next((t for t in texts(a.get("name")) if " " in t), "")
        for ref in texts(a.get("company")):
            if who:
                by_company.setdefault(ref, []).append(who)
    out = []
    for r in rows:
        a = r.get("attributes") or r
        name = next(iter(texts(a.get("name"))), "")
        domain = next((t for t in texts(a.get("domains")) if "." in t and " " not in t), "")
        if name:
            out.append({"name": name, "domain": re.sub(r"^https?://(www\.)?", "", domain).strip("/"),
                        "people": "; ".join(dict.fromkeys(by_company.get(str(r.get("record_id") or ""), []))), "note": ""})
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "testset.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "domain", "people", "note"])
        w.writeheader()
        w.writerows(out)
    print(f"{len(out)} companies written to {OUT / 'testset.csv'}; {sum(1 for o in out if o['domain'])} have a website, "
          f"{sum(1 for o in out if o['people'])} have people on record. Add rows by hand, and a note such as 'stealth when met'.")
    return 0


def connected() -> list[mcpapp.McpApp]:
    s = load()
    mcpapp.load_custom(s.data_dir)
    return [a for a in mcpapp.connected(Path(s.data_dir), Journal(OUT / "journal.jsonl")) if a.slug in VENDORS]


async def tools() -> int:
    apps = connected()
    if not apps:
        print("No data vendor is connected. From the chat: /connect harmonic  (or specter, crunchbase, crustdata, evertrace, dealroom).")
        return 1
    for app in apps:
        cat = await app._catalogue()
        pick = LOOKUPS.get(app.slug) or choose(cat)
        print(f"== {app.title}: {len(cat)} tools; lookup chosen: {pick}")
        for name, row in cat.items():
            props = list(((row.get("inputs") or row.get("input_schema") or {}).get("properties") or {}).keys())
            print(f"   {name:40} {props[:8]}")
        await app._b.close()
    return 0


async def run(limit: int) -> int:
    apps = connected()
    if not apps or not (OUT / "testset.csv").exists():
        print("Connect at least one vendor and write the test set first (see the top of this file).")
        return 1
    companies = list(csv.DictReader((OUT / "testset.csv").open(encoding="utf-8")))
    done_file = OUT / "results.jsonl"
    done = [json.loads(x) for x in done_file.read_text(encoding="utf-8").splitlines() if x.strip()] if done_file.exists() else []
    seen = {(d["vendor"], d["name"]) for d in done}
    for app in apps:
        cat = await app._catalogue()
        pick = LOOKUPS.get(app.slug) or choose(cat)
        if pick is None:
            print(f"{app.title}: no lookup tool recognised; run `tools` and add it to LOOKUPS.")
            continue
        tool, field = pick
        handlers, made = app.handlers(), 0
        for c in companies:
            if (app.slug, c["name"]) in seen or made >= limit:
                continue
            value = c["domain"] if (field in _BY_SITE and c["domain"]) else c["name"]
            if field in _BY_SITE and not c["domain"]:
                continue                # this vendor's lookup is by website and the firm has none on record
            got = await handlers[f"{app.slug}_run"]({"tool": tool, "arguments": {field: [value] if field == "domains" else value}})
            answer = got["content"][0]["text"]
            row = {"vendor": app.slug, "name": c["name"], "error": bool(got.get("is_error")), **judge("" if got.get("is_error") else answer, c)}
            done.append(row)
            made += 1
            with done_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        await app._b.close()
        print(f"{app.title}: {made} lookups made with {tool}({field})")
    print(f"\n{'vendor':12} {'looked up':>9} {'found':>6} {'people':>7} {'funding':>8} {'median chars':>13} {'errors':>7}")
    for slug in VENDORS:
        mine = [d for d in done if d["vendor"] == slug]
        if not mine:
            continue
        hit = [d for d in mine if d["found"]]
        with_people = [d for d in mine if d["people"] != ""]
        sizes = sorted(d["chars"] for d in hit) or [0]
        print(f"{slug:12} {len(mine):>9} {len(hit):>6} {sum(1 for d in with_people if d['people']):>4}/{len(with_people):<2} "
              f"{sum(1 for d in hit if d['funding']):>8} {sizes[len(sizes) // 2]:>13} {sum(1 for d in mine if d['error']):>7}")
    print(f"\nRows are kept in {done_file}. 'people' counts only companies with people on record in the test set.")
    return 0


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    cap = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 90
    sys.exit(asyncio.run({"testset": testset, "tools": tools}.get(what, lambda: run(cap))()) if what in ("testset", "tools", "run") else print(__doc__) or 1)
