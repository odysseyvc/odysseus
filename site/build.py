"""Build the Odysseus site into site/dist: product page, deploy guide and book.

The build log is private. Its entries live in private/log, which git ignores, and are never
part of the public site. `python site/build.py --log` also writes private/log.html for the owner to read.


No dependencies. Run:  py site/build.py

Content lives in site/content:
  contents.json        site facts (repository address, test count) and the book's table of contents
  home.html            the product page
  deploy.html          the deploy guide
  cover.html           the opening of the book's contents page
  chapters/<slug>.html one chapter body each, as an HTML fragment
  log/*.html           one build-log entry each; first line is a comment with
                       "key: value" pairs (n, date, title, summary)

The build numbers sections, figures and tables, so chapter files never carry
numbers by hand. A chapter refers to another by its name, written {{ch:the-gate}},
and the build turns that into a link reading "Chapter 8". The book can then be
reordered without a single chapter being edited.
"""
import html
import json
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).parent
CONTENT = ROOT / "content"
DIST = ROOT / "dist"
PRIVATE_LOG = ROOT.parent / "private" / "log"     # the owner's notebook; ignored by git, never published

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    "family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&"
    'family=Instrument+Serif:ital@0;1&display=swap">'
)

# The mark: a sail on a hull.
MARK = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" '
        'stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12.500"/><path d="M12 4.500l6.500 9.500H12z" '
        'fill="currentColor" fill-opacity="0.18"/><path d="M12 7l-5 7h5"/><path d="M3.500 17.500h17l-2.200 3.500H5.700z"/></svg>')
ICON = ("data:image/svg+xml," + "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%231f3a5f' "
        "stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M12 3v12.500M12 4.500l6.500 9.500H12zM12 7l-5 7h5"
        "M3.500 17.500h17l-2.200 3.500H5.700z'/%3E%3C/svg%3E")


def page(title, description, body, current=""):
    def nav(href, label, cls=""):
        cur = ' aria-current="page"' if href == current else ""
        return f'<a href="{href}"{cur}{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}>{label}</a>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description, quote=True)}">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="{ICON}">
{FONTS}
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<header class="masthead"><div class="masthead-inner">
<a class="brand" href="index.html">{MARK}Odysseus</a>
<nav aria-label="Site">{nav("index.html", "Product", "hide-sm")}{nav("book.html", "The book")}{nav("deploy.html", "Run your own", "go")}</nav>
</div></header>
{body}
<footer><div>
<p>Odysseus is built in the open. Every factual claim on this site carries an evidence grade, and anything not yet run is marked as such.</p>
<nav aria-label="Footer"><a href="book.html">The book</a><a href="what-is-built.html">What is built</a><a href="deploy.html">Run your own</a><a href="evidence.html">How to read the evidence</a></nav>
</div></footer>
</body>
</html>
"""


def number_chapter(n, body):
    """Number h2 sections, figures and tables; return (body, sections)."""
    sections = []
    count = {"s": 0, "f": 0, "t": 0}

    def h2(m):
        count["s"] += 1
        num = f"{n}.{count['s']}"
        sections.append((m.group(1), num, re.sub(r"<[^>]+>", "", m.group(2))))
        return f'<h2 id="{m.group(1)}"><span class="n">{num}</span>{m.group(2)}</h2>'

    def fig(m):
        count["f"] += 1
        return f"<figcaption><b>Figure {n}.{count['f']}.</b> "

    def tab(m):
        count["t"] += 1
        return f"<caption><b>Table {n}.{count['t']}.</b> "

    body = re.sub(r'<h2 id="([^"]+)">(.*?)</h2>', h2, body, flags=re.S)
    body = re.sub(r"<figcaption>", fig, body)
    body = re.sub(r"<caption>", tab, body)
    return body, sections


def rail(contents, here=None, sections=()):
    out = ['<aside class="rail"><div class="rail-inner">']
    if sections:
        out.append("<h2>In this chapter</h2><ol>")
        for sid, num, title in sections:
            out.append(f'<li><a href="#{sid}"><span class="n">{num}</span>{html.escape(title)}</a></li>')
        out.append("</ol>")
    out.append('<div class="book"><h2>The book</h2><ol>')
    for part in contents["parts"]:
        for ch in part["chapters"]:
            if ch.get("slug"):
                cls = ' class="here"' if ch["slug"] == here else ""
                out.append(
                    f'<li{cls}><a href="{ch["slug"]}.html"><span class="n">{ch["n"]}</span>{html.escape(ch["title"])}</a></li>'
                )
            else:
                out.append(f'<li class="planned"><span class="n">{ch["n"]}</span> {html.escape(ch["title"])}</li>')
    out.append('</ol></div></div></aside>')
    return "".join(out)


def cross_refs(text, numbers, where):
    """Turn {{ch:slug}} into a link to that chapter, by its number. An unknown name stops the build."""
    def one(m):
        slug = m.group(1)
        if slug not in numbers:
            raise SystemExit(f"{where}: refers to a chapter that does not exist: {slug}")
        return f'<a href="{slug}.html">Chapter {numbers[slug]}</a>'
    return re.sub(r"\{\{ch:([a-z0-9-]+)\}\}", one, text)


def read_entry(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"\s*<!--(.*?)-->", text, re.S)
    meta = {}
    for line in m.group(1).strip().splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, text[m.end():].strip()


def fill(text, values):
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", str(value))
    left = re.findall(r"\{\{[a-z_]+\}\}", text)
    if left:
        raise SystemExit(f"unfilled placeholders: {sorted(set(left))}")
    return text


def build():
    # Overwrite in place: a running preview server may hold the folder open,
    # so the folder itself is never deleted. Stale pages are removed one by one.
    (DIST / "assets").mkdir(parents=True, exist_ok=True)
    for old in DIST.glob("*.html"):
        old.unlink()
    for f in (ROOT / "assets").iterdir():
        shutil.copy(f, DIST / "assets" / f.name)

    contents = json.loads((CONTENT / "contents.json").read_text(encoding="utf-8"))
    site = contents.get("site", {})
    repo = site.get("repo", "").strip()
    written = [(p, c) for p in contents["parts"] for c in p["chapters"] if c.get("slug")]
    numbers = {c["slug"]: c["n"] for _, c in written}
    entries = sorted((read_entry(p) for p in PRIVATE_LOG.glob("*.html")), key=lambda e: int(e[0]["n"])) if "--log" in sys.argv else []

    # Chapters
    for i, (part, ch) in enumerate(written):
        raw = (CONTENT / "chapters" / f'{ch["slug"]}.html').read_text(encoding="utf-8")
        body, sections = number_chapter(ch["n"], cross_refs(raw, numbers, ch["slug"]))
        prev_ = written[i - 1][1] if i else None
        next_ = written[i + 1][1] if i + 1 < len(written) else None
        pager = ['<nav class="pager" aria-label="Chapters">']
        if prev_:
            pager.append(f'<a href="{prev_["slug"]}.html"><span class="dir">Previous</span>{prev_["n"]}. {html.escape(prev_["title"])}</a>')
        if next_:
            pager.append(f'<a class="next" href="{next_["slug"]}.html"><span class="dir">Next</span>{next_["n"]}. {html.escape(next_["title"])}</a>')
        else:
            pager.append('<a class="next" href="deploy.html"><span class="dir">Next</span>Deploy your own</a>')
        pager.append("</nav>")
        main = f"""<div class="frame">{rail(contents, ch["slug"], sections)}
<main><article class="text">
<header class="opener">
<div class="part">{html.escape(part["title"])}</div>
<div class="chno">Chapter {ch["n"]}</div>
<h1>{html.escape(ch["title"])}</h1>
<p class="lede">{ch["summary"]}</p>
</header>
{body}
{"".join(pager)}
</article></main></div>"""
        (DIST / f'{ch["slug"]}.html').write_text(
            page(f'{ch["n"]}. {ch["title"]} | Odysseus', re.sub(r"<[^>]+>", "", ch["summary"]), main, "book.html"), encoding="utf-8"
        )

    # Build log
    log = ['<div class="frame"><aside class="rail"><div class="rail-inner"><h2>Entries</h2><ol>']
    for meta, _ in reversed(entries):
        log.append(f'<li><a href="#entry-{meta["n"]}"><span class="n">{int(meta["n"]):03d}</span>{html.escape(meta["title"])}</a></li>')
    log.append('</ol></div></aside><main><div class="text">')
    log.append(
        '<header class="opener"><div class="part">Laboratory notebook</div><h1>The build log</h1>'
        '<p class="lede">What I tried, in the order it happened: what I decided, what was measured and what went wrong. '
        "Newest entry first. The chapters explain; the log records.</p></header>"
    )
    for meta, body in reversed(entries):
        log.append(
            f'<section class="entry" id="entry-{meta["n"]}"><div class="when">Entry {int(meta["n"]):03d} · {meta["date"]}</div>'
            f'<h2>{html.escape(meta["title"])}</h2>{body}</section>'
        )
    log.append("</div></main></div>")
    if entries:     # only with --log, and only into the private folder
        private_page = page("Build log (private) | Odysseus", "The owner's private record of building Odysseus.", "".join(log), "log.html")
        private_page = private_page.replace('href="assets/', 'href="../site/assets/')
        private_page = re.sub(r'href="(index|book|deploy|evidence)\.html"', r'href="../site/dist/\1.html"', private_page)
        (PRIVATE_LOG.parent / "log.html").write_text(private_page, encoding="utf-8")

    # Evidence page (plain single page)
    ev = cross_refs((CONTENT / "evidence.html").read_text(encoding="utf-8"), numbers, "evidence")
    (DIST / "evidence.html").write_text(
        page("How to read the evidence | Odysseus", "The evidence grades used across the Odysseus site.",
             f'<div class="frame plain"><main><article class="text">{ev}</article></main></div>', "book.html"),
        encoding="utf-8",
    )

    # The book's contents page
    cover = cross_refs((CONTENT / "cover.html").read_text(encoding="utf-8"), numbers, "cover")
    toc = ['<section class="toc"><h2>Contents</h2>']
    for part in contents["parts"]:
        toc.append(f'<div class="parttitle">{html.escape(part["title"])}</div>'
                   + (f'<p class="partnote">{part["note"]}</p>' if part.get("note") else "") + "<ol>")
        for ch in part["chapters"]:
            if ch.get("slug"):
                toc.append(f'<li><span class="n">{ch["n"]}</span><a href="{ch["slug"]}.html">{html.escape(ch["title"])}</a><p>{ch["summary"]}</p></li>')
            else:
                toc.append(f'<li class="planned"><span class="n">{ch["n"]}</span><span><span class="t">{html.escape(ch["title"])}</span><span class="tag">not yet written</span></span><p>{ch["summary"]}</p></li>')
        toc.append("</ol>")
    toc.append("</section>")
    (DIST / "book.html").write_text(
        page("The book | Odysseus", "A textbook on building a personal AI agent, from first principles to the code that runs.",
             f'<div class="frame plain"><main>{cover}{"".join(toc)}</main></div>', "book.html"),
        encoding="utf-8",
    )

    # Product page
    home = fill(cross_refs((CONTENT / "home.html").read_text(encoding="utf-8"), numbers, "home"),
                {"chapters": len(written), "tests": site.get("tests", ""), "mark": MARK, "repo": html.escape(repo or "book.html", quote=True)})
    (DIST / "index.html").write_text(
        page("Odysseus: an assistant that does the work, and asks before it matters",
             "A personal AI agent that lives in your chat, works on your accounts and asks before anything that cannot be undone. "
             "Open source, and explained in a textbook.", f"<main>{home}</main>", "index.html"),
        encoding="utf-8",
    )

    # Deploy guide
    if repo:
        notice = ""
        clone = (f'<div class="cmd"><pre><code>git clone {html.escape(repo)}.git odysseus\ncd odysseus</code></pre></div>'
                 f'<p>The code is at <a href="{html.escape(repo, quote=True)}">{html.escape(repo)}</a>.</p>')
    else:
        notice = ('<div class="callout bad"><p><b>The code is not public yet.</b> Odysseus is developed in a private repository that has not '
                  "been published. Everything below describes exactly how it is installed and deployed, and will work as written once "
                  "the repository address appears in step 1. Until then this page is a description, not something you can follow.</p></div>")
        clone = "<p>Get the code from the project's repository (its address will appear here when it is published) and open a terminal in its folder.</p>"
    deploy = fill(cross_refs((CONTENT / "deploy.html").read_text(encoding="utf-8"), numbers, "deploy"), {"repo_notice": notice, "clone": clone})
    (DIST / "deploy.html").write_text(
        page("Deploy your own | Odysseus", "Step-by-step guide to running your own Odysseus on your computer and on a small server.",
             f'<main><article class="guide">{deploy}</article></main>', "deploy.html"),
        encoding="utf-8",
    )
    print(f"built {len(written)} chapters, product page and deploy guide -> {DIST}"
          + (f"; private log of {len(entries)} entries -> {PRIVATE_LOG.parent / 'log.html'}" if entries else ""))


if __name__ == "__main__":
    build()
