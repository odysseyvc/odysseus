"""A person's own computer: one machine that stays, found again by its id, put to sleep and not destroyed, never used by
two runs at once, and wiped only on the person's word.

The sandbox company's library is replaced by a stand-in. No machine is started.
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

from odysseus import sandbox


class Box:
    made: list["Box"] = []
    asked: list[dict] = []
    killed_ids: list[str] = []
    alive: dict[str, "Box"] = {}

    def __init__(self):
        self.sandbox_id = f"sbx-{len(Box.made) + 1}"
        self.ran, self.timeouts, self.paused, self.busy, self.overlap, self.fail = [], [], False, 0, False, False
        self.files = types.SimpleNamespace(write=self._nothing, list=self._list, read=self._nothing)
        Box.made.append(self)
        Box.alive[self.sandbox_id] = self

    @classmethod
    async def create(cls, **kw):
        cls.asked.append(kw)
        return cls()

    @classmethod
    async def connect(cls, sandbox_id, **kw):
        if sandbox_id not in cls.alive:
            raise RuntimeError("no such machine")
        cls.alive[sandbox_id].paused = False
        return cls.alive[sandbox_id]

    @classmethod
    async def kill(cls, sandbox_id, **kw):
        cls.killed_ids.append(sandbox_id)
        return cls.alive.pop(sandbox_id, None) is not None

    async def set_timeout(self, seconds):
        if self.paused:
            raise RuntimeError("asleep")
        self.timeouts.append(seconds)

    async def pause(self):
        self.paused = True

    async def run_code(self, code, timeout=None):
        self.busy += 1
        self.overlap = self.overlap or self.busy > 1
        self.ran.append(code)
        await asyncio.sleep(0.01)
        self.busy -= 1
        if self.fail and code != sandbox.COMPUTER_PREPARE:
            raise RuntimeError("the machine stopped answering")
        return types.SimpleNamespace(error=None, text="", logs=types.SimpleNamespace(stdout=["ok\n"], stderr=[]))

    async def _nothing(self, *a, **k): return b""
    async def _list(self, path): return []


@pytest.fixture
def lib(monkeypatch, tmp_path):
    Box.made, Box.asked, Box.killed_ids, Box.alive = [], [], [], {}
    monkeypatch.setitem(sys.modules, "e2b_code_interpreter", types.SimpleNamespace(AsyncSandbox=Box))
    return tmp_path / "computer.json"


def computer(state, **kw):
    return sandbox.Computer("key", "template", state_file=state, owner="7", **kw)


async def test_one_machine_stays_and_is_made_to_sleep_not_die_when_idle(lib):
    c = computer(lib)
    await c.run("print(1)", [], 120)
    await c.run("print(2)", [], 120)
    assert len(Box.made) == 1 and Box.killed_ids == []
    asked = Box.asked[0]
    assert asked["lifecycle"] == {"on_timeout": "pause"} and asked["metadata"] == {"odysseus_owner": "7"}
    assert asked["allow_internet_access"] is True and asked["network"] == {"allow_public_traffic": False}, "out, by the owner's choice; never in"
    assert Box.made[0].ran == [sandbox.COMPUTER_PREPARE, "print(1)", sandbox.COMPUTER_PREPARE, "print(2)"]
    emptied = sandbox.WORK_DIR in sandbox.COMPUTER_PREPARE and "rmtree" in sandbox.COMPUTER_PREPARE and sandbox.WORK_DIR not in sandbox.CLEAR_CODE
    assert emptied, "the in and out folders are emptied each run; the work folder never is"
    assert Box.made[0].timeouts[-1] == sandbox.COMPUTER_IDLE_S


async def test_it_is_found_again_by_its_id_after_the_program_restarts(lib):
    await computer(lib).run("print(1)", [], 120)
    c = computer(lib)                               # a new program: only the file is known
    await c.close()                                 # nothing held yet: nothing to do
    await c.run("print(2)", [], 120)
    assert len(Box.made) == 1, "the same machine, woken"
    await c.close()
    assert Box.made[0].paused and Box.killed_ids == [], "stopping the program puts it to sleep"
    await computer(lib).run("print(3)", [], 120)
    assert len(Box.made) == 1 and not Box.made[0].paused


async def test_a_machine_that_is_gone_is_replaced_and_the_new_one_remembered(lib):
    await computer(lib).run("print(1)", [], 120)
    Box.alive.clear()
    c = computer(lib)
    await c.run("print(2)", [], 120)
    assert len(Box.made) == 2 and "sbx-2" in lib.read_text()


async def test_two_runs_wait_their_turn_on_the_one_machine(lib):
    c = computer(lib)
    await asyncio.gather(c.run("print(1)", [], 120), c.run("print(2)", [], 120), c.run("print(3)", [], 120))
    assert len(Box.made) == 1 and not Box.made[0].overlap


async def test_a_run_that_goes_wrong_never_destroys_the_persons_computer(lib):
    c = computer(lib)
    await c.run("print(1)", [], 120)
    Box.made[0].fail = True
    with pytest.raises(RuntimeError):
        await c.run("print(2)", [], 120)
    Box.made[0].fail = False
    await c.run("print(3)", [], 120)
    assert len(Box.made) == 1 and Box.killed_ids == [], "found again by its id; its files are the person's"


async def test_it_is_wiped_only_when_asked_and_then_a_new_one_is_made(lib):
    c = computer(lib)
    await c.run("print(1)", [], 120)
    assert await c.wipe() is True and Box.killed_ids == ["sbx-1"] and not lib.exists()
    assert await c.wipe() is False, "nothing left to wipe"
    await c.run("print(2)", [], 120)
    assert len(Box.made) == 2


async def test_the_owner_can_have_it_without_internet_or_not_at_all(lib):
    c = computer(lib, internet=False)
    await c.run("print(1)", [], 120)
    assert Box.asked[0]["allow_internet_access"] is False
    from odysseus.config import load
    base = {"TELEGRAM_BOT_TOKEN": "t", "OWNER_TELEGRAM_ID": "7", "ANTHROPIC_API_KEY": "k", "EXA_API_KEY": "e"}
    on = load(env=base, env_file=lib.parent / "none.env")
    off = load(env={**base, "ODYSSEUS_COMPUTER": "off", "ODYSSEUS_COMPUTER_INTERNET": "off"}, env_file=lib.parent / "none.env")
    assert on.computer and on.computer_internet and not off.computer and not off.computer_internet


def test_the_model_is_told_it_is_a_computer_that_stays_and_what_never_goes_on_it():
    about = sandbox.schemas(True, lasting=True)["run_code"][0]
    assert "stays" in about and "internet access" in about and "Never put a password" in about and "only the owner asks for that" in about
    assert "throwaway" in sandbox.schemas(True)["run_code"][0], "the machine that is thrown away is still described as one"


def test_wiping_is_asked_for_in_words_and_always_put_to_the_owner(tmp_path):
    from odysseus import said
    from odysseus.gate import ASK
    from odysseus.journal import Journal
    from odysseus.tools import MANAGE_TOOL, make_gate
    assert said.command("wipe_computer") == "/computer wipe" and said.kind("wipe_computer") == said.ALWAYS_ASK
    assert make_gate(Journal(tmp_path / "j.jsonl")).decide(MANAGE_TOOL, {"what": "wipe_computer"}) == ASK
    assert "cannot be undone" in said.describe({"what": "wipe_computer"})
