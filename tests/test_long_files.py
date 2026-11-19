"""A PDF too long to look at whole: only the pages with pictures are looked at, picked by code."""
from __future__ import annotations

import io

from pypdf import PdfReader, PdfWriter

from odysseus.config import Settings
from odysseus.journal import Journal, Ledger
from odysseus.ocr import MAX_SEEN_PAGES, PDF, OcrResult, join_pages, pages_text, pick_pages, pictured_pages, shown_in_part
from odysseus.pages import make_page_reader
from odysseus.reading import Reader
from test_slice import OWNER, FakeClient, result


def pdf(pages: int) -> bytes:
    w = PdfWriter()
    for i in range(pages):
        w.add_blank_page(width=200 + i, height=100)      # each page a different width, so that a test can tell them apart
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


class Ocr:
    def __init__(self, pages, pictured):
        self.result = OcrResult(join_pages([f"Words of page {i}. " + "More words. " * 30 for i in range(1, pages + 1)]), pages, tuple(pictured))

    def read(self, data, mime):
        return self.result


def rig(tmp_path, pages, pictured, notes="[page 12] A bar chart with no scale."):
    journal, calls = Journal(tmp_path / "j.jsonl"), []

    async def look(data, text, numbers=()):
        calls.append((data, text, numbers))
        return notes, 0.03
    return Reader(Ocr(pages, pictured), look, journal, Ledger(journal), 3.0), calls, journal


def test_code_picks_the_pages_with_a_picture_or_almost_no_words_from_what_text_recognition_returned():
    long = "Plenty of words on this page. " * 20
    assert pictured_pages([(long, False), (long, True), ("![img-0.jpeg](img-0.jpeg)\n" + long, False), ("Thank you", False), (long, False)]) == (2, 3, 4)
    many = OcrResult("", 60, tuple(range(1, 41)))
    assert shown_in_part(PDF, 1000, many) == tuple(range(1, MAX_SEEN_PAGES + 1)), "no more than the reader can be shown at once"
    assert shown_in_part(PDF, 1000, OcrResult("", 12, (3,))) == () and shown_in_part("image/png", 1000, many) == ()


def test_the_chosen_pages_are_cut_out_in_order_with_their_own_text():
    part = PdfReader(io.BytesIO(pick_pages(pdf(40), (5, 12, 33))))
    assert [int(p.mediabox.width) for p in part.pages] == [204, 211, 232]
    text = join_pages([f"words {i}" for i in range(1, 6)])
    assert pages_text(text, (2, 4)) == "[page 2]\nwords 2\n\n[page 4]\nwords 4"


async def test_a_long_deck_has_only_its_picture_pages_looked_at_and_the_model_is_told_which(tmp_path):
    reader, calls, journal = rig(tmp_path, 40, (5, 12, 33))
    block = await reader.read(pdf(40), "Series B deck.pdf", PDF)
    (data, text, numbers), = calls
    assert numbers == (5, 12, 33) and len(PdfReader(io.BytesIO(data)).pages) == 3, "three pages are sent, not forty"
    assert text.startswith("[page 5]\nWords of page 5.") and "[page 6]" not in text
    assert "FILE: Series B deck.pdf (40 pages)" in block and "Words of page 40." in block, "every page's text still reaches the model"
    assert "This file has 40 pages, too many to look at every one. Looked at: pages 5, 12, 33, chosen by the program" in block
    assert "The other pages are known only by their text.)\n[page 12] A bar chart with no scale." in block
    rec = [r for r in journal.read() if r["kind"] == "file"][0]
    assert rec["seen"] is True and rec["looked_at"] == 3 and rec["pages"] == 40


async def test_more_picture_pages_than_fit_are_named_and_a_long_file_with_none_is_not_looked_at(tmp_path):
    reader, calls, _ = rig(tmp_path / "many", 60, range(1, 31), notes="")
    block = await reader.read(pdf(60), "Data pack.pdf", PDF)
    assert calls[0][2] == tuple(range(1, 26))
    assert "Not looked at for lack of room, though they also hold pictures: pages 26, 27, 28, 29, 30." in block
    assert "(Nothing beyond the text on the pages looked at.)" in block
    reader, calls, journal = rig(tmp_path / "none", 40, ())
    block = await reader.read(pdf(40), "Contract.pdf", PDF)
    assert calls == [] and "WHAT THE PAGES SHOW" not in block and [r for r in journal.read() if r["kind"] == "file"][0]["seen"] is False


async def test_the_page_reader_is_told_which_of_the_files_pages_it_holds(tmp_path):
    settings = Settings(telegram_token="t", owner_id=OWNER, anthropic_api_key="k", exa_api_key="e", data_dir=tmp_path)
    clients = []

    def factory(options=None):
        clients.append(FakeClient([[result(text="[page 12] A chart.")], [result(text="[page 2] A chart.")]], options=options))
        return clients[-1]
    read = make_page_reader(settings, Journal(tmp_path / "j.jsonl"), object(), client_factory=factory)
    notes, _ = await read(b"%PDF-", "[page 5]\nwords\n\n[page 12]\nwords", numbers=(5, 12))
    said = [m async for m in clients[0].prompts[0]][0]["message"]["content"][1]["text"]
    assert notes == "[page 12] A chart." and "only 2 pages of a longer file" in said and "its pages 5, 12" in said and "the first page you see is page 5" in said
    await read(b"%PDF-", "[page 1]\nwords")
    whole = [m async for m in clients[-1].prompts[-1]][0]["message"]["content"][1]["text"]
    assert "longer file" not in whole and "[page 1]" in whole, "a short file is shown whole and nothing is said of parts"
