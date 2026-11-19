"""A long read runs in the background: the chat stays free, a second link waits its turn, and stop ends it."""
from __future__ import annotations

import asyncio

from odysseus.deckview import View
from test_fresh import OWNER, rig

LINK = "https://docsend.com/view/abc123xyz"
OTHER = "https://docsend.com/view/zzz999"
PAGES = "[page 1]\nCorvid Freight\n\n[page 2]\nTraction: 14 fleets"


class SlowViewer:
    """Stands in for the browser read, which takes minutes: it finishes only when the test lets it."""

    def __init__(self, ok=True):
        self.asked, self.go, self.ok = [], asyncio.Event(), ok

    async def view(self, url):
        self.asked.append(url)
        await self.go.wait()
        return View(True, PAGES, pages=2, cost_usd=0.08, verified=True) if self.ok else View(False, "the site asks for a passcode, which only the sender can give")


def reading_rig(tmp_path, ok=True):
    x = rig(tmp_path)
    x.viewer = x.conv._deckview = SlowViewer(ok)
    return x


async def settle(x):
    for _ in range(200):
        if x.conv._task is not None and x.conv._task.done():
            return
        await asyncio.sleep(0.01)


async def test_while_a_document_is_being_read_the_owner_can_go_on_talking_and_is_answered(tmp_path):
    x = reading_rig(tmp_path)
    await x.conv.handle(OWNER, LINK, x.chat)
    assert x.chat.sent == ["Opening that in a browser and reading it page by page. This takes a few minutes; you can keep messaging me meanwhile."]
    assert x.viewer.asked == [] or x.viewer.asked == [LINK]
    await x.conv.handle(OWNER, "What time is my call tomorrow?", x.chat)
    await settle(x)
    assert len(x.clients) == 1 and "What time is my call tomorrow?" in x.clients[0].prompts[0], "answered while the reading is still going on"
    assert "Corvid Freight" not in str(x.clients[0].prompts), "and the document has not arrived yet"
    assert x.chat.sent[-1].startswith("Reply 1."), "the owner was not told to wait"
    x.viewer.go.set()
    await x.conv.wait()
    last = x.clients[0].prompts[-1]
    assert "Corvid Freight" in last and "Traction: 14 fleets" in last, "when the pages are in, the take is a task of its own"
    assert x.conv._viewing is False and x.viewer.asked == [LINK]


async def test_a_second_link_waits_its_turn_and_is_not_handed_to_the_model_as_words(tmp_path):
    x = reading_rig(tmp_path)
    await x.conv.handle(OWNER, LINK, x.chat)
    await x.conv.handle(OWNER, OTHER, x.chat)
    assert x.chat.sent[-1] == "I'm still reading the earlier document. I'll open this one as soon as that is done."
    assert x.clients == [], "no model has been shown a link it cannot open"
    x.viewer.go.set()
    await x.conv.wait()
    assert x.viewer.asked == [LINK, OTHER], "the second was read once the first was done"


async def test_a_reading_that_fails_is_said_once_and_does_not_start_another(tmp_path):
    x = reading_rig(tmp_path, ok=False)
    await x.conv.handle(OWNER, LINK, x.chat)
    x.viewer.go.set()
    await x.conv.wait()
    assert x.viewer.asked == [LINK], "the same link is not tried again when the failure is handed to the model"
    prompt = x.clients[0].prompts[0]
    assert "could not: the site asks for a passcode" in prompt and "Nothing of it has been read" in prompt


async def test_stop_ends_a_reading_in_the_background(tmp_path):
    x = reading_rig(tmp_path)
    await x.conv.handle(OWNER, LINK, x.chat)
    await x.conv.handle(OWNER, OTHER, x.chat)
    await asyncio.sleep(0.05)
    await x.conv.handle(OWNER, "stop", x.chat)
    assert x.chat.sent[-1] == "Stopped reading that document."
    await asyncio.sleep(0.05)
    assert x.conv._reading.cancelled() and x.conv._viewing is False and not x.conv._view_next
    x.viewer.go.set()
    await asyncio.sleep(0.05)
    assert x.clients == [], "nothing read after the stop reaches a model"
    assert [r for r in x.journal.read() if r["kind"] == "stop_requested"][0].get("reading") is True
