# Odysseus

A personal AI agent you message on Telegram (and, optionally, WhatsApp) and run yourself, with your own keys. It
searches the web, remembers, sends reminders, tells you when something you are waiting for happens, reads your mail and calendar, works in
other apps, browses, looks at photos, reads PDFs and listens to voice notes you send. Anything that reaches another
person or cannot be undone is shown to you first and needs a button press.

**Status: in development, used by one person.** It has 648 automated tests. Reading, filing into a customer database,
reading pitch decks and connecting services from the chat have run for real in its owner's chat. Much else has run
once, often on an invented example. Sending mail has run only in tests. It has never run on a server for more than a
few minutes, and nothing has been proven over weeks. The book's closing chapter
(`site/content/chapters/what-is-built.html`) says for each part what was and was not tested.

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
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`, `OWNER_WHATSAPP` | Meta's WhatsApp Cloud API (a second channel; needs a public HTTPS address) | optional |
| `ODYSSEUS_DATA_DIR` | where the journal, memory and conversation are kept (default `var`). They hold mail text in the clear: do not put this inside a folder that syncs to a cloud drive | optional |

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

Message your bot in Telegram. Only the account in `OWNER_TELEGRAM_ID` gets replies, until you invite someone.

To let a colleague in, send `/invite` to the bot and pass on the link it gives you. They open it, tap Start, and sign in
to their own mail and calendar from two buttons. Each person gets a folder, memory, sign-ins and daily limit of their
own; you hold the keys, pay for everyone, and could read what is stored. `/people` lists who is here and `/remove`
stops someone's assistant. They connect Attio with `/connect attio`. On a server with `ODYSSEUS_PUBLIC_URL` set they
approve and see a page that says Connected. Without it, their browser shows a page that does not load, and they send
its address back in the chat. Someone who has been silent for fifteen minutes keeps everything and gives back about
200 MB of memory until their next message, which then takes a few seconds longer.
The commands were run on Windows; the macOS and Linux forms are the standard
equivalents and have not been run by the author.

## Connect Attio (optional)

For a firm that keeps its companies, people and deals in Attio. Run
`python scripts/connect_attio.py` on the machine whose browser you will use, open the link
it prints, sign in to Attio and approve. No key is created; the sign-in is kept in the data
folder. Restart the bot. Reading Attio runs at once. Additions (a new record, a note) run and are listed in the
reply; a change to anything already there asks you first. Records have been filed in a real workspace through it; it
does not yet work on a remote server.
`python scripts/live_attio.py` checks the connection without changing anything.

## Connect Granola (optional)

For meeting notes and transcripts. Run `python scripts/connect_mcp.py granola`, open the link, sign in and approve, then
restart the bot. Reading runs at once; any change asks you first. Other services that run their own server can be added
as an entry in `odysseus/mcpapp.py`. Read from live once; nothing has been changed through it. Scheduled routines can read these services and Attio, never change them, with a fixed number of reads in one run; `python scripts/live_routine.py "..."` runs one routine once and prints what it used and what it cost. X (read-only) is prepared the same way: `python scripts/connect_mcp.py x`
says what it needs first, which is an app of your own at X's developer site.

## Put it on a server

`Dockerfile` and `fly.toml` deploy it to Fly.io with a persistent disk. The full guide,
including what went wrong, is `site/content/deploy.html` (built into the site as
`deploy.html`). Two things to know first: a Fly.io account with no payment card stops
every machine after five minutes, and you must set `ODYSSEUS_TZ` to your time zone or
reminders fire in UTC.

## Know before you use it

- It spends your money with each vendor. Limits per task and per day are in `.env`.
- The browser hands a whole task to a third-party browsing agent. A browser run signed in
  as you asks you first, showing the task; nothing asks before a click inside it. Set
  `ODYSSEUS_BROWSER_ASK_SIGNED_IN=0` to let such runs start without asking.
- Small changes that can be put back run without asking and are listed in the reply: additions to Attio, additions
  and edits in your own Google files, a routine set up from your own words alone. Sending, sharing, inviting, deleting
  and overwriting a value in Attio still ask. Set `ODYSSEUS_ASK_EVERY_CHANGE=1` to be asked before every change.
- Half an hour before a meeting with someone outside your firm, a brief is sent. For it, code sends the guest's name
  and the domain of their address to the search service, and nothing else. `ODYSSEUS_MEETING_BRIEFS=0` turns it off.
- A pitch deck sent as a file is filed in Attio and answered with a take, without a question first. The pages of a PDF
  of up to 25 pages are also looked at once, by a separate run with no tools, and its notes on charts and logos are
  given to the conversation as text. `ODYSSEUS_PAGES_MODEL` names a cheaper model for that run.
- A link you type to a PDF, or to a file in Google Drive, Slides, Docs or Dropbox, is fetched and read as a file. Only
  https, only public addresses, 20 MB at most. A link inside mail or a file is never fetched this way.
- A DocSend link you type is opened in a browser. If it asks for an email address, yours is given (the connected
  mailbox's, or `ODYSSEUS_VIEWER_EMAIL`), and the sender is told who looked. If it sends a verification email, code
  reads that one link from your mailbox and opens it in the same browser. It never accepts an agreement or tries a passcode.
- `/connect` in the chat connects a service that runs its own server: by a name it knows, by a company's name or
  website (its server is looked up in the public registry, and accepted only if published under the company's own
  domain), or by the server's address. You approve on the service's own page, in a browser on the machine the program runs on. Tools the server
  labels read-only run unasked; everything else asks. Only you can do this; the assistant cannot connect anything.
- Your messages, mail and files pass through the vendors whose keys you supply.
- With `E2B_API_KEY` set, the assistant can run Python on a rented machine that has no internet access and is destroyed
  after each run: for spreadsheets, arithmetic and charts. The code and the files it is given pass through E2B.
  Run `python scripts/build_sandbox.py` once and set `ODYSSEUS_SANDBOX_TEMPLATE=odysseus-documents`, and it can also
  make slide decks, Word files and PDFs (a designed PDF is a web page printed by a browser on that machine), and looks
  at pictures of the pages before it sends them. Send it your own
  deck or Word file and say it is your template, and later ones start from it. If your deck exists only as a PDF, it
  measures the PDF and builds a template from that, which is an imitation and says so.
- With `AGENTMAIL_API_KEY` set, `/address` gives the assistant an email address of its own. Mail from you is dealt
  with at once; mail from anyone else is put to you with one tap before any model reads it. It can send from it
  with a key that has the send permission, and asks you before every message.
- Files you send in the chat, attachments it reads in your mail and the text of DocSend documents it reads are kept in
  the data folder so that you can ask for them back. `/files` lists them and
  `/files remove` with an id deletes one.
- Tell it how you want a kind of task done ("learn how we write a memo: ...") and it keeps that as a skill, a
  `SKILL.md` file in the data folder, and follows it from then on. `/skills` lists them, `/skills show` with a name
  prints one and `/skills remove` with a name deletes it. Four come with it, for an investment firm's regular work: a
  first-draft memo, a note to a founder (a pass or a follow-up), the pack for a partner meeting, and a first reading
  of a data room. Teach it your own way under the same name and yours replaces the one that came with it.
- With Attio connected, it sends a review of the pipeline once a week, worked out by code with no model call: what is
  open, new, gone quiet, overdue, and who is owed a reply, as a message and a PDF. Ask for it at any time. It finds
  the pipeline from your workspace's own layout and tells you which stages it took as closed.
- It keeps the figures your companies report (revenue, cash, burn, headcount and a few more) by company and period,
  and points out a fall, rising burn, cash going faster than burn explains, short runway and an overdue update. Each
  morning it looks for such updates in your inbox from companies on a list named portfolio in your database; forward
  or paste one and it is recorded the same way. `/portfolio` shows what is kept.
- Ask what a round does to ownership, what notes and SAFEs convert into, or what a pro rata costs, and the sums are
  done by code with the conventions stated, not by the model.
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
