"""The owner's templates on the real sandbox machine (sandbox.py). No model is called.

  python scripts/live_templates.py [folder]

A made-up house deck and house memo are first made on the machine (a dark slide master, a named style, a header), as a
stand-in for files an owner would send. Then the checks: that fixed code reads each one's layouts and styles, that a deck
and a memo started from them keep the house look and lose the example content, and that a template goes onto the
machine only when the code names it. With a folder, what was made is written there. Costs about a cent.
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
from odysseus.sandbox import E2B, IN_DIR, OUT_DIR, TEMPLATE_FILE, CodeRunner  # noqa: E402
from odysseus.taint import Taint  # noqa: E402

RESULTS: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


HOUSE = f'''
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.shared import Pt as DPt, RGBColor as DRGB

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
master = prs.slide_masters[0]
fill = master.background.fill
fill.solid(); fill.fore_color.rgb = RGBColor(0x0A, 0x23, 0x42)
for shape in master.placeholders:
    for p in shape.text_frame.paragraphs:
        p.font.color.rgb = RGBColor(0xF6, 0xF4, 0xEF)
for n in range(3):
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Example slide %d of the house deck" % n
    s.placeholders[1].text = "Example text that must not survive"
prs.save("{OUT_DIR}/House deck.pptx")

d = Document()
style = d.styles.add_style("House Body", WD_STYLE_TYPE.PARAGRAPH)
style.font.size, style.font.color.rgb = DPt(12), DRGB(0x0A, 0x23, 0x42)
d.sections[0].header.paragraphs[0].text = "HOUSE HEADER, made up for a test"
d.add_heading("Example memo", 1)
for i in range(4):
    d.add_paragraph("Example paragraph that must not survive.", style="House Body")
d.save("{OUT_DIR}/House memo.docx")
print("made")
'''

USE = f'''
import subprocess
from pptx import Presentation
from docx import Document
prs = Presentation("{IN_DIR}/{TEMPLATE_FILE['deck']}")
for s in list(prs.slides._sldIdLst):
    prs.part.drop_rel(s.rId); prs.slides._sldIdLst.remove(s)
for title, body in [("Revenue tripled in a year", "A made-up figure, for a test"), ("Two risks remain", "Concentration and hiring")]:
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.placeholders[0].text, slide.placeholders[1].text = title, body
prs.save("{OUT_DIR}/update.pptx")
again = Presentation("{OUT_DIR}/update.pptx")
texts = " | ".join(sh.text_frame.text for sl in again.slides for sh in sl.shapes if sh.has_text_frame)
print("DECK slides=%d master=%s example=%s" % (len(again.slides), again.slide_masters[0].background.fill.fore_color.rgb, "Example" in texts))

d = Document("{IN_DIR}/{TEMPLATE_FILE['document']}")
body = d.element.body
for el in list(body)[:-1]:
    body.remove(el)
d.add_heading("A test memo", 1)
d.add_paragraph("A made-up paragraph in the house style.", style="House Body")
d.save("{OUT_DIR}/memo.docx")
again = Document("{OUT_DIR}/memo.docx")
print("MEMO paragraphs=%d header=%s style=%s example=%s" % (len(again.paragraphs), again.sections[0].header.paragraphs[0].text[:12],
      again.paragraphs[1].style.name, any("Example" in p.text for p in again.paragraphs)))
'''

LISTING = f'''
import os
print("IN", sorted(os.listdir("{IN_DIR}")))
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
        handlers = CodeRunner(E2B(settings.e2b_api_key, settings.sandbox_template), journal, Ledger(journal), 3.0, Taint()).handlers(shelf, send)
        run, template = handlers["run_code"], handlers["template"]

        await run({"code": HOUSE})
        by_name = {e["name"]: e for e in sent}
        check("a made-up house deck and memo exist to stand in for the owner's", {"House deck.pptx", "House memo.docx"} <= set(by_name))
        if len(by_name) < 2:
            return 1

        out = await template({"id": by_name["House deck.pptx"]["id"]})
        text = out["content"][0]["text"]
        check("fixed code reads the deck: size, example slides, layouts", not out.get("is_error") and "13.33 by 7.5" in text and "3 example slides" in text
              and '"Title and Content"' in text, " / ".join(ln.strip() for ln in text.splitlines() if ln.startswith("  "))[:150])
        out = await template({"id": by_name["House memo.docx"]["id"]})
        text = out["content"][0]["text"]
        check("fixed code reads the memo: its own style and its header", not out.get("is_error") and '"House Body"' in text and "It has a header" in text,
              next((ln for ln in text.splitlines() if ln.startswith("Styles its own")), "")[:120])
        rows = [r for r in journal.read() if r.get("name") == "template"]
        print(f"     reading the two took {sum(r['seconds'] for r in rows):.1f} s, estimated ${sum(r['cost_usd'] for r in rows):.4f}")

        out = await run({"code": LISTING})
        check("a run that does not name a template gets none", "IN []" in out["content"][0]["text"], out["content"][0]["text"].split("Printed:")[-1].strip()[:80])

        sent.clear()
        out = await run({"code": USE, "look": True})
        text = out["content"][0]["text"]
        pictures = [c for c in out["content"] if c["type"] == "image"]
        check("the trial from the templates ran", not out.get("is_error") and len(pictures) == 2 and sent == [], text.split("Printed:")[-1].strip()[:160].replace("\n", " / "))
        check("the deck kept the house master and lost the example slides", "DECK slides=2 master=0A2342 example=False" in text)
        check("the memo kept the house header and style and lost the example text", "MEMO paragraphs=2 header=HOUSE HEADER style=House Body example=False" in text)
        if keep:
            keep.mkdir(parents=True, exist_ok=True)
            for i, p in enumerate(pictures):
                (keep / f"template-look-{i}.jpg").write_bytes(base64.b64decode(p["data"]))

        await template({"clear": "deck"})
        out = await run({"code": LISTING + f"# {TEMPLATE_FILE['deck']}\n"})
        check("after clear, naming it brings nothing", "IN []" in out["content"][0]["text"] and list(shelf.templates()) == ["document"])
    print(f"\n{sum(RESULTS)} of {len(RESULTS)} passed")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
