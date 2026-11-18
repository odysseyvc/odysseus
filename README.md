# Odysseus

A personal AI agent you message on Telegram (and, optionally, WhatsApp) and run yourself, with your own keys. It
searches the web, remembers, sends reminders, tells you when something you are waiting for happens, reads your mail and calendar, works in
other apps, browses, looks at photos, reads PDFs and listens to voice notes you send. Anything that reaches another
person or cannot be undone is shown to you first and needs a button press.

**Status: early.** It was built in a day and has 235 automated tests. The parts
that read (search, mail, calendar, public pages, files) have each run once against the
real services. The parts that act have not: no email has been sent, no event created, no
work done in a connected app, and no reminder has fired at its real time. Nothing has
been proven over weeks. The book in `site/content/chapters/` says for each part what was and was not tested.

## What you need

| Setting | From | Needed |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | @BotFather in Telegram | yes |
| `OWNER_TELEGRAM_ID` | your numeric Telegram ID | yes |
| `ANTHROPIC_API_KEY` | Claude Console | yes |
| `EXA_API_KEY` | exa.ai | yes |
| `COMPOSIO_API_KEY` | Composio (mail, calendar, other apps) | optional |
| `BROWSER_USE_API_KEY` | Browser Use Cloud (browser) | optional |
| `MISTRAL_API_KEY` | Mistral (reading documents, listening to voice notes) | optional |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` | Langfuse (a readable record of every task) | optional |

Python 3.13.

## Run it

macOS and Linux:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # then fill it in
python -m odysseus
```

Windows (PowerShell):

```powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env    # then fill it in
python -m odysseus
```

Message your bot in Telegram. Only the account in `OWNER_TELEGRAM_ID` gets replies.
The commands were run on Windows; the macOS and Linux forms are the standard
equivalents and have not been run by the author.

## Put it on a server

`Dockerfile` and `fly.toml` deploy it to Fly.io with a persistent disk. The full guide,
including what went wrong, is `site/content/deploy.html` (built into the site as
`deploy.html`). Two things to know first: a Fly.io account with no payment card stops
every machine after five minutes, and you must set `ODYSSEUS_TZ` to your time zone or
reminders fire in UTC.

## Know before you use it

- It spends your money with each vendor. Limits per task and per day are in `.env`.
- The browser hands a whole task to a third-party browsing agent. By default a browser
  run signed in as you starts without asking. Set `ODYSSEUS_BROWSER_ASK_SIGNED_IN=1` to
  be asked first.
- Your messages, mail and files pass through the vendors whose keys you supply.
- With Langfuse keys set, your messages and the replies are also recorded there. Keys are removed, and the text of mail,
  calendar and app results is replaced by its length unless you set `LANGFUSE_FULL_CONTENT=1`.

## Layout

- `odysseus/` the program
- `tests/` automated tests: `pip install -r requirements-dev.txt`, then `python -m pytest -q`
- `scripts/live_*.py` scripted runs against the real services (these cost money)
- `evals/` the evaluation: the real agent against a simulated mailbox, calendar and web in which time passes, other people
  act and tools fail. Every errand is run as written, reworded, and under failure, and every run is held to a set of
  properties that must always hold. `python -m evals.run` (uses the real model; about a cent a run, about $5.50 in all)
- `site/` the product site and a textbook on how it is built:
  `python site/build.py` writes `site/dist`

## Licence

MIT. See `LICENSE`.
