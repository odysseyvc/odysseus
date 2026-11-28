"""A machine kept between one person's runs: reused when the next run follows closely, never by two runs at once,
its folders emptied before each run, and destroyed when its time is up or a run goes wrong.

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

    def __init__(self):
        self.ran, self.killed, self.timeouts, self.fail, self.gone = [], False, [], False, False
        self.files = types.SimpleNamespace(write=self._write, list=self._list, read=self._read)
        Box.made.append(self)

    asked: list[dict] = []

    @classmethod
    async def create(cls, **kw):
        cls.asked.append(kw)
        return cls()

    async def set_timeout(self, seconds):
        if self.gone:
            raise RuntimeError("no such machine")
        self.timeouts.append(seconds)

    async def run_code(self, code, timeout=None):
        self.ran.append(code)
        if self.fail and code not in (sandbox.CLEAR_CODE,):
            raise RuntimeError("the machine stopped answering")
        await asyncio.sleep(0)
        return types.SimpleNamespace(error=None, text="", logs=types.SimpleNamespace(stdout=["ok\n"], stderr=[]))

    async def _write(self, path, data): ...
    async def _list(self, path): return []
    async def _read(self, path, format=None): return b""

    async def kill(self):
        self.killed = True


@pytest.fixture
def e2b(monkeypatch):
    Box.made = []
    monkeypatch.setitem(sys.modules, "e2b_code_interpreter", types.SimpleNamespace(AsyncSandbox=Box))
    now = types.SimpleNamespace(t=1000.0)
    return sandbox.E2B("key", "template", clock=lambda: now.t), now


async def test_the_next_run_uses_the_machine_already_running_with_its_folders_emptied(e2b):
    backend, now = e2b
    first = await backend.run("print(1)", [("a.txt", b"x")], 120)
    assert len(Box.made) == 1 and not Box.made[0].killed and first.idle == sandbox.KEEP_S, "kept, and the keeping is counted as paid for"
    now.t += 30
    second = await backend.run("print(2)", [], 120)
    assert len(Box.made) == 1 and 29 <= second.idle <= 30, "used again 30 seconds on: only the extra 30 seconds of keeping are paid for, not 90 again"
    assert Box.made[0].ran == [sandbox.CLEAR_CODE, "print(1)", sandbox.CLEAR_CODE, "print(2)"], "emptied before each run"
    assert Box.made[0].timeouts[-1] == sandbox.KEEP_S, "its life is cut back to the keeping time after each run"
    await backend.close()
    assert Box.made[0].killed


async def test_a_machine_past_its_time_or_already_gone_is_not_used(e2b):
    backend, now = e2b
    await backend.run("print(1)", [], 120)
    now.t += sandbox.KEEP_S + 1
    await backend.run("print(2)", [], 120)
    assert len(Box.made) == 2 and Box.made[0].killed
    Box.made[1].gone = True
    await backend.run("print(3)", [], 120)
    assert len(Box.made) == 3, "the company had destroyed it: a new one, and no error"


async def test_two_runs_at_once_never_share_a_machine(e2b):
    backend, now = e2b
    await backend.run("print(0)", [], 120)
    await asyncio.gather(backend.run("print(1)", [], 120), backend.run("print(2)", [], 120))
    assert len(Box.made) == 2, "one took the machine standing ready; the other got its own"
    assert sum(1 for b in Box.made if not b.killed) == 1, "and only one is kept afterwards"


async def test_a_run_that_goes_wrong_ends_its_machine(e2b):
    backend, now = e2b
    await backend.run("print(1)", [], 120)
    Box.made[0].fail = True
    with pytest.raises(RuntimeError):
        await backend.run("print(2)", [], 120)
    assert Box.made[0].killed
    await backend.run("print(3)", [], 120)
    assert len(Box.made) == 2


async def test_keeping_can_be_switched_off(monkeypatch):
    Box.made = []
    monkeypatch.setitem(sys.modules, "e2b_code_interpreter", types.SimpleNamespace(AsyncSandbox=Box))
    backend = sandbox.E2B("key", "template", keep_s=0)
    got = await backend.run("print(1)", [], 120)
    assert Box.made[0].killed and got.idle == 0.0


async def test_a_machine_is_made_with_no_way_out_and_no_way_in(e2b):
    backend, now = e2b
    Box.asked.clear()
    await backend.run("print(1)", [], 120)
    assert Box.asked[0]["allow_internet_access"] is False and Box.asked[0]["network"] == {"allow_public_traffic": False}
