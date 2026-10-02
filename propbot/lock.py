"""Run lock with stale detection, so two runs never overlap."""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path


class LockHeld(Exception):
    pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:              # Windows raises WinError 87 for a pid that no longer exists
        return False
    return True


class RunLock:
    """File lock in data/. A lock is stale when its process is gone or it is older than max_age."""

    def __init__(self, path: str | Path, max_age_seconds: float, clock=time.time):
        self.path = Path(path)
        self.max_age = max_age_seconds
        self.clock = clock
        self.held = False
        self.stale_taken_over: dict | None = None

    def read(self) -> dict | None:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return None

    def is_stale(self, info: dict | None) -> bool:
        if info is None:
            return True
        same_host = info.get("host") == socket.gethostname()
        if same_host and not _pid_alive(int(info.get("pid", 0))):
            return True
        return self.clock() - float(info.get("started_at", 0)) > self.max_age

    def acquire(self, label: str = "run") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"pid": os.getpid(), "host": socket.gethostname(),
                              "started_at": self.clock(), "label": label})
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                info = self.read()
                if self.is_stale(info):
                    self.stale_taken_over = info
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                raise LockHeld(f"another {info.get('label', 'run')} is running (pid {info.get('pid')})")
            with os.fdopen(fd, "w") as fh:
                fh.write(payload)
            self.held = True
            return
        raise LockHeld("could not take the run lock")

    def release(self) -> None:
        if self.held:
            info = self.read()
            if info and info.get("pid") == os.getpid():
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
            self.held = False

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
