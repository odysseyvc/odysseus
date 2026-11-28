"""A message picked up again after the program was stopped in the middle of it: only if it had changed nothing, only if
recent, only once.

Everything here is made up. No model is called.
"""
from __future__ import annotations

from odysseus import resume

NOW = 1_800_000_000.0


def started(*after, bg=False):
    return [{"kind": "start"}, {"kind": "task_start", "preview": "what is on tomorrow", **({"background": True} if bg else {})}, *after]


def kept(tmp_path, monkeypatch, age=30.0, **more):
    monkeypatch.setattr(resume.time, "time", lambda: NOW - age)
    path = tmp_path / "in_flight.json"
    assert resume.keep(path, "What is on tomorrow, and who is Priya meeting?", "vobiz", **more)
    return path


def test_a_message_that_had_only_read_is_picked_up_in_the_chat_it_came_from(tmp_path, monkeypatch):
    path = kept(tmp_path, monkeypatch)
    records = started({"kind": "tool", "name": "calendar_list", "ok": True}, {"kind": "tool", "name": "attio_run", "ok": True, "tool": "list-records", "action": "read"},
                      {"kind": "tool", "name": "manage", "ok": True, "what": "people"}, {"kind": "tool", "name": "granola_find", "ok": True})
    got = resume.message(path, records, NOW)
    assert got == {"text": "What is on tomorrow, and who is Priya meeting?", "via": "vobiz", "at": NOW - 30.0}
    assert resume.message(path, started(), NOW) is not None, "stopped before its first step: nothing was done at all"


def test_a_message_that_had_changed_anything_is_never_run_again_by_code(tmp_path, monkeypatch):
    path = kept(tmp_path, monkeypatch)
    for done in ({"kind": "tool", "name": "mail_send", "ok": True},
                 {"kind": "tool", "name": "attio_run", "ok": True, "tool": "create-note", "action": "change"},
                 {"kind": "tool", "name": "app_run", "ok": True, "action": "change"},
                 {"kind": "tool", "name": "remember", "ok": True},
                 {"kind": "tool", "name": "schedule", "ok": True},
                 {"kind": "tool", "name": "manage", "ok": True, "what": "invite"},
                 {"kind": "tool", "name": "something_new", "ok": True},
                 {"kind": "gate", "tool": "x", "verdict": "approved"},
                 {"kind": "gate", "tool": "x", "verdict": "standing"}):
        assert resume.message(path, started({"kind": "tool", "name": "calendar_list", "ok": True}, done), NOW) is None, done
    assert resume.message(path, started({"kind": "tool", "name": "mail_send", "ok": False}), NOW) is not None, "a send that failed did not happen"
    assert resume.message(path, started({"kind": "gate", "tool": "x", "verdict": "asked"}), NOW) is not None, "asked and not answered: nothing was done"


def test_not_an_old_message_not_twice_and_not_without_a_task(tmp_path, monkeypatch):
    assert resume.message(kept(tmp_path, monkeypatch, age=resume.RECENT_S + 60), started(), NOW) is None
    assert resume.message(kept(tmp_path, monkeypatch, again=True), started(), NOW) is None, "picked up once already"
    path = kept(tmp_path, monkeypatch)
    assert resume.message(path, [{"kind": "start"}], NOW) is None and resume.message(path, started(bg=True), NOW) is None
    resume.done(path)
    assert resume.message(path, started(), NOW) is None and not path.exists()
    path.write_text("not json", encoding="utf-8")
    assert resume.message(path, started(), NOW) is None


async def test_the_conversation_keeps_the_message_while_its_task_runs_and_only_a_typed_one(tmp_path):
    from types import SimpleNamespace
    from odysseus.conversation import Conversation
    seen = {}

    class C(Conversation):
        def __init__(self):
            self.in_flight, self.picked_up, self._running, self.after_task = tmp_path / "in_flight.json", "", 0, []
            self._workers, self._runner = [], None

        async def _run_one(self, prompt, chat, images, material, bg, runner, key, alone):
            seen[prompt] = self.in_flight.exists()
    c, chat = C(), SimpleNamespace(route="vobiz")
    await c._run("what is on tomorrow?", chat, None, None, False, 0) if "slot" in Conversation._run.__code__.co_varnames else None
    if not seen:                                    # the signature, whatever its order, is exercised by name
        import inspect
        names = list(inspect.signature(Conversation._run).parameters)[1:]
        base = {"prompt": "what is on tomorrow?", "chat": chat, "images": None, "material": None, "bg": False, "background": False, "slot": 0}
        await c._run(**{n: base[n] for n in names if n in base})
    assert seen == {"what is on tomorrow?": True} and not c.in_flight.exists(), "kept while it runs, gone when it ends"
