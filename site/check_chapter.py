"""Check one or more chapters against the course's unit template, without building the site.

    .venv/Scripts/python site/check_chapter.py the-gate memory

For each: every {{cite:key}} is in readings.json, every {{ch:slug}} is a chapter, the unit box and the sections "theory" and
"literature" are present, the problems are grouped, tags are balanced, and no dash is used as punctuation.
"""
import html.parser
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).parent / "content"
VOID = {"br", "img", "hr", "meta", "link", "input", "col", "wbr"}


class Balance(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1][0] != tag:
            self.errors.append(f"line {self.getpos()[0]}: </{tag}> closes {self.stack[-1] if self.stack else 'nothing'}")
            while self.stack and self.stack[-1][0] != tag:
                self.stack.pop()
        if self.stack:
            self.stack.pop()


def check(slug: str) -> list[str]:
    text = (ROOT / "chapters" / f"{slug}.html").read_text(encoding="utf-8")
    keys = {r["key"] for r in json.loads((ROOT / "readings.json").read_text(encoding="utf-8"))}
    slugs = {c["slug"] for p in json.loads((ROOT / "contents.json").read_text(encoding="utf-8"))["parts"] for c in p["chapters"]}
    out = []
    cited = [k.strip() for m in re.findall(r"\{\{cite:([^}]+)\}\}", text) for k in m.split(",")]
    out += [f"cites a work not in readings.json: {k}" for k in cited if k not in keys]
    out += [f"refers to a chapter that does not exist: {s}" for s in re.findall(r"\{\{ch:([a-z0-9-]+)\}\}", text) if s not in slugs]
    for need, what in (('class="box unit"', "the unit box"), ('<h2 id="theory">', 'the section id="theory"'), ('<h2 id="literature">', 'the section id="literature"'),
                       ("<h3>Analysis</h3>", "the Analysis problems"), ("<h3>Design</h3>", "the Design problems"), ("<h3>Reading</h3>", "the Reading problems"),
                       ("<h3>Open</h3>", "the Open problem")):
        if need not in text:
            out.append(f"missing {what}")
    if len(set(cited)) < 4:
        out.append(f"cites only {len(set(cited))} works; a unit cites at least 4")
    if re.search(r"[—–]", text):
        out.append("uses a dash as punctuation")
    ids = re.findall(r'<h2 id="([^"]+)">', text)
    out += [f"two sections share the id {i}" for i in set(ids) if ids.count(i) > 1]
    b = Balance()
    b.feed(text)
    out += b.errors[:5] + [f"never closed: <{t}> from line {n}" for t, n in b.stack[:5]]
    return out


if __name__ == "__main__":
    bad = 0
    for slug in sys.argv[1:]:
        problems = check(slug)
        bad += bool(problems)
        print(slug + ":", "ok" if not problems else "")
        for p in problems:
            print("   ", p)
    raise SystemExit(1 if bad else 0)
