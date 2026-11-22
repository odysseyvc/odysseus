"""Build the machine image that code runs on when it has to make documents (sandbox.py).

  python scripts/build_sandbox.py            build it at the sandbox company, under the name below
  python scripts/build_sandbox.py --show     print the recipe as a Dockerfile, and build nothing

The company's standard image can write a spreadsheet and a Word file and nothing else, and a machine with no internet
cannot download what is missing. So the missing parts are put into an image once, here: the libraries for slide decks
and PDFs, LibreOffice to turn a deck or a document into a PDF and into pictures of its pages, a browser to print a web
page to a PDF, and fonts that are
metric-compatible with the ones Office files usually ask for. The build runs on the company's machines; nothing is
built on this one. Afterwards, set ODYSSEUS_SANDBOX_TEMPLATE to the name printed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

NAME = "odysseus-documents"
BASE = "code-interpreter-v1"            # the image sandbox.py used before; everything it had is still there
APT = ["libreoffice-impress", "libreoffice-writer", "libreoffice-calc", "poppler-utils", "git", "fontconfig", "chromium",
       "fonts-liberation2", "fonts-crosextra-carlito", "fonts-crosextra-caladea", "fonts-inter", "fonts-noto-core"]
PIP = ["python-pptx", "reportlab", "pypdf", "XlsxWriter", "markdown"]
# Open-licensed typefaces that firms' decks and memos are often set in, from Google's public collection. A template that
# names one of these is then drawn in it when the pages are looked at, and not in a stand-in. A list, to be added to.
FONTS = ["archivo", "bebasneue", "bitter", "bricolagegrotesque", "cormorantgaramond", "crimsonpro", "dmsans", "dmserifdisplay", "dmseriftext",
         "ebgaramond", "figtree", "fraunces", "geist", "geistmono", "ibmplexmono", "ibmplexsans", "ibmplexserif", "instrumentsans",
         "instrumentserif", "jetbrainsmono", "karla", "lato", "lexend", "librebaskerville", "librefranklin", "lora", "manrope", "merriweather",
         "montserrat", "newsreader", "nunito", "nunitosans", "opensans", "oswald", "outfit", "playfairdisplay", "plusjakartasans", "poppins",
         "ptsans", "ptserif", "publicsans", "raleway", "roboto", "robotomono", "robotoslab", "rubik", "sora", "sourcesans3", "sourceserif4",
         "spacegrotesk", "spectral", "urbanist", "worksans"]
FONT_CMD = ("git clone --quiet --depth 1 --filter=blob:none --sparse https://github.com/google/fonts /tmp/gf && cd /tmp/gf && "
            "git sparse-checkout set " + " ".join(f"ofl/{f}" for f in FONTS) + " && mkdir -p /usr/share/fonts/truetype/open && "
            "find ofl -name '*.ttf' -exec cp {} /usr/share/fonts/truetype/open/ ';' && cd / && rm -rf /tmp/gf && fc-cache -f && "
            "fc-list : family | sort -u | wc -l")


def recipe():
    from e2b import Template
    return (Template().from_template(BASE)
            .set_user("root")
            .apt_install(APT, no_install_recommends=True)
            .pip_install(PIP)
            .run_cmd(FONT_CMD)
            # LibreOffice writes a profile the first time it starts, which takes several seconds. Done once here.
            .run_cmd("soffice --headless --convert-to pdf --outdir /tmp /dev/null || true", user="user")
            .set_user("user"))


def main() -> int:
    from e2b import Template, default_build_logger
    for stream in (sys.stdout, sys.stderr):        # the build log draws boxes that a Windows console's own encoding cannot print
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if "--show" in sys.argv:
        print(Template.to_dockerfile(recipe()))
        return 0
    from odysseus import config
    settings = config.load()
    if not settings.e2b_api_key:
        print("No E2B_API_KEY in the settings.")
        return 1
    info = Template.build(recipe(), NAME, cpu_count=2, memory_mb=2048, on_build_logs=default_build_logger(), api_key=settings.e2b_api_key)
    print(f"\nBuilt. Set ODYSSEUS_SANDBOX_TEMPLATE={NAME}   (template id {getattr(info, 'template_id', '?')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
