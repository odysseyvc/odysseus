"""Running code on the real sandbox service (sandbox.py). No model is called. Each run costs a fraction of a cent.

  python scripts/live_sandbox.py

Checks what the unit tests cannot: that the library's answers have the shape the code expects, that a file goes in and
a file comes out, that the machine really has no way out to the internet, and that no machine is left running.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odysseus import config  # noqa: E402
from odysseus.files import Shelf  # noqa: E402
from odysseus.journal import Journal, Ledger  # noqa: E402
from odysseus.sandbox import E2B, IN_DIR, OUT_DIR, CodeRunner  # noqa: E402
from odysseus.taint import Taint  # noqa: E402

RESULTS: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


OUTSIDE = '''
import socket, ssl, urllib.request
# A connection to a numeric address is accepted by the service's own filter, which then passes nothing on. So what is
# tested is whether anything comes back from outside, not whether a connection opens.
def answer(port, secure):
    s = socket.create_connection(("1.1.1.1", port), timeout=8)
    if secure:
        ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        s = ctx.wrap_socket(s)
    s.sendall(b"GET / HTTP/1.0\\r\\nHost: 1.1.1.1\\r\\n\\r\\n")
    got = s.recv(60)
    if not got:
        raise ConnectionError("nothing came back")
    return got
def dns():
    q = b"\\x12\\x34\\x01\\x00\\x00\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x07example\\x03com\\x00\\x00\\x01\\x00\\x01"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(6); s.sendto(q, ("8.8.8.8", 53))
    return s.recv(100)[:12]
for name, try_ in (("a web page by name", lambda: urllib.request.urlopen("https://example.com", timeout=8).status),
                   ("a reply from a numeric address, plain", lambda: answer(80, False)),
                   ("a reply from a numeric address, encrypted", lambda: answer(443, True)),
                   ("a name looked up", lambda: socket.getaddrinfo("example.com", 443)[0][4][0]),
                   ("a name server asked directly", dns)):
    try:
        print("REACHED", name, try_())
    except Exception as e:
        print("BLOCKED", name, type(e).__name__)
'''


async def main() -> int:
    settings = config.load()
    if not settings.e2b_api_key:
        print("No E2B_API_KEY in the settings.")
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="odysseus-sandbox-"))
    journal = Journal(tmp / "journal.jsonl")
    ledger, shelf, sent = Ledger(journal), Shelf(tmp / "files"), []

    async def send(entry):
        sent.append(entry)
        return ""
    run = CodeRunner(E2B(settings.e2b_api_key), journal, ledger, 1.0, Taint()).handlers(shelf, send)["run_code"]

    began = time.monotonic()
    out = (await run({"code": "print(sum(range(1, 101)))\n17 * 3"}))["content"][0]["text"]
    first = time.monotonic() - began
    check("code runs and what it prints comes back", "5050" in out, f"{first:.1f} s from call to answer")
    check("the value of the last line comes back too", "51" in out)

    table = shelf.add(b"investor,shares\nA,1200000\nB,800000\nC,500000\n", "cap table.csv", "text/csv")
    code = (f"import pandas as pd, matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
            f"t = pd.read_csv('{IN_DIR}/cap table.csv')\nprint('TOTAL', t.shares.sum())\n"
            f"t.plot.bar(x='investor', y='shares')\nplt.savefig('{OUT_DIR}/shares.png')\nt.to_excel('{OUT_DIR}/table.xlsx', index=False)\n")
    out = (await run({"code": code, "files": [table["id"]]}))["content"][0]["text"]
    check("a kept file goes in and is read by pandas", "TOTAL 2500000" in out)
    names = sorted(e["name"] for e in sent)
    check("files the code made come back, are kept and sent", names == ["shares.png", "table.xlsx"], ", ".join(f"{e['name']} {e['size']} bytes" for e in sent))
    png = next((shelf.data(e) for e in sent if e["name"] == "shares.png"), b"") or b""
    check("the chart is a real picture", png[:4] == b"\x89PNG")

    out = (await run({"code": OUTSIDE}))["content"][0]["text"]
    print("\n".join(line for line in out.splitlines() if line.startswith(("REACHED", "BLOCKED"))))
    check("nothing comes back to the machine from the internet", "REACHED" not in out and out.count("BLOCKED") == 5)

    out = await run({"code": "1/0"})
    check("an error in the code is reported as one", bool(out.get("is_error")) and "ZeroDivisionError" in out["content"][0]["text"])

    out = (await run({"code": "import os\nprint(sorted(os.listdir('/home/user/in')), sorted(os.listdir('/home/user/out')))"}))["content"][0]["text"]
    check("a new run starts with nothing from the last", "[] []" in out)

    from e2b_code_interpreter import AsyncSandbox
    await asyncio.sleep(3)
    listed = AsyncSandbox.list(api_key=settings.e2b_api_key)
    left = await listed.next_items() if hasattr(listed, "next_items") else list(await listed)
    running = [s for s in left if "run" in str(getattr(s, "state", "running")).lower()]
    check("no machine is left running", not running, f"{len(running)} running")

    rows = [r for r in journal.read() if r.get("name") == "run_code"]
    print(f"\n{len(rows)} runs, {sum(r.get('seconds', 0) for r in rows):.1f} machine-seconds, estimated ${ledger.spent_today():.4f}")
    print("seconds per run:", [r.get("seconds") for r in rows])
    failed = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)} of {len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
