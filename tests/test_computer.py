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
        self.files = types.SimpleNamespace(write=self._nothing, list=self._list, read=self._read)
        self.cmds, self.logs, self.live, self.killed_pids = [], {}, set(), []
        self.commands = types.SimpleNamespace(run=self._cmd, list=self._procs, kill=self._kill)
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

    async def _cmd(self, cmd, background=False, timeout=None, envs=None, user=None):
        self.cmds.append((cmd, background, envs, user))
        if "wc -l" in cmd:
            return types.SimpleNamespace(stdout=str(len(self.logs)), pid=1)
        if background and "ODYSSEUS_JOB" in (envs or {}):
            pid = 1000 + len(self.logs)
            self.logs[f"{sandbox.JOB_DIR}/job-{len(self.logs) + 1}.log"] = "step 1\n"
            self.live.add(pid)
            return types.SimpleNamespace(pid=pid)
        return types.SimpleNamespace(stdout="", pid=2)

    async def _procs(self):
        return [types.SimpleNamespace(pid=p) for p in self.live]

    async def _kill(self, pid):
        self.killed_pids.append(pid)
        self.live.discard(pid)

    async def _read(self, path, format=None):
        if path in self.logs:
            return self.logs[path]
        if str(path).startswith(sandbox.JOB_DIR):
            raise RuntimeError("no such file")
        return b""

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


async def test_a_job_goes_on_after_the_run_that_started_it_and_is_looked_in_on(lib):
    c = computer(lib)
    job = await c.start_job("python fetch.py; echo \"it's done\"", 15)
    box = Box.made[0]
    line, background, envs, user = [x for x in box.cmds if x[1] and "ODYSSEUS_JOB" in (x[2] or {})][0]
    assert "fetch.py" not in line and envs == {"ODYSSEUS_JOB": "python fetch.py; echo \"it's done\""}, "the command is a value, never pasted into the shell's line"
    assert user == sandbox.JOB_USER and sandbox.WORK_DIR in line and job == "1:1000"
    assert box.timeouts[-1] >= 15 * 60 - 5, "the machine is kept awake for as long as the job was given"
    assert await c.job(job) == ("running", "step 1\n")
    box.logs[f"{sandbox.JOB_DIR}/job-1.log"] += "step 2\n[finished with exit code 0]\n"
    box.live.clear()
    state, tail = await c.job(job)
    assert state == "finished" and "exit code 0" in tail
    assert (await c.job("9:9999"))[0] == "unknown" and (await c.job("not an id"))[0] == "unknown"


async def test_a_job_can_be_stopped_and_one_that_died_is_not_called_running(lib):
    c = computer(lib)
    job = await c.start_job("sleep 300", 5)
    assert (await c.job(job, stop=True))[0] == "stopped" and Box.made[0].killed_pids == [1000]
    other = await c.start_job("sleep 300", 5)
    Box.made[0].live.clear()                    # it ended without writing its last line: killed, or the machine was restarted
    assert (await c.job(other))[0] == "stopped"


async def test_a_new_computer_warms_the_page_picturing_program_once(lib):
    c = computer(lib)
    await c.run("print(1)", [], 120)
    await c.run("print(2)", [], 120)
    warm = [x for x in Box.made[0].cmds if x[0] == sandbox.WARM_CMD]
    assert len(warm) == 1 and warm[0][1] is True, "once, when the machine is made, and in the background"


async def test_the_job_tools_exist_only_on_a_computer_that_stays(lib, tmp_path):
    from odysseus.journal import Journal, Ledger
    journal = Journal(tmp_path / "j.jsonl")
    started = []
    runner = sandbox.CodeRunner(computer(lib), journal, Ledger(journal), 3.0)
    runner.on_job = lambda job, minutes: started.append((job, minutes))
    h = runner.handlers(types.SimpleNamespace())
    assert {"job_start", "job_check"} <= set(h) and {"job_start", "job_check"} <= set(sandbox.schemas(True, True))
    out = await h["job_start"]({"command": "python long.py", "minutes": 500})
    assert "Started as job 1:1000" in out["content"][0]["text"] and started == [("1:1000", sandbox.JOB_MOST_MIN)], "its time is capped, and code is told to look in on it"
    assert (await h["job_start"]({"command": ""})).get("is_error")
    checked = await h["job_check"]({"id": "1:1000"})
    assert "is running" in checked["content"][0]["text"]
    assert (await h["job_check"]({"id": "7:7"})).get("is_error")
    thrown_away = sandbox.CodeRunner(sandbox.E2B("key", "template"), journal, Ledger(journal), 3.0).handlers(types.SimpleNamespace())
    assert "job_start" not in thrown_away and "job_start" not in sandbox.schemas(True, False)
