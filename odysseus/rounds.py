"""Round maths: what a financing does to who owns how much. Worked out by code, never by the model.

A partner asks the same sums about every round: what share does the new money buy, how far is everyone else diluted,
what do the notes convert into, what does it cost to keep our share. A language model gets such sums wrong often
enough to matter, and says the wrong figure in the same voice as the right one. So the model's part is to read the
numbers off the term sheet or the spreadsheet and hand them over; the arithmetic is here, the same every time.

The conventions, said in every answer because a term sheet may use others:

  the pre-money valuation   covers the company as it stands just before the new money: the shares in issue, the notes
                            and SAFEs once converted, and any top-up of the option pool. That is the usual term-sheet
                            reading. It means conversion and the top-up dilute the existing holders and not the new
                            investors.
  a post-money SAFE         with a cap converts into amount / cap of the company, measured after all the SAFEs and
                            notes have converted and before the new money and the pool top-up.
  a pre-money SAFE or note  with a cap converts at cap / the shares in issue before any conversion.
  a discount                converts at the round's price less the discount. With both a cap and a discount, the holder
                            gets whichever gives more shares.
  the option pool           is topped up before the round to the stated share of the company after the round.

What is not handled: liquidation preferences, participation, anti-dilution, interest on a note (give the amount with
interest already added), warrants, and more than one class of new share. The answer says so.
"""
from __future__ import annotations

from typing import Any

NOTIONAL = 10_000_000.0       # shares assumed when the holders are given as percentages
CONVENTIONS = ("How this was worked out: the pre-money valuation is taken to include the converted notes and SAFEs and any top-up of the "
               "option pool (the usual term-sheet reading), so those dilute the existing holders and not the new money. Not handled: "
               "liquidation preferences, participation, anti-dilution, interest on a note, warrants. If the term sheet says otherwise, say so "
               "to the owner; do not adjust the figures yourself.")

UNFIT = "These numbers do not fit together: the notes and the pool between them take the whole company. Check the caps and the pool target."


class RoundError(Exception):
    """Numbers that cannot be worked with, in words for the model."""


def _num(value: Any, what: str, least: float = 0.0, most: float = 1e15) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise RoundError(f"{what} must be a number.") from None
    if n != n or not least <= n <= most:
        raise RoundError(f"{what} is {value}, which is outside what a round can have.")
    return n


def _name(value: Any, fallback: str) -> str:
    return " ".join(str(value or "").split())[:60] or fallback


def work_out(a: dict[str, Any]) -> dict[str, Any]:
    """The round, from the tool's inputs. Raises RoundError with what is wrong."""
    investment = _num(a.get("investment"), "investment", 1)
    if a.get("pre_money") is None and a.get("post_money") is None:
        raise RoundError("Give pre_money or post_money.")
    pre = _num(a["pre_money"], "pre_money", 1) if a.get("pre_money") is not None else _num(a["post_money"], "post_money", 1) - investment
    if pre <= 0:
        raise RoundError("The post-money valuation is not larger than the investment.")
    notes: list[str] = []

    # The cap table before the round. Real share counts if given; otherwise percentages of a notional number of shares.
    rows = [h for h in a.get("holders") or [] if isinstance(h, dict)][:60]
    real = bool(rows) and all(h.get("shares") is not None for h in rows)
    holders: list[dict[str, Any]] = []
    if not rows:
        holders = [{"name": "Existing holders", "shares": NOTIONAL, "pool": False}]
    elif real:
        holders = [{"name": _name(h.get("name"), f"Holder {i}"), "shares": _num(h["shares"], "shares", 0), "pool": bool(h.get("pool"))} for i, h in enumerate(rows, 1)]
    else:
        if any(h.get("percent") is None for h in rows):
            raise RoundError("Give every holder `shares`, or every holder `percent`. Do not mix them.")
        total = sum(_num(h["percent"], "percent", 0, 100) for h in rows)
        if total <= 0:
            raise RoundError("The holders' percentages add up to nothing.")
        if abs(total - 100) > 0.5:
            notes.append(f"The holders' percentages add up to {total:.1f}, not 100; they were scaled to 100.")
        holders = [{"name": _name(h.get("name"), f"Holder {i}"), "shares": float(h["percent"]) / total * NOTIONAL, "pool": bool(h.get("pool"))}
                   for i, h in enumerate(rows, 1)]
    s0 = sum(h["shares"] for h in holders)
    if s0 <= 0:
        raise RoundError("The holders have no shares between them.")
    pool_now = sum(h["shares"] for h in holders if h["pool"])
    target = _num(a.get("pool_target_percent") or 0, "pool_target_percent", 0, 60) / 100

    convs = []
    for i, c in enumerate([c for c in a.get("convertibles") or [] if isinstance(c, dict)][:30], 1):
        cap = _num(c["cap"], "cap", 1) if c.get("cap") else 0.0
        disc = _num(c.get("discount_percent") or 0, "discount_percent", 0, 90) / 100
        convs.append({"name": _name(c.get("name"), f"Note {i}"), "amount": _num(c.get("amount"), "a convertible's amount", 1), "cap": cap,
                      "discount": disc, "post": c.get("post_money") is not False, "shares": 0.0, "how": ""})

    pool_new, price = 0.0, pre / s0
    for _ in range(5000):
        conv_total = sum(c["shares"] for c in convs)
        pre_fd = s0 + conv_total + pool_new
        if not pre_fd < s0 * 1e6:        # the notes and the pool keep growing: there is no cap table that satisfies them
            raise RoundError(UNFIT)
        price = pre / pre_fd
        post_fd = pre_fd + investment / price
        moved = 0.0
        for c in convs:
            ways = []
            if c["cap"]:
                ways.append((c["cap"] / ((s0 + conv_total) if c["post"] else s0), "its cap"))
            if c["discount"]:
                ways.append((price * (1 - c["discount"]), "its discount"))
            if not ways:
                ways.append((price, "the round's price"))
            at, how = min(ways)
            if at > price:           # a cap above the round's valuation does nothing: nobody converts at worse than the round
                at, how = price, "the round's price"
            new = c["amount"] / at
            moved += abs(new - c["shares"])
            c["shares"], c["how"] = new, how
        top = max(0.0, target * post_fd - pool_now) if target else 0.0
        moved += abs(top - pool_new)
        pool_new = top
        if moved < 1e-7 * s0:
            break
    else:
        raise RoundError(UNFIT)
    conv_total = sum(c["shares"] for c in convs)
    pre_fd = s0 + conv_total + pool_new
    price = pre / pre_fd
    new_shares = investment / price
    post_fd = pre_fd + new_shares

    table = [{"name": h["name"], "before": h["shares"] / s0, "after": h["shares"] / post_fd} for h in holders if not h["pool"]]
    if pool_now or pool_new:
        table.append({"name": "Option pool", "before": pool_now / s0, "after": (pool_now + pool_new) / post_fd})
    for c in convs:
        table.append({"name": f"{c['name']} (converts at {c['how']})", "before": 0.0, "after": c["shares"] / post_fd})
    table.append({"name": "New money in this round", "before": 0.0, "after": new_shares / post_fd})
    out: dict[str, Any] = {"pre_money": pre, "investment": investment, "post_money": pre + investment, "new_share": new_shares / post_fd,
                           "price": price if real else None, "table": table, "notes": notes,
                           "existing_keep": s0 / post_fd, "ours": None}

    # The firm's own position: a cheque into this round, a holding from before, or both.
    cheque = _num(a.get("our_cheque") or 0, "our_cheque", 0)
    if cheque > investment:
        raise RoundError("our_cheque is larger than the whole investment.")
    mine = 0.0
    who = _name(a.get("our_holder"), "")
    if who:
        match = [h for h in holders if h["name"].lower() == who.lower()]
        if not match:
            raise RoundError(f"our_holder is {who!r}, and no holder has that name. The names: {', '.join(h['name'] for h in holders)}.")
        mine = match[0]["shares"]
    elif a.get("our_percent_before") is not None:
        mine = _num(a["our_percent_before"], "our_percent_before", 0, 100) / 100 * s0
    if cheque or mine:
        ours: dict[str, Any] = {"cheque": cheque, "before": mine / s0, "after": (mine + cheque / price) / post_fd}
        if mine:
            ours["after_without"] = mine / post_fd
            ours["pro_rata"] = mine / pre_fd * investment                       # the usual right: our share of the round, by our holding just before it
            ours["pro_rata_share"] = mine / pre_fd
            ours["to_keep"] = max(0.0, (mine / s0 * post_fd - mine) * price)    # what holds the share exactly where it was
            if ours["to_keep"] > investment:
                ours["to_keep_note"] = "more than the whole round, so it cannot be kept in this round"
        out["ours"] = ours
    return out


def money(n: float) -> str:
    for size, mark in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if abs(n) >= size:
            return f"{n / size:.2f}".rstrip("0").rstrip(".") + mark
    return f"{n:.2f}".rstrip("0").rstrip(".")


def pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def text(r: dict[str, Any], currency: str = "") -> str:
    """The round as plain lines, every figure from work_out."""
    cur = (" ".join(str(currency).split())[:6] + " ") if currency else ""
    lines = [f"Pre-money {cur}{money(r['pre_money'])} + investment {cur}{money(r['investment'])} = post-money {cur}{money(r['post_money'])}.",
             f"The new money buys {pct(r['new_share'])} of the company. Everyone holding shares before keeps {pct(r['existing_keep'])} between them "
             f"(each is diluted by {pct(1 - r['existing_keep'])} of what they held)."]
    if r["price"] is not None:
        lines.append(f"Price per share: {cur}{r['price']:.4f}.")
    lines.append("\nWho holds what, before and after:")
    lines += [f"  {t['name']}: {pct(t['before']) if t['before'] else '-'} -> {pct(t['after'])}" for t in r["table"]]
    o = r.get("ours")
    if o:
        lines.append("\nOur position:")
        if o["cheque"]:
            lines.append(f"  A cheque of {cur}{money(o['cheque'])} into this round" + (f", on top of {pct(o['before'])} held before," if o["before"] else "")
                         + f" gives {pct(o['after'])} after the round.")
        if "after_without" in o:
            lines.append(f"  Putting in nothing, {pct(o['before'])} becomes {pct(o['after_without'])}.")
            lines.append(f"  Pro rata: {cur}{money(o['pro_rata'])}. That is {pct(o['pro_rata_share'])} of the round: our holding just before the new money, "
                         "once the notes have converted and the pool is topped up.")
            lines.append(f"  To hold exactly {pct(o['before'])} after the round: {cur}{money(o['to_keep'])}"
                         + (f" ({o['to_keep_note']})." if o.get("to_keep_note") else "."))
    lines += [f"\nNote: {n}" for n in r["notes"]]
    return "\n".join(lines) + "\n\n" + CONVENTIONS


SCHEMA = ("Work out a financing round by code: what share the new money buys, how far each holder is diluted, what notes and SAFEs convert "
          "into, what an option-pool top-up costs the holders, and what it costs the owner's firm to take or keep a share. Use it whenever "
          "the owner asks about dilution, ownership after a round, a pro rata, or what a cheque buys, and for every such figure in a memo. "
          "Read the numbers from what the owner gave you (their message, a term sheet, a cap table) and pass them in; do not do this "
          "arithmetic yourself, and do not fill in a number nobody gave: ask for it, or say which one is missing. Give your reply's "
          "figures exactly as this returns them, with its line on how they were worked out.",
          {"type": "object",
           "properties": {
               "pre_money": {"type": "number", "description": "Pre-money valuation, in full units (4000000, not 4)."},
               "post_money": {"type": "number", "description": "Post-money valuation, if that is what was given and not the pre-money."},
               "investment": {"type": "number", "description": "All the new money in this round."},
               "currency": {"type": "string", "description": "Shown beside the amounts, e.g. USD. Only if a currency was named; never assume one."},
               "holders": {"type": "array", "description": "The cap table before the round, if known: every holder with `shares`, or every "
                           "holder with `percent`. Include the unissued option pool as a holder with pool=true.",
                           "items": {"type": "object", "properties": {"name": {"type": "string"}, "shares": {"type": "number"},
                                                                      "percent": {"type": "number"}, "pool": {"type": "boolean"}}}},
               "pool_target_percent": {"type": "number", "description": "The option pool the term sheet asks for, as a percentage of the company after the round."},
               "convertibles": {"type": "array", "description": "Notes and SAFEs that convert in this round.",
                                "items": {"type": "object", "required": ["amount"],
                                          "properties": {"name": {"type": "string"}, "amount": {"type": "number", "description": "With any interest already added."},
                                                         "cap": {"type": "number"}, "discount_percent": {"type": "number"},
                                                         "post_money": {"type": "boolean", "description": "true (the default) for a post-money SAFE; false for a pre-money SAFE or a note."}}}},
               "our_cheque": {"type": "number", "description": "What the owner's firm would put into this round, if anything."},
               "our_holder": {"type": "string", "description": "The name in `holders` that is the owner's firm, if it holds shares already."},
               "our_percent_before": {"type": "number", "description": "The firm's share before the round, if no cap table was given."}},
           "required": ["investment"]})


def handler(journal: Any):
    async def round_maths(args: dict[str, Any]) -> dict[str, Any]:
        try:
            out = text(work_out(args), str(args.get("currency") or ""))
        except RoundError as e:
            journal.write("tool", name="round_maths", ok=False, error=str(e)[:80])
            return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
        journal.write("tool", name="round_maths", ok=True, convertibles=len(args.get("convertibles") or []), holders=len(args.get("holders") or []))
        return {"content": [{"type": "text", "text": out}]}
    return round_maths
