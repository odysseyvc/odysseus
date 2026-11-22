"""Making documents on the real sandbox machine (sandbox.py, with the image from scripts/build_sandbox.py). No model is called.

  python scripts/live_documents.py [folder]

Checks what the unit tests cannot: that the image has the libraries, that a deck, a Word file and a PDF come out, that
a deck becomes a PDF, that a trial returns pictures of the pages and sends nothing, and that the machine still has no
way out. With a folder, what was made and the pictures are written there to be looked at. Costs about a cent.
"""
from __future__ import annotations

import asyncio
import base64
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus import config  # noqa: E402
from odysseus.files import Shelf  # noqa: E402
from odysseus.journal import Journal, Ledger  # noqa: E402
from odysseus.sandbox import E2B, OUT_DIR, CodeRunner  # noqa: E402
from odysseus.taint import Taint  # noqa: E402

RESULTS: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


MAKE = f'''
import subprocess
from pptx import Presentation
from pptx.util import Inches, Pt
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
for n, (title, body) in enumerate([("Revenue tripled in a year", "From 1.2 to 3.7 in twelve months"), ("Two risks remain", "Concentration and hiring"),
                                   ("The ask", "A made-up figure, for a test"), ("A slide whose text is far too long " * 4, "x " * 400)]):
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = title
    box = s.shapes.add_textbox(Inches(0.8), Inches(2), Inches(11.5), Inches(4)).text_frame
    box.word_wrap = True
    box.text = body
    box.paragraphs[0].runs[0].font.size = Pt(24)
prs.save("{OUT_DIR}/deck.pptx")

d = Document()
d.add_heading("A test memo", 1)
for i in range(3):
    d.add_paragraph("A paragraph of a made-up memo, number %d. " % i * 6)
t = d.add_table(rows=3, cols=3)
for r in range(3):
    for c in range(3):
        t.cell(r, c).text = str(r * c)
d.save("{OUT_DIR}/memo.docx")

done = subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", "{OUT_DIR}", "{OUT_DIR}/deck.pptx"], capture_output=True, text=True, timeout=100)
print("converted", done.returncode)
import os
print(sorted(os.listdir("{OUT_DIR}")))
'''

OUTSIDE = '''
import socket
try:
    socket.create_connection(("example.com", 80), timeout=6); print("REACHED")
except Exception as e:
    print("NO WAY OUT", type(e).__name__)
'''


PRINTED = f'''
import subprocess
from pypdf import PdfReader
html = """<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 22mm 20mm; @bottom-right {{ content: counter(page); font: 9pt Inter; color: #777 }} }}
body {{ font: 11pt/1.5 'Source Serif 4', serif; color: #111 }}
h1 {{ font: 600 28pt Inter; margin: 0 0 4mm }}
.cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 6mm; margin: 8mm 0 }}
.card {{ background: #f4f3ef; border-radius: 3mm; padding: 5mm; break-inside: avoid }}
.card b {{ display: block; font: 600 22pt Inter }}
h2 {{ break-before: page; font: 600 16pt Inter }}
</style></head><body><h1>A made-up memo</h1><p>""" + "A sentence of a made-up memo, for a test. " * 30 + """</p>
<div class="cards"><div class="card"><b>7</b>fleets</div><div class="card"><b>12</b>months</div><div class="card"><b>3x</b>growth</div></div>
<h2>Second page</h2><p>More of it.</p></body></html>"""
open("/home/user/page.html", "w").write(html)
done = subprocess.run(["chromium", "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                       "--print-to-pdf={OUT_DIR}/memo.pdf", "file:///home/user/page.html"], capture_output=True, text=True, timeout=90)
pdf = PdfReader("{OUT_DIR}/memo.pdf")
fonts = subprocess.run(["pdffonts", "{OUT_DIR}/memo.pdf"], capture_output=True, text=True).stdout
print("PRINTED pages=%d width=%d inter=%s serif=%s" % (len(pdf.pages), round(float(pdf.pages[0].mediabox.width)), "Inter" in fonts, "SourceSerif4" in fonts))
'''


async def main() -> int:
    settings = config.load()
    if not settings.e2b_api_key or not settings.sandbox_template:
        print("Needs E2B_API_KEY and ODYSSEUS_SANDBOX_TEMPLATE in the settings.")
        return 1
    keep = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    with tempfile.TemporaryDirectory() as tmp:
        journal = Journal(Path(tmp) / "j.jsonl")
        shelf, sent = Shelf(Path(tmp) / "files"), []

        async def send(entry):
            sent.append(entry)
            return ""
        runner = CodeRunner(E2B(settings.e2b_api_key, settings.sandbox_template), journal, Ledger(journal), 3.0, Taint())
        run = runner.handlers(shelf, send)["run_code"]
        check("the machine says it can make documents", runner.documents)

        out = await run({"code": MAKE, "look": True})
        text = out["content"][0]["text"]
        pictures = [c for c in out["content"] if c["type"] == "image"]
        row = journal.read()[-1]
        check("the trial ran without an error", not out.get("is_error"), text.split("Printed:")[-1].strip()[:120].replace("\n", " "))
        check("a trial sends and keeps nothing", sent == [] and shelf.all() == [])
        check("the deck, the memo and the deck's PDF were made", row.get("made") == 3, f"made {row.get('made')}")
        check("each came back as a picture of its pages", len(pictures) == 3, f"{len(pictures)} pictures, {[len(base64.b64decode(p['data'])) for p in pictures]} bytes")
        check("the deck's pages were counted", "deck.pptx: 4 pages" in text and "deck.pdf: 4 pages" in text, " | ".join(ln for ln in text.splitlines() if ": " in ln and "page" in ln)[:200])
        print(f"     trial took {row['seconds']} s, estimated ${row['cost_usd']}")
        if keep:
            keep.mkdir(parents=True, exist_ok=True)
            for i, p in enumerate(pictures):
                (keep / f"look-{i}.jpg").write_bytes(base64.b64decode(p["data"]))

        out = await run({"code": MAKE})
        check("the run after it sends the three files", len(sent) == 3 and len(shelf.all()) == 3, ", ".join(f"{e['name']} {e['size']} bytes" for e in sent))
        check("the deck is a real file of its kind", any(e["name"] == "deck.pptx" and (shelf.data(e) or b"")[:2] == b"PK" for e in sent)
              and any(e["name"] == "deck.pdf" and (shelf.data(e) or b"")[:4] == b"%PDF" for e in sent))
        if keep:
            for e in sent:
                (keep / e["name"]).write_bytes(shelf.data(e) or b"")

        out = await run({"code": PRINTED, "look": True})
        text = out["content"][0]["text"]
        row = journal.read()[-1]
        check("a web page is printed to a PDF by the browser on the machine: two A4 pages in the typefaces it names",
              "PRINTED pages=2 width=595 inter=True serif=True" in text and len([c for c in out["content"] if c["type"] == "image"]) == 1,
              f"{row['seconds']} s, ${row['cost_usd']}")
        if keep:
            for i, p in enumerate(c for c in out["content"] if c["type"] == "image"):
                (keep / f"printed-{i}.jpg").write_bytes(base64.b64decode(p["data"]))

        out = await run({"code": OUTSIDE})
        check("the machine with our image still has no way out", "NO WAY OUT" in out["content"][0]["text"] and "REACHED" not in out["content"][0]["text"])
    print(f"\n{sum(RESULTS)} of {len(RESULTS)} passed")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
