import json
import os
import socket

import pytest

from propbot.lock import LockHeld, RunLock


def test_lock_excludes_second_holder(tmp_path):
    a = RunLock(tmp_path / "run.lock", 3600)
    a.acquire()
    with pytest.raises(LockHeld):
        RunLock(tmp_path / "run.lock", 3600).acquire()
    a.release()
    RunLock(tmp_path / "run.lock", 3600).acquire()


def test_stale_lock_dead_pid_taken_over(tmp_path):
    p = tmp_path / "run.lock"
    p.write_text(json.dumps({"pid": 999999, "host": socket.gethostname(), "started_at": 0}))
    lk = RunLock(p, 3600)
    lk.acquire()
    assert lk.stale_taken_over["pid"] == 999999


def test_stale_lock_too_old_taken_over(tmp_path):
    p = tmp_path / "run.lock"
    p.write_text(json.dumps({"pid": os.getpid(), "host": "other-host", "started_at": 0}))
    lk = RunLock(p, 60, clock=lambda: 10_000)
    lk.acquire()
    assert lk.held
