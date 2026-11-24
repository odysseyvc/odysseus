"""Merge the checked reading lists into site/content/readings.json, the one bibliography the book may cite.

    .venv/Scripts/python site/readings_merge.py

The lists themselves are working files outside the repository (private/course_research). Each entry there was confirmed
against a page for the work before it was written down. This script joins them, removes duplicates, sorts them into the
groups the Readings page shows, and works out how each is named in running text.
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).parent
SOURCE = ROOT.parent / "private" / "course_research"
GROUPS = [("readings_architecture.json", "Agents, tools, memory and interfaces"), ("readings_security.json", "Protection, information flow and prompt injection"),
          ("readings_evaluation.json", "Evaluation, measurement and improvement"), ("readings_systems.json", "Systems, reliability, people and economics")]
ORGS = ("Anthropic", "OpenAI", "OWASP", "Model Context Protocol", "Stripe", "GitHub", "Meta", "Jepsen", "Google", "Microsoft", "NIST", "IETF")
# The first was confirmed only through an encyclopaedia's summary, which is not the work; the others are second entries for works already listed.
DROP = {"deutsch1994fallacies", "parasuraman1997misuse", "stripe-webhooks"}
SHORT = {"sre-embracing-risk": "SRE book, embracing risk", "sre-monitoring": "SRE book, monitoring", "sre-postmortem": "SRE book, postmortems",
         "anthropic2024effectiveagents": "Anthropic 2024a", "anthropic2024contextualretrieval": "Anthropic 2024b", "anthropic2024computeruse": "Anthropic 2024c",
         "anthropic2025contexteng": "Anthropic 2025a", "anthropic2025multiagent": "Anthropic 2025b", "anthropic2025writingtools": "Anthropic 2025c",
         "anthropic2025advancedtool": "Anthropic 2025d", "anthropic2025computeruse_docs": "Anthropic, computer use", "anthropic2026demystifying": "Anthropic 2026a",
         "anthropic2026successcriteria": "Anthropic 2026b", "anthropic2026promptcaching": "Anthropic 2026c", "mcp2025spec": "MCP specification", "mcp2025tools": "MCP specification, tools",
         "mcp_security_best_practices": "MCP security practices", "rfc6749": "RFC 6749", "rfc7636": "RFC 7636", "rfc9700": "RFC 9700", "rfc6819": "RFC 6819",
         "owasp2021ssrf": "OWASP 2021", "owasp2025llmtop10": "OWASP 2025", "stripe_webhooks": "Stripe, webhooks", "github_webhooks": "GitHub, webhooks",
         "openai2025functioncalling": "OpenAI 2025a", "openai2025practicalguide": "OpenAI 2025b", "openai2022whisperpost": "OpenAI 2022", "openai2026evalbestpractices": "OpenAI 2026",
         "jepsen": "Jepsen", "meta2025ruleoftwo": "Meta 2025", "yan2025dontmulti": "Yan 2025", "yao2023tot": "Yao et al. 2023b", "yao2023react": "Yao et al. 2023a",
         "mialon2023augmented": "Mialon et al. 2023a", "mialon2023gaia": "Mialon et al. 2023b", "beurer2025patterns": "Beurer-Kellner et al. 2025", "beurer2025toolpoisoning": "Beurer-Kellner and Fischer 2025",
         "debenedetti2024agentdojo": "Debenedetti et al. 2024", "debenedetti2025camel": "Debenedetti et al. 2025", "lampson1971protection": "Lampson 1971", "lampson1973confinement": "Lampson 1973",
         "willison2023dualllm": "Willison 2023", "willison2025trifecta": "Willison 2025"}


def surnames(authors: str) -> list[str]:
    text = re.sub(r"\([^)]*\)", "", authors)
    text = re.sub(r"\bet al\.?", "", text)
    parts = [p.strip() for p in re.split(r";|,| and ", text) if p.strip()]
    out = []
    for p in parts:
        if any(p.startswith(o) for o in ORGS):
            out.append(p)
        else:
            words = p.split()
            # particles belong to the surname: "de Rooij", "van Horn"
            tail = [words[-1]]
            for w in reversed(words[:-1]):
                if w.lower() in ("de", "van", "von", "der", "del", "di", "la", "le"):
                    tail.insert(0, w)
                else:
                    break
            out.append(" ".join(tail))
    if "et al" in authors and len(out) < 3:
        out += ["", ""]         # forces "et al." in running text
    return out


def main() -> None:
    merged: dict[str, dict] = {}
    for name, group in GROUPS:
        path = SOURCE / name
        if not path.exists():
            print("missing", name)
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = next(v for v in data.values() if isinstance(v, list))
        for e in data:
            key = e["key"].strip()
            if key in DROP:
                continue
            if not e.get("year"):
                e["year"] = "undated"
            if key in merged:
                if e.get("level") == "core":
                    merged[key]["level"] = "core"
                continue
            names = surnames(str(e["authors"]))
            entry = {"key": key, "authors": str(e["authors"]).replace(";", ","), "surnames": names, "title": e["title"].strip(), "venue": str(e["venue"]).strip().rstrip("."),
                     "year": e["year"], "url": e["url"], "shows": e["shows"].strip(), "level": e.get("level", "further"), "group": group,
                     "checked": "records" if e.get("verification") == "search_confirmed" or "crossref" in str(e.get("verified_via", "")).lower() else "page"}
            if key in SHORT:
                entry["short"] = SHORT[key]
            merged[key] = entry
    # two works that would be named alike in running text must be told apart by hand, in SHORT
    alike: dict[str, list[dict]] = {}
    for e in merged.values():
        n = e["surnames"]
        label = e.get("short") or ((n[0] if len(n) == 1 else f"{n[0]} and {n[1]}" if len(n) == 2 else f"{n[0]} et al.") + f" {e['year']}")
        alike.setdefault(label, []).append(e)
    for label, entries in alike.items():
        if len(entries) > 1:        # the usual remedy: a letter after the year, in the order of their keys
            if any(e.get("short") for e in entries):
                raise SystemExit(f"two works are both named '{label}': {[e['key'] for e in entries]}; tell them apart in SHORT")
            for letter, e in zip("abcdefgh", sorted(entries, key=lambda e: e["key"])):
                e["short"] = label + letter
    out = list(merged.values())
    (ROOT / "content" / "readings.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(len(out), "works;", sum(1 for e in out if e["level"] == "core"), "core;", sum(1 for e in out if e["checked"] == "records"), "checked against records only")


if __name__ == "__main__":
    main()
