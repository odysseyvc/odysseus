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
  readings.json        the bibliography: every work a chapter may cite, each one checked to exist
  course/*.html        the course built on the book: syllabus, problem sets, labs, project, examination;
                       first line is a comment with "key: value" pairs (order, slug, title, summary)
  log/*.html           one build-log entry each; first line is a comment with
                       "key: value" pairs (n, date, title, summary)

The build numbers sections, figures and tables, so chapter files never carry
numbers by hand. A chapter refers to another by its name, written {{ch:the-gate}},
and the build turns that into a link reading "Chapter 8". The book can then be
reordered without a single chapter being edited. A work in the bibliography is cited by
its key, written {{cite:yao2023react}}; a key that is not in readings.json stops the build,
so nothing can be cited that was not checked.
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
<nav aria-label="Site">{nav("index.html", "Product", "hide-sm")}{nav("book.html", "The book")}{nav("course.html", "The course")}{nav("deploy.html", "Run your own", "go")}</nav>
</div></header>
{body}
<footer><div>
<p>Odysseus is built in the open. Every factual claim on this site carries an evidence grade, and anything not yet run is marked as such.</p>
<nav aria-label="Footer"><a href="book.html">The book</a><a href="course.html">The course</a><a href="readings.html">Readings</a><a href="what-is-built.html">What is built</a><a href="deploy.html">Run your own</a><a href="evidence.html">How to read the evidence</a></nav>
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


def short_cite(entry):
    """How a work is named in running text: Yao et al. 2023; Saltzer and Schroeder 1975."""
    if entry.get("short"):
        return entry["short"]
    names = entry.get("surnames") or []
    who = names[0] if len(names) == 1 else f"{names[0]} and {names[1]}" if len(names) == 2 else f"{names[0]} et al." if names else entry["key"]
    return f"{who} {entry['year']}"


def citations(text, readings, where, used):
    """Turn {{cite:key}} into a link to the bibliography. An unknown key stops the build. Several keys may be given, separated by commas."""
    def one(m):
        out = []
        for key in (k.strip() for k in m.group(1).split(",")):
            if key not in readings:
                raise SystemExit(f"{where}: cites a work that is not in readings.json: {key}")
            used.setdefault(key, [])
            if where not in used[key]:
                used[key].append(where)
            out.append(f'<a class="cite" href="readings.html#{key}">{html.escape(short_cite(readings[key]))}</a>')
        return "[" + "; ".join(out) + "]"
    return re.sub(r"\{\{cite:([a-z0-9_, -]+)\}\}", one, text)


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
    titles = {c["slug"]: c["title"] for _, c in written}
    reading_list = json.loads((CONTENT / "readings.json").read_text(encoding="utf-8")) if (CONTENT / "readings.json").exists() else []
    readings = {r["key"]: r for r in reading_list}
    used = {}       # key -> the chapters and course pages that cite it
    entries = sorted((read_entry(p) for p in PRIVATE_LOG.glob("*.html")), key=lambda e: int(e[0]["n"])) if "--log" in sys.argv else []

    # Chapters
    for i, (part, ch) in enumerate(written):
        raw = (CONTENT / "chapters" / f'{ch["slug"]}.html').read_text(encoding="utf-8")
        body, sections = number_chapter(ch["n"], citations(cross_refs(raw, numbers, ch["slug"]), readings, ch["slug"], used))
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

    # The course: syllabus, problem sets, labs, project, examination
    course = sorted((read_entry(p) for p in (CONTENT / "course").glob("*.html")), key=lambda e: int(e[0]["order"])) if (CONTENT / "course").exists() else []
    tabs = "".join(f'<a href="{m["slug"]}.html">{html.escape(m.get("tab") or m["title"])}</a>' for m, _ in course) + '<a href="readings.html">Readings</a>'
    for meta, body in course:
        body = citations(cross_refs(body, numbers, meta["slug"]), readings, meta["slug"], used)
        here = tabs.replace(f'<a href="{meta["slug"]}.html">', f'<a aria-current="page" href="{meta["slug"]}.html">')
        (DIST / f'{meta["slug"]}.html').write_text(
            page(f'{meta["title"]} | Odysseus', meta.get("summary", ""),
                 f'<div class="frame plain"><main><article class="text course"><nav class="coursetabs" aria-label="The course">{here}</nav>'
                 f'<header class="opener"><div class="part">The course</div><h1>{html.escape(meta["title"])}</h1>'
                 f'<p class="lede">{meta.get("summary", "")}</p></header>{body}</article></main></div>', "course.html"),
            encoding="utf-8")

    # The bibliography, with the places each work is cited
    if reading_list:
        groups = []
        for r in reading_list:
            if r["group"] not in groups:
                groups.append(r["group"])
        out = [f'<nav class="coursetabs" aria-label="The course">{tabs.replace(chr(60) + "a href=" + chr(34) + "readings.html" + chr(34) + ">", chr(60) + "a aria-current=" + chr(34) + "page" + chr(34) + " href=" + chr(34) + "readings.html" + chr(34) + ">")}</nav>',
               '<header class="opener"><div class="part">The course</div><h1>Readings</h1>'
               f'<p class="lede">The {len(reading_list)} works the chapters and the course may cite. Each was confirmed to exist before it was listed: '
               f'{sum(1 for r in reading_list if r.get("checked") != "records")} against a page for the work itself, and '
               f'{sum(1 for r in reading_list if r.get("checked") == "records")}, mostly older papers that publishers keep behind a barrier, against their bibliographic records. '
               'A chapter cannot cite a work that is not on this page. Works marked core are the ones units assign; under each is where it is cited.</p></header>']
        for g in groups:
            out.append(f'<h2 id="{re.sub(r"[^a-z]+", "-", g.lower()).strip("-")}">{html.escape(g)}</h2><ul class="readings">')
            for r in sorted((x for x in reading_list if x["group"] == g), key=lambda x: (short_cite(x).lower(), x["year"])):
                where = used.get(r["key"], [])
                cited = ", ".join(f'<a href="{w}.html">{"Chapter " + str(numbers[w]) if w in numbers else html.escape(next((m["title"] for m, _ in course if m["slug"] == w), w))}</a>' for w in where)
                out.append(f'<li id="{r["key"]}"><span class="who">{html.escape(r["authors"])} ({r["year"]}).</span> '
                           f'<a href="{html.escape(r["url"], quote=True)}">{html.escape(r["title"])}</a>. <span class="venue">{html.escape(r["venue"])}.</span>'
                           + (' <span class="core">core</span>' if r.get("level") == "core" else "")
                           + f'<p>{html.escape(r["shows"])}</p>' + (f'<p class="cited">Cited in {cited}.</p>' if cited else "") + "</li>")
            out.append("</ul>")
        (DIST / "readings.html").write_text(
            page("Readings | Odysseus", "The bibliography of the course on building personal agents.",
                 f'<div class="frame plain"><main><article class="text course">{"".join(out)}</article></main></div>', "course.html"), encoding="utf-8")

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
