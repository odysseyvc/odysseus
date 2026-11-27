"""How a reply looks: the model's few marks, turned by code into what each chat shows, and never into a broken message.

Everything here is made up. No test sends anything.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

from odysseus import agent, fmt

ALLOWED = {"b", "i", "s", "code", "pre", "a", "blockquote"}


class Balanced(HTMLParser):
    """Telegram refuses a message whose tags are not closed in order, or that uses a tag it does not know."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.open, self.bad = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED:
            self.bad.append(tag)
        self.open.append(tag)

    def handle_endtag(self, tag):
        if not self.open or self.open.pop() != tag:
            self.bad.append("/" + tag)


def well_formed(html: str) -> bool:
    p = Balanced()
    p.feed(html)
    p.close()
    return not p.open and not p.bad


def test_each_mark_becomes_each_chats_own():
    src = "**Northwind** raised _a seed round_ of `$2M`, ~~not $3M~~."
    assert fmt.telegram(src) == "<b>Northwind</b> raised <i>a seed round</i> of <code>$2M</code>, <s>not $3M</s>."
    assert fmt.whatsapp(src) == "*Northwind* raised _a seed round_ of `$2M`, ~not $3M~."
    assert fmt.plain(src) == "Northwind raised a seed round of $2M, not $3M."


def test_lists_quotes_and_kept_text():
    src = "Three things:\n- one\n- **two**\n\n1. first\n2. second\n\n> she said yes\n> twice\n\n```\na < b\n```"
    t = fmt.telegram(src)
    assert "• one\n• <b>two</b>" in t and "1. first\n2. second" in t
    assert "<blockquote>she said yes\ntwice</blockquote>" in t and "<pre>a &lt; b</pre>" in t and well_formed(t)
    w = fmt.whatsapp(src)
    assert "- one\n- *two*" in w and "> she said yes\n> twice" in w and "```a < b```" in w


def test_what_the_chat_cannot_show_is_turned_into_something_it_can():
    src = "## Pipeline\n\n| Company | Stage | Ask |\n|---|---|---|\n| Northwind | Seed | $1M |\n| Corvid | A | $5M |\n\n---\n\nDone."
    t, w = fmt.telegram(src), fmt.whatsapp(src)
    assert t.startswith("<b>Pipeline</b>") and "• <b>Northwind</b>: Stage: Seed; Ask: $1M" in t and "|" not in t and "---" not in t
    assert w.startswith("*Pipeline*") and "- *Corvid*: Stage: A; Ask: $5M" in w and "#" not in w


def test_text_from_elsewhere_cannot_break_or_fake_a_message():
    src = "The note says <b>urgent</b> & </blockquote><a href='x'>click</a> **done**"
    t = fmt.telegram(src)
    assert "&lt;b&gt;urgent&lt;/b&gt; &amp; &lt;/blockquote&gt;" in t and t.endswith("<b>done</b>") and well_formed(t)


def test_a_mark_left_open_is_shown_as_typed():
    for src in ("so far **bold and", "a stray * star and _ underscore", "`never closed", "```\nno end", "[name](not a link)", "5 * 4 * 3 = 60"):
        assert well_formed(fmt.telegram(src)), src
        assert fmt.plain(src) == src


def test_names_with_underscores_and_stars_are_left_alone():
    src = "founder_referrals.pptx and snake_case_name and 2*3*4 and a_b"
    assert fmt.telegram(src) == src and fmt.whatsapp(src) == src


def test_a_bare_address_is_never_styled():
    src = "Open https://example.com/_a_/b*c*?x=1&y=_z_. Then **go**."
    t = fmt.telegram(src)
    assert "https://example.com/_a_/b*c*?x=1&amp;y=_z_." in t and "<i>" not in t and t.endswith("<b>go</b>.")
    assert "https://example.com/_a_/b*c*?x=1&y=_z_." in fmt.whatsapp(src)


def test_a_link_by_name_and_the_tricks_it_allows():
    assert fmt.telegram("See [the report](https://news.example/a?b=1&c=2).") == 'See <a href="https://news.example/a?b=1&amp;c=2">the report</a>.'
    assert fmt.whatsapp("See [the report](https://news.example/a).") == "See the report (https://news.example/a)."
    # a name that looks like another address is written out in full, on every chat
    assert fmt.telegram("[mybank.example](https://elsewhere.example/login)") == "mybank.example (https://elsewhere.example/login)"
    assert fmt.telegram("[news.example](https://www.news.example/a)").startswith("<a href=")
    assert fmt.disguised("mybank.example", "https://mybank.example.elsewhere.example/") is True
    # only web addresses are links
    assert "<a" not in fmt.telegram("[run](javascript:alert(1)) [app](tg://resolve?domain=x)")
    # in a question put for approval no address is hidden at all
    assert fmt.telegram("[the report](https://news.example/a)", links=False) == "the report (https://news.example/a)"
    # an attribute cannot be closed from inside the address
    t = fmt.telegram('[x](https://a.example/"onclick="y)')
    assert well_formed(t) and '"onclick' not in t.split("href=")[-1].split(">")[0].replace("&quot;", "")


def test_nothing_wraps_text_marked_to_be_copied():
    t = fmt.telegram("**the code is `AB-12`** and [the `x` page](https://a.example/)")
    assert "<b>the code is AB-12</b>" in t and "<code>" not in t and well_formed(t)


def test_a_long_quotation_folds_on_telegram():
    long = "\n".join("> line %d of what they wrote, at some length, to fill the space" % i for i in range(20))
    assert fmt.telegram(long).startswith("<blockquote expandable>") and fmt.telegram("> short").startswith("<blockquote>")


def test_many_made_up_replies_are_always_well_formed():
    import random
    rnd = random.Random(7)
    bits = ["**", "*", "_", "`", "~~", "[a](https://x.example/y)", "<", ">", "&", "\n", "\n\n", "- ", "1. ", "> ", "```", "# ", "|", " ", "word",
            "https://a.example/_b_", "snake_case", "(", ")", "[", "]"]
    for _ in range(3000):
        src = "".join(rnd.choice(bits) for _ in range(rnd.randint(1, 30)))
        t = fmt.telegram(src)
        assert well_formed(t), repr(src)
        assert len(re.sub(r"<[^>]+>", "", t)) <= len(src) + src.count("\n") + 40, "what is shown is never much longer than what was written"
        fmt.whatsapp(src), fmt.plain(src)


def test_a_long_reply_is_cut_between_paragraphs_and_never_inside_kept_text():
    src = ("First paragraph with **bold words** in it.\n\n" + "- item\n" * 30 + "\n```\n" + "kept line\n" * 20 + "```\n\nLast.")
    got = fmt.parts(src, 300)
    assert len(got) > 1 and all(p.strip() for p in got) and "".join(got).replace("\n", "") == src.replace("\n", "")
    assert all(p.count("**") % 2 == 0 for p in got)
    assert fmt.parts("short", 300) == ["short"] and fmt.parts("   \n ", 300) == []
    assert all(len(p) <= 50 for p in fmt.parts("x" * 500, 50))


def test_the_model_is_told_the_marks_and_no_longer_told_there_are_none():
    for prompt in (agent.VOICE_PROMPT, agent.ROUTINE_PROMPT):
        assert "exactly these marks" in prompt and "@MARKS@" not in prompt
        assert "Plain text only" not in prompt and "no Markdown" not in prompt
    assert "Do not use Markdown markup" not in agent.SYSTEM_PROMPT


async def test_telegram_is_sent_the_marked_form_and_the_plain_words_if_it_refuses():
    from aiogram.exceptions import TelegramBadRequest
    from odysseus.telegram import TelegramChat

    class Bot:
        def __init__(self, refuse=False):
            self.calls, self.refuse = [], refuse

        async def send_message(self, chat_id, text, parse_mode=None, **more):
            self.calls.append((text, parse_mode))
            if self.refuse and parse_mode == "HTML":
                raise TelegramBadRequest(method=None, message="can't parse entities")

        async def send_message_draft(self, chat_id, draft_id, text="", parse_mode=None, **more):
            self.calls.append((text, parse_mode))

    bot = Bot()
    await TelegramChat(bot, 7).send("**Done.** Filed under _Northwind_.")
    await TelegramChat(bot, 7).draft(1, "**Done.** Filed under _North")
    await TelegramChat(bot, 7).send("   ")
    assert bot.calls == [("<b>Done.</b> Filed under <i>Northwind</i>.", "HTML"), ("<b>Done.</b> Filed under _North", "HTML")]
    bot = Bot(refuse=True)
    await TelegramChat(bot, 7).send("**Done.** a < b")
    assert bot.calls == [("<b>Done.</b> a &lt; b", "HTML"), ("Done. a < b", None)]
