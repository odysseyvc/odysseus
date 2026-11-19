"""An approval the owner widens to a thread: what it covers, and every way it still asks."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from odysseus.apps import MAIL_REPLY, MAIL_SEND
from odysseus.gate import STANDING_MAX, STANDING_NOTE
from odysseus.journal import Journal
from odysseus.review import OUTGOING, UNSEEN, make_second_reader
from odysseus.tools import make_gate

REPLY = {"thread_id": "t-77", "to": "tara@corvidfreight.example", "body": "Thursday at three works. See you then."}


def rig(tmp_path, reply="OK", reader=True, fail=False):
    journal, clock, seen, asked = Journal(tmp_path / "j.jsonl"), SimpleNamespace(t=datetime(2026, 11, 20, 10, 0)), [], []

    async def call(model, system, user):
        seen.append(user)
        if fail:
            raise ConnectionError("down")
        return x.reply, {"input_tokens": 900, "output_tokens": 3}
    gate = make_gate(journal, lambda: clock.t, with_apps=True)
    if reader:
        gate.set_second_reader(make_second_reader(journal, call, "claude-haiku-5-5", gate.taint), OUTGOING)
    gate.taint.owner_said("tara@corvidfreight.example sam@acme-capital.example")
    x = SimpleNamespace(gate=gate, journal=journal, clock=clock, seen=seen, asked=asked, reply=reply, tap=("approve", True))

    async def asker(approval):
        asked.append(approval)
        how, thread = x.tap
        gate.resolve(approval.id, how == "approve", thread=thread)
    gate.set_asker(asker)
    gate.new_task("The owner said: Tell Tara Thursday at three works.")
    return x


async def widen(x, args=REPLY):
    assert isinstance(await x.gate.permission(MAIL_REPLY, args), PermissionResultAllow) and x.gate.guard(MAIL_REPLY, args)
    x.asked.clear(), x.seen.clear()
    x.tap = ("decline", False)      # from here on, any question is declined, so that "allowed" can only mean "not asked"


async def test_a_reply_offers_the_third_button_and_only_a_reply_and_only_with_a_reader(tmp_path):
    x = rig(tmp_path)
    await x.gate.permission(MAIL_REPLY, REPLY)
    await x.gate.permission(MAIL_SEND, {"to": "tara@corvidfreight.example", "subject": "s", "body": "b"})
    assert [a.offer_thread for a in x.asked] == [True, False]
    y = rig(tmp_path / "no-reader", reader=False)
    await y.gate.permission(MAIL_REPLY, REPLY)
    assert y.asked[0].offer_thread is False, "with nobody to read each reply again, the owner reads every one"


async def test_after_the_owner_widens_an_approval_the_next_reply_in_the_thread_goes_and_is_reported(tmp_path):
    x = rig(tmp_path)
    await widen(x)
    again = {**REPLY, "body": "One more thing: could you bring the customer references?"}
    out = await x.gate.permission(MAIL_REPLY, again)
    assert isinstance(out, PermissionResultAllow) and x.asked == [], "sent without a question"
    assert UNSEEN in x.seen[0] and "could you bring the customer references" in x.seen[0], "it was read once more, and told nobody else would"

    async def send(args):
        return {"content": [{"type": "text", "text": "Sent."}]}
    result = await x.gate.guarded(MAIL_REPLY, send)(again)
    assert [c["text"] for c in result["content"]] == ["Sent.", STANDING_NOTE], "the model is told the owner did not see this one"
    assert (await x.gate.guarded(MAIL_REPLY, send)(again))["is_error"], "one sending, as with any approval"
    assert x.gate.pop_standing() == ["reply to tara@corvidfreight.example"] and x.gate.pop_standing() == []
    assert [r["verdict"] for r in x.journal.read() if r["kind"] == "gate"][-2:] == ["standing", "blocked"]


async def test_everything_outside_what_was_widened_is_still_asked(tmp_path):
    cases = {
        "another thread": {**REPLY, "thread_id": "t-78"},
        "somebody new addressed": {**REPLY, "to": ["tara@corvidfreight.example", "sam@acme-capital.example"]},
        "a different person": {**REPLY, "to": "sam@acme-capital.example"},
        "a long reply": {**REPLY, "body": "x" * 4001},
    }
    for i, (why, args) in enumerate(cases.items()):
        x = rig(tmp_path / str(i))
        await widen(x)
        assert isinstance(await x.gate.permission(MAIL_REPLY, args), PermissionResultDeny) and len(x.asked) == 1, why
    for i, kw in enumerate(({"reply": "NOT_ASKED"}, {"reply": "It looks fine to me"}, {"fail": True})):
        x = rig(tmp_path / f"r{i}", **kw)
        x.reply = "OK"
        x.gate._clean = make_second_reader(x.journal, None, "", x.gate.taint).clean if kw.get("fail") else x.gate._clean
        await widen(x)
        x.reply = kw.get("reply", "OK")
        assert isinstance(await x.gate.permission(MAIL_REPLY, {**REPLY, "body": "We will lead the round."}), PermissionResultDeny) and len(x.asked) == 1, kw
    x = rig(tmp_path / "plain")
    x.tap = ("approve", False)
    await widen(x)
    assert isinstance(await x.gate.permission(MAIL_REPLY, {**REPLY, "body": "Again."}), PermissionResultDeny), "an ordinary Approve widens nothing"
    x = rig(tmp_path / "declined")
    x.tap = ("decline", True)
    assert isinstance(await x.gate.permission(MAIL_REPLY, REPLY), PermissionResultDeny)
    x.tap = ("decline", False)
    assert isinstance(await x.gate.permission(MAIL_REPLY, REPLY), PermissionResultDeny) and len(x.asked) == 2, "a decline widens nothing either"


async def test_it_ends_at_midnight_after_five_replies_and_when_the_owner_says_so(tmp_path):
    x = rig(tmp_path / "night")
    await widen(x)
    x.clock.t = datetime(2026, 11, 20, 23, 59)
    assert isinstance(await x.gate.permission(MAIL_REPLY, {**REPLY, "body": "Late."}), PermissionResultAllow)
    x.clock.t = datetime(2026, 11, 21, 0, 1)
    assert isinstance(await x.gate.permission(MAIL_REPLY, {**REPLY, "body": "Tomorrow."}), PermissionResultDeny) and len(x.asked) == 1
    x = rig(tmp_path / "five")
    await widen(x)
    for n in range(STANDING_MAX):
        assert isinstance(await x.gate.permission(MAIL_REPLY, {**REPLY, "body": f"Reply {n}."}), PermissionResultAllow)
    assert isinstance(await x.gate.permission(MAIL_REPLY, {**REPLY, "body": "The sixth."}), PermissionResultDeny) and len(x.asked) == 1
    x = rig(tmp_path / "cleared")
    await widen(x)
    assert x.gate.clear_standing() == 1
    assert isinstance(await x.gate.permission(MAIL_REPLY, {**REPLY, "body": "After /ask."}), PermissionResultDeny) and len(x.asked) == 1


async def test_the_owner_is_told_what_the_third_button_did_and_can_go_back(tmp_path):
    from odysseus.conversation import THREAD_APPROVED
    from test_fresh import OWNER, rig as conv_rig
    c = conv_rig(tmp_path)
    x = rig(tmp_path / "g")
    c.conv._gate = x.gate

    async def asker(approval):
        x.asked.append(approval)
    x.gate.set_asker(asker)
    import asyncio
    task = asyncio.create_task(x.gate.permission(MAIL_REPLY, REPLY))
    while not x.asked:
        await asyncio.sleep(0.01)
    assert await c.conv.handle_decision(OWNER, x.asked[0].id, True, thread=True) == THREAD_APPROVED
    assert isinstance(await task, PermissionResultAllow) and "until midnight" in THREAD_APPROVED and "/ask" in THREAD_APPROVED
    await c.conv.handle(OWNER, "/ask", c.chat)
    assert c.chat.sent[-1] == "From now on I'll show you every reply before it goes."
    await c.conv.handle(OWNER, "/ask", c.chat)
    assert c.chat.sent[-1] == "I already show you every reply before it goes."
