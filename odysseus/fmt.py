"""How a reply looks: one small set of marks the model may write, and code that turns them into what each chat shows.

A chat is read on a phone, a few lines at a time. Two things go wrong there. Marks the chat does not understand are
shown as typed, so a reply written as for a document arrives full of asterisks and hashes. And each chat has marks of
its own: bold is two asterisks in one place, one in another and a tag in a third, so a reply written for one chat is
wrong in the next. For a long time the answer here was no marks at all, which is safe and reads flat: a figure the
owner is looking for sits in the middle of a sentence like every other word.

So the model writes in one small set of marks, whatever chat the reply is going to, and this file is the only place
that knows what a chat can show:

  **bold**            _italic_            ~~struck~~          `to be copied exactly`
  - an item           1. a step           > someone's words   [a name](https://address)
  a line of ``` above and below text to be kept as it is

Nothing else is a mark. A heading written with # becomes a bold line; a table becomes one line a row; anything not
understood is shown as it was typed.

What is held by code:

  no broken message   the marks are read into pieces and each chat's form is written out from the pieces, so tags
                      are always closed and text taken from elsewhere is escaped. A mark left open (a reply still
                      arriving, or a stray asterisk) is shown as typed. If a chat still refuses the result, the same
                      text goes out with no marks at all (telegram.py).
  no hidden address   a link shown by name hides where it goes. A name that itself looks like an address, and is not
                      the address it leads to, is the classic trick: such a link is written out in full, name then
                      address. Only http and https addresses are links. In a question put to the owner for approval
                      no link is hidden at all (`links=False`).
  an address is       a web address written bare is left exactly as it is, whatever marks it seems to contain.
  never styled

`telegram` writes Telegram's HTML, `whatsapp` writes WhatsApp's own marks, `plain` writes no marks (a chat with none,
a spoken reply, a check of what was said). `parts` cuts a long reply between paragraphs before it is turned into a
chat's form, so a cut never falls inside a mark.
"""
from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urlsplit

QUOTE_FOLDS_AT = 600        # a quotation longer than this is folded on Telegram, to be opened by a tap

_INLINE = re.compile(
    r"(?P<url>https?://[^\s<>]+)"
    r"|`(?P<code>[^`\n]+)`"
    r"|\[(?P<name>[^\]\n]+)\]\((?P<href>https?://[^\s)]+)\)"
    r"|\*\*(?=\S)(?P<b>[^\n]+?)(?<=\S)\*\*"
    r"|(?<![\w*])\*(?=[^\s*])(?P<b1>[^*\n]+?)(?<=[^\s*])\*(?![\w*])"
    r"|(?<![\w_])_(?=[^\s_])(?P<i>[^_\n]+?)(?<=[^\s_])_(?![\w_])"
    r"|~~(?=\S)(?P<s>[^\n]+?)(?<=\S)~~")
_BULLET = re.compile(r"^\s{0,3}[-*•]\s+(.*)$")
_NUMBER = re.compile(r"^\s{0,3}(\d{1,3})[.)]\s+(.*)$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_DOMAIN = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", re.I)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def disguised(name: str, url: str) -> bool:
    """Does the name of a link look like an address other than the one it leads to?"""
    host = _host(url)
    if not host:
        return True
    for seen in _DOMAIN.findall(name):
        seen = seen.lower().removeprefix("www.")
        if seen != host and not host.endswith("." + seen):
            return True
    return False


# --- the pieces --------------------------------------------------------------------------------------------------
def _spans(text: str) -> list[tuple]:
    """One line of text as pieces: ("t", text), ("code", text), ("url", address), ("link", pieces, address), or a
    style ("b" | "i" | "s", pieces)."""
    out, at = [], 0
    for m in _INLINE.finditer(text):
        if m.start() > at:
            out.append(("t", text[at:m.start()]))
        if m.group("url"):
            url = m.group("url")
            tail = ""
            while url and url[-1] in ".,;:!?)]'\"*_":           # the end of the sentence is not the end of the address
                if url[-1] == ")" and url.count("(") >= url.count(")"):
                    break
                url, tail = url[:-1], url[-1] + tail
            out.append(("url", url))
            if tail:
                out.extend(_spans(tail) if set(tail) & set("*_") and len(tail) > 1 else [("t", tail)])
        elif m.group("code"):
            out.append(("code", m.group("code")))
        elif m.group("href"):
            out.append(("link", _spans(m.group("name")), m.group("href")))
        else:
            kind = "b" if m.group("b") or m.group("b1") else ("i" if m.group("i") else "s")
            out.append((kind, _spans(m.group("b") or m.group("b1") or m.group("i") or m.group("s"))))
        at = m.end()
    if at < len(text):
        out.append(("t", text[at:]))
    return out


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def blocks(text: str) -> list[tuple]:
    """A reply as blocks: ("p", lines), ("ul", items), ("ol", [(n, item)]), ("quote", lines), ("pre", text), ("h", line)."""
    out: list[tuple] = []
    lines = str(text or "").replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            end = next((j for j in range(i + 1, len(lines)) if lines[j].strip().startswith("```")), None)
            if end is None:                         # a fence never closed: shown as typed, as a paragraph
                got, i = [line], i + 1
                while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith("```"):
                    got.append(lines[i])
                    i += 1
                out.append(("p", got))
                continue
            out.append(("pre", "\n".join(lines[i + 1:end])))
            i = end + 1
        elif not line.strip() or _RULE.match(line):
            i += 1
        elif _HEADING.match(line):
            out.append(("h", _HEADING.match(line).group(1)))
            i += 1
        elif line.lstrip().startswith(">"):
            got = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                got.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append(("quote", got))
        elif line.strip().startswith("|") and i + 1 < len(lines) and _TABLE_RULE.match(lines[i + 1]) and "|" in lines[i + 1]:
            head, i = _cells(line), i + 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            # A phone has no room for columns. Each row becomes a line: its first cell, then the rest under their headings.
            items = []
            for row in rows:
                rest = [f"{h}: {v}" if h else v for h, v in zip(head[1:], row[1:]) if v]
                items.append(f"**{row[0]}**" + (": " + "; ".join(rest) if rest else "") if row and row[0] else "; ".join(rest))
            out.append(("ul", items))
        elif _BULLET.match(line) and not _RULE.match(line):
            items = []
            while i < len(lines) and _BULLET.match(lines[i]):
                items.append(_BULLET.match(lines[i]).group(1))
                i += 1
            out.append(("ul", items))
        elif _NUMBER.match(line):
            items = []
            while i < len(lines) and _NUMBER.match(lines[i]):
                m = _NUMBER.match(lines[i])
                items.append((m.group(1), m.group(2)))
                i += 1
            out.append(("ol", items))
        else:
            got = []
            while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(("```", ">")) and not _HEADING.match(lines[i]) \
                    and not _BULLET.match(lines[i]) and not _NUMBER.match(lines[i]):
                got.append(lines[i])
                i += 1
            if not got:                             # a line that is none of the above and was not taken: never loop on it
                got, i = [lines[i]], i + 1
            out.append(("p", got))
    return out


# --- one chat's form ---------------------------------------------------------------------------------------------
class _Plain:
    """No marks at all."""
    gap = "\n\n"

    def __init__(self, links: bool = True):
        self.links = links

    def t(self, s): return s
    def code(self, s): return s
    def url(self, u): return u
    def b(self, inner): return inner
    def i(self, inner): return inner
    def s(self, inner): return inner
    def link(self, inner, bare, url): return bare if bare == url else f"{bare} ({url})"
    def p(self, lines): return "\n".join(lines)
    def h(self, line): return line
    def ul(self, items): return "\n".join("- " + x for x in items)
    def ol(self, items): return "\n".join(f"{n}. {x}" for n, x in items)
    def quote(self, lines, raw): return "\n".join("> " + x for x in lines)
    def pre(self, text): return text


class _WhatsApp(_Plain):
    def code(self, s): return f"`{s}`"
    def b(self, inner): return f"*{inner}*" if inner.strip() else inner
    def i(self, inner): return f"_{inner}_" if inner.strip() else inner
    def s(self, inner): return f"~{inner}~" if inner.strip() else inner
    def h(self, line): return f"*{line}*"
    def pre(self, text): return f"```{text}```"


class _Telegram(_Plain):
    def t(self, s): return html.escape(s, quote=False)
    def code(self, s): return f"<code>{html.escape(s, quote=False)}</code>"
    def url(self, u): return html.escape(u, quote=False)
    def b(self, inner): return f"<b>{inner}</b>"
    def i(self, inner): return f"<i>{inner}</i>"
    def s(self, inner): return f"<s>{inner}</s>"

    def link(self, inner, bare, url):
        if not self.links or disguised(bare, url):
            return f"{inner} ({html.escape(url, quote=False)})"
        return f'<a href="{html.escape(url, quote=True)}">{inner}</a>'

    def h(self, line): return f"<b>{line}</b>"
    def ul(self, items): return "\n".join("• " + x for x in items)

    def quote(self, lines, raw):
        fold = " expandable" if len(raw) > QUOTE_FOLDS_AT else ""
        return f"<blockquote{fold}>" + "\n".join(lines) + "</blockquote>"

    def pre(self, text): return f"<pre>{html.escape(text, quote=False)}</pre>"


def _bare(spans: list[tuple]) -> str:
    return "".join(x[1] if x[0] in ("t", "code", "url") else _bare(x[1]) for x in spans)


def _inline(spans: list[tuple], f: Any, styled: bool = True, inside: bool = False) -> str:
    out = []
    for x in spans:
        if x[0] == "t":
            out.append(f.t(x[1]))
        elif x[0] == "code":
            out.append(f.t(x[1]) if inside else f.code(x[1]))        # Telegram allows nothing to wrap text marked to be copied
        elif x[0] == "url":
            out.append(f.url(x[1]))
        elif x[0] == "link":
            out.append(f.link(_inline(x[1], f, inside=True), _bare(x[1]), x[2]))
        else:
            inner = _inline(x[1], f, inside=True)
            out.append(getattr(f, x[0])(inner) if styled and not inside else inner)
    return "".join(out)


def _render(text: str, f: Any) -> str:
    out = []
    for b in blocks(text):
        kind = b[0]
        if kind == "p":
            out.append(f.p([_inline(_spans(x), f) for x in b[1]]))
        elif kind == "h":
            out.append(f.h(_inline(_spans(b[1]), f, styled=False)))        # already bold as a whole: no bold inside bold
        elif kind == "ul":
            out.append(f.ul([_inline(_spans(x), f) for x in b[1]]))
        elif kind == "ol":
            out.append(f.ol([(n, _inline(_spans(x), f)) for n, x in b[1]]))
        elif kind == "quote":
            out.append(f.quote([_inline(_spans(x), f) for x in b[1]], "\n".join(b[1])))
        elif kind == "pre":
            out.append(f.pre(b[1]))
    return f.gap.join(x for x in out if x.strip())


def telegram(text: str, links: bool = True) -> str:
    """The reply in Telegram's HTML. Always well formed: every tag written is closed and all other text is escaped."""
    return _render(text, _Telegram(links))


def whatsapp(text: str) -> str:
    """The reply in WhatsApp's own marks. A link by name is written as the name and then the address."""
    return _render(text, _WhatsApp())


def plain(text: str) -> str:
    """The reply with no marks: for a chat that shows none, for a spoken reply, and for checking what was said."""
    return _render(text, _Plain())


def settle(text: str) -> str:
    """A reply still arriving, with a mark that has been opened and not yet closed taken off its end. Shown as typed, a
    lone pair of asterisks sat in the chat for a moment before the bold word arrived behind it."""
    text = str(text or "")
    if text.count("**") % 2:
        at = text.rfind("**")
        text = text[:at] + text[at + 2:]
    if text.count("`") % 2:
        at = text.rfind("`")
        text = text[:at] + text[at + 1:]
    return text


def parts(text: str, limit: int) -> list[str]:
    """A long reply as several, each within `limit`, cut between blocks where it can be and never inside a mark's line.
    The parts are still in the model's marks: turn each into a chat's form after cutting."""
    text = str(text or "")
    if len(text) <= limit:
        return [text] if text.strip() else []
    out, now = [], ""
    for para in re.split(r"\n{2,}", text):
        if "```" in now and now.count("```") % 2:           # inside a kept block: never cut here
            now += "\n\n" + para
            continue
        if now and len(now) + 2 + len(para) > limit:
            out.append(now)
            now = ""
        now = now + "\n\n" + para if now else para
    if now:
        out.append(now)
    done = []
    for piece in out:                                        # one block longer than the limit: cut at a line, then anywhere
        while len(piece) > limit:
            cut = piece.rfind("\n", 0, limit)
            if cut < limit // 2:
                cut = limit
            done.append(piece[:cut])
            piece = piece[cut:].lstrip("\n")
        done.append(piece)
    return [p for p in done if p.strip()]
