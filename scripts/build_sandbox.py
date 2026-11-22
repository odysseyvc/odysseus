"""Build the machine image that code runs on when it has to make documents (sandbox.py).

  python scripts/build_sandbox.py            build it at the sandbox company, under the name below
  python scripts/build_sandbox.py --show     print the recipe as a Dockerfile, and build nothing

The company's standard image can write a spreadsheet and a Word file and nothing else, and a machine with no internet
cannot download what is missing. So the missing parts are put into an image once, here: the libraries for slide decks
and PDFs, LibreOffice to turn a deck or a document into a PDF and into pictures of its pages, and fonts that are
metric-compatible with the ones Office files usually ask for. The build runs on the company's machines; nothing is
built on this one. Afterwards, set ODYSSEUS_SANDBOX_TEMPLATE to the name printed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

NAME = "odysseus-documents"
BASE = "code-interpreter-v1"            # the image sandbox.py used before; everything it had is still there
APT = ["libreoffice-impress", "libreoffice-writer", "libreoffice-calc", "poppler-utils",
       "fonts-liberation2", "fonts-crosextra-carlito", "fonts-crosextra-caladea", "fonts-inter", "fonts-noto-core"]
PIP = ["python-pptx", "reportlab", "pypdf", "XlsxWriter", "markdown"]


def recipe():
    from e2b import Template
    return (Template().from_template(BASE)
            .set_user("root")
            .apt_install(APT, no_install_recommends=True)
            .pip_install(PIP)
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
