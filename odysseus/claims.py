"""A reply that says a thing was done is checked against the log, by code, before the owner reads it.

The lines under a reply that begin "Done on your accounts" are written by code from what the tools did, so they can hardly
be wrong. The reply above them is the model's own, and a model can write "I've sent it" when nothing was sent: the send was
declined, a tool failed, or it lost track. Of everything an assistant gets wrong, people forgive this least, and it is the
hardest to notice, because the sentence reads exactly like a true one.

So after every task the model's own words are read by code for a first-person claim of one of four kinds of act (mail
sent, a calendar event made or changed, an event deleted, something filed in the firm's database), and each claim is
looked for in the log: a successful call of a tool that does that kind of act, in this task or earlier the same day (the
owner may have asked "did you send it?" about something that did go). A claim with nothing behind it is not removed
or rewritten. A line is added under the reply, written by code, saying what was claimed and that the log shows no such
act, and the mismatch is written to the log so that it is counted.

What this cannot do: it reads a short list of phrasings in English, so a claim worded another way passes unread, and it
knows nothing of acts outside those four kinds. It is a net with wide holes under the commonest way of falling.

Only a sentence in the first person counts: "I've sent it", "I have filed it in the database". The passive was read too at
first ("the reply was sent", "recorded in Attio"), and it gave false warnings on ordinary reporting: "the note says no
reply has been sent" is about somebody else's reply, and "the ownership recorded in Attio" is a description, not a claim.
A warning that is wrong teaches the owner to ignore the line, which is worse than a claim that slips through, so the
passive is no longer read.
"""
from __future__ import annotations

import re
from typing import Any

_NEGATED = re.compile(r"(?:\bnot|n't|\bnever|\bwithout|\bbefore|\bcannot|\bunable to|\bwould|\bshall i|\bshould i|\bwant me to|\bif|\bonce|\bwhen|\bafter)\W+(?:\w+\W+){0,3}$")
# "I sent nothing" reports that nothing was sent; "I've sent you a link" is this chat, which is not an act on an account.
_NOTHING = r"(?!\s+(?:no|nothing|none|neither|nobody|no one|zero|you)\b)"
_I = r"\bi(?:'ve| have)?\s+(?:just\s+|now\s+|already\s+)?"
KINDS: dict[str, tuple[re.Pattern, str]] = {
    "sent": (re.compile(_I + r"(?:sent|emailed|replied to|forwarded)\b" + _NOTHING), "that something was sent"),
    "event": (re.compile(r"\bi(?:'ve| have)\s+(?:just\s+|now\s+)?(?:created|booked|added|scheduled|moved|rescheduled)\b[^.\n]{0,60}\b(?:event|meeting|calendar|invite|invitation|appointment|call)\b"),
              "that a calendar event was made or changed"),
    "deleted": (re.compile(r"\bi(?:'ve| have)\s+(?:just\s+|now\s+)?(?:deleted|cancelled|canceled|removed)\b[^.\n]{0,60}\b(?:event|meeting|appointment|invite|invitation)\b"),
                "that a calendar event was deleted"),
    "filed": (re.compile(r"\bi(?:'ve| have)\s+(?:just\s+|now\s+)?(?:filed|recorded|logged|added|updated|created)\b[^.\n]{0,80}\b(?:attio|the database|the crm|the deal|the record)\b"
                         ),
              "that something was filed or changed in the firm's database"),
}
# The tools whose successful call is the act, by the name each is logged under.
ACTS = {"sent": ("mail_send", "mail_reply", "inbox_send", "mail_send_by_code"), "event": ("calendar_create", "calendar_change"), "deleted": ("calendar_delete",),
        "filed": ("attio_file",)}
_CODE_LINES = re.compile(r"\n\n(?:Memory|Done on your accounts|Browser|Schedule|Watching|Recorded|Learned|Changed): ")
SAME_DAY_S = 24 * 3600
CHECK = ("A check by the program, not by me: the reply above says {what}, and my log shows no such act in this task or earlier today. "
         "If you expected it to have happened, it has not; tell me and I will do it.")


def says(kind: str, text: str) -> bool:
    """Does the text say, in the first person and not negated, offered or conditional, that this kind of thing was done?"""
    low = text.lower()
    for _ in range(2):      # a blank to fill in, inside a draft, is not the model speaking; twice, for a blank inside a blank
        low = re.sub(r"\[[^\[\]\n]{0,300}\]", " ", low)
    low = re.sub(r"(?m)^\s*>.*$", " ", low)             # a quoted line is somebody else's words
    low = re.sub(r"\"[^\"\n]{0,400}\"", " ", low)       # so is anything inside quotation marks, a draft shown for approval among them
    return any(not _NEGATED.search(low[max(0, m.start() - 40):m.start()]) for m in KINDS[kind][0].finditer(low))


def did(kind: str, rows: list[dict[str, Any]], now: float) -> bool:
    for r in rows:
        if r.get("kind") != "tool" or r.get("ok") is not True or now - float(r.get("ts") or 0) > SAME_DAY_S:
            continue
        name = str(r.get("name") or "")
        if name in ACTS[kind] or (kind == "filed" and name == "attio_run" and r.get("action") not in (None, "read")):
            return True
    return False


def unbacked(reply: str, rows: list[dict[str, Any]], now: float) -> list[str]:
    """The kinds of act the model's own words claim and the log does not show."""
    own = _CODE_LINES.split(reply)[0]
    return [k for k in KINDS if says(k, own) and not did(k, rows, now)]


def note(kinds: list[str]) -> str:
    return CHECK.format(what=" and ".join(KINDS[k][1] for k in kinds)) if kinds else ""
