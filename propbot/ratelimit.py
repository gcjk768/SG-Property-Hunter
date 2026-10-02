"""Rate limits, budgets, backoff and cooldowns (same module shape as pddbot).

Buckets: web (with a sub bucket per domain), datagov, ura, onemap, telegram, claude, analyses.
Each bucket has a minimum gap with jitter, optional rolling per minute cap, and per run and
per day caps. Days roll over at midnight Asia/Singapore. State lives in SQLite (rate_state and
requests_log); tokens and passwords are never logged. The clock is injectable for tests.
"""
from __future__ import annotations

import random
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from .config import Settings
from .db import DB


class Clock:
    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class FakeClock(Clock):
    """Test clock: sleeping advances time instantly."""

    def __init__(self, start: float = 1_790_000_000.0):
        self.t = start
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self.slept.append(seconds)
            self.t += seconds

    def advance(self, seconds: float) -> None:
        self.t += seconds


class BudgetExceeded(Exception):
    def __init__(self, bucket: str, scope: str, limit: int):
        super().__init__(f"{bucket} budget reached ({scope} limit {limit})")
        self.bucket, self.scope, self.limit = bucket, scope, limit


class CooldownActive(Exception):
    def __init__(self, bucket: str, key: str, until: float, reason: str):
        super().__init__(f"{bucket}:{key} cooling down until {until:.0f} ({reason})")
        self.bucket, self.key, self.until, self.reason = bucket, key, until, reason


@dataclass(frozen=True)
class BucketSpec:
    name: str
    min_gap: float = 0.0
    max_gap: float = 0.0          # jitter upper bound; gap is uniform(min_gap, max_gap)
    per_run: int | None = None
    per_day: int | None = None
    per_minute: int | None = None
    gap_per_key: bool = False     # True: the gap applies per key (per domain for web)


def specs_from_settings(settings: Settings) -> dict[str, BucketSpec]:
    lim = settings.limits
    api_gap = lim.official_apis.min_gap_seconds
    return {
        "web": BucketSpec("web", lim.web.per_domain_min_gap_seconds, lim.web.per_domain_max_gap_seconds,
                          per_run=lim.web.max_requests_per_run, per_day=lim.web.max_requests_per_day,
                          gap_per_key=True),
        "datagov": BucketSpec("datagov", api_gap, api_gap * 1.5,
                              per_day=lim.official_apis.datagov_requests_per_day),
        "ura": BucketSpec("ura", api_gap, api_gap * 1.5, per_day=lim.official_apis.ura_requests_per_day),
        "onemap": BucketSpec("onemap", api_gap, api_gap * 1.5,
                             per_day=lim.official_apis.onemap_requests_per_day),
        "telegram": BucketSpec("telegram", lim.telegram.min_gap_seconds, lim.telegram.min_gap_seconds + 0.5,
                               per_minute=lim.telegram.max_per_minute),
        "claude": BucketSpec("claude", per_run=lim.claude.max_calls_per_run,
                             per_day=lim.claude.max_calls_per_day),
        "analyses": BucketSpec("analyses", per_day=lim.claude.max_analyses_per_day),
    }


_SECRET_PARAM = re.compile(r"(token|key|secret|password|passwd|email|auth)", re.I)


def redact_url(url: str) -> str:
    """Drop secret looking query parameters before a URL is logged."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    q = [(k, "REDACTED" if _SECRET_PARAM.search(k) else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


class RateLimiter:
    def __init__(self, db: DB, settings: Settings, clock: Clock | None = None,
                 rng: random.Random | None = None, run_id: str = ""):
        self.db = db
        self.settings = settings
        self.clock = clock or Clock()
        self.rng = rng or random.Random()
        self.run_id = run_id
        self.tz = ZoneInfo(settings.run.timezone)
        self.specs = specs_from_settings(settings)
        self._lock = threading.RLock()
        self._minute: dict[str, deque[float]] = {}

    # ------------------------------------------------------------ helpers
    def set_run(self, run_id: str) -> None:
        self.run_id = run_id

    def day(self, ts: float | None = None) -> str:
        ts = self.clock.now() if ts is None else ts
        return datetime.fromtimestamp(ts, self.tz).strftime("%Y-%m-%d")

    def _row(self, bucket: str, key: str = "") -> dict:
        row = self.db.one("SELECT * FROM rate_state WHERE bucket=? AND key=?", (bucket, key))
        if row is None:
            self.db.execute("INSERT OR IGNORE INTO rate_state (bucket, key) VALUES (?, ?)", (bucket, key))
            row = self.db.one("SELECT * FROM rate_state WHERE bucket=? AND key=?", (bucket, key))
        data = dict(row)
        today = self.day()
        if data["day"] != today:      # midnight rollover in Asia/Singapore
            data["day"], data["day_count"] = today, 0
        if data["run_id"] != self.run_id:
            data["run_id"], data["run_count"] = self.run_id, 0
        return data

    def _save(self, bucket: str, key: str, data: dict) -> None:
        self.db.execute(
            "UPDATE rate_state SET day=?, day_count=?, run_id=?, run_count=?, last_at=?, cooldown_until=?,"
            " cooldown_reason=?, fail_streak=?, alerted_day=? WHERE bucket=? AND key=?",
            (data["day"], data["day_count"], data["run_id"], data["run_count"], data["last_at"],
             data["cooldown_until"], data["cooldown_reason"], data["fail_streak"], data["alerted_day"],
             bucket, key))

    # ------------------------------------------------------------ checks
    def remaining(self, bucket: str) -> dict[str, int | None]:
        spec = self.specs[bucket]
        row = self._row(bucket)
        return {
            "day": None if spec.per_day is None else max(0, spec.per_day - row["day_count"]),
            "run": None if spec.per_run is None else max(0, spec.per_run - row["run_count"]),
        }

    def in_cooldown(self, bucket: str, key: str = "") -> tuple[bool, float, str]:
        row = self._row(bucket, key)
        until = row["cooldown_until"]
        return (until > self.clock.now(), until, row["cooldown_reason"])

    def check(self, bucket: str, key: str = "") -> None:
        spec = self.specs[bucket]
        cooling, until, reason = self.in_cooldown(bucket, key)
        if cooling:
            raise CooldownActive(bucket, key, until, reason)
        if key:
            cooling, until, reason = self.in_cooldown(bucket, "")
            if cooling:
                raise CooldownActive(bucket, "", until, reason)
        row = self._row(bucket)
        if spec.per_day is not None and row["day_count"] >= spec.per_day:
            raise BudgetExceeded(bucket, "day", spec.per_day)
        if spec.per_run is not None and row["run_count"] >= spec.per_run:
            raise BudgetExceeded(bucket, "run", spec.per_run)

    def acquire(self, bucket: str, key: str = "") -> float:
        """Wait for the gap, check budgets, count the request. Returns seconds waited."""
        spec = self.specs[bucket]
        waited = 0.0
        with self._lock:
            self.check(bucket, key)
            gap_key = key if spec.gap_per_key else ""
            gap_row = self._row(bucket, gap_key)
            if spec.min_gap > 0 and gap_row["last_at"] > 0:
                gap = self.rng.uniform(spec.min_gap, max(spec.min_gap, spec.max_gap))
                wait = gap_row["last_at"] + gap - self.clock.now()
                if wait > 0:
                    self.clock.sleep(wait)
                    waited += wait
            if spec.per_minute:
                window = self._minute.setdefault(bucket, deque())
                while True:
                    now = self.clock.now()
                    while window and now - window[0] >= 60:
                        window.popleft()
                    if len(window) < spec.per_minute:
                        break
                    wait = 60 - (now - window[0]) + self.rng.uniform(0.1, 0.5)
                    self.clock.sleep(wait)
                    waited += wait
                window.append(self.clock.now())
            now = self.clock.now()
            total = self._row(bucket)
            total["day_count"] += 1
            total["run_count"] += 1
            if gap_key == "":
                total["last_at"] = now
            self._save(bucket, "", total)
            if gap_key:
                gap_row = self._row(bucket, gap_key)
                gap_row["last_at"] = now
                gap_row["day_count"] += 1
                gap_row["run_count"] += 1
                self._save(bucket, gap_key, gap_row)
        return waited

    # ------------------------------------------------------------ outcomes
    def success(self, bucket: str, key: str = "") -> None:
        row = self._row(bucket, key)
        if row["fail_streak"]:
            row["fail_streak"] = 0
            self._save(bucket, key, row)

    def failure(self, bucket: str, key: str = "", status: int | None = None,
                cooldown_hours: float | None = None) -> bool:
        """Record a failure. Returns True when the key is now cooling down."""
        hours = cooldown_hours if cooldown_hours is not None else self.settings.limits.web.domain_cooldown_hours
        row = self._row(bucket, key)
        row["fail_streak"] += 1
        reason = ""
        if status in (429, 403):
            reason = f"HTTP {status}"
        elif row["fail_streak"] >= 3:
            reason = "three failures in a row"
        if reason:
            row["cooldown_until"] = self.clock.now() + hours * 3600
            row["cooldown_reason"] = reason
        self._save(bucket, key, row)
        return bool(reason)

    def cooldown(self, bucket: str, key: str, seconds: float, reason: str) -> None:
        row = self._row(bucket, key)
        row["cooldown_until"] = max(row["cooldown_until"], self.clock.now() + seconds)
        row["cooldown_reason"] = reason
        self._save(bucket, key, row)

    def first_alert_today(self, bucket: str, key: str = "") -> bool:
        """True the first time per day an alert is raised for this bucket and key."""
        row = self._row(bucket, key)
        today = self.day()
        if row["alerted_day"] == today:
            return False
        row["alerted_day"] = today
        self._save(bucket, key, row)
        return True

    def log(self, bucket: str, key: str = "", *, method: str = "GET", url: str = "",
            status: int | None = None, nbytes: int = 0, duration_ms: int = 0,
            from_cache: bool = False, note: str = "") -> None:
        self.db.insert("requests_log", {
            "ts": self.clock.now(), "day": self.day(), "run_id": self.run_id, "bucket": bucket,
            "key": key, "method": method, "url": redact_url(url), "status": status, "bytes": nbytes,
            "duration_ms": duration_ms, "from_cache": from_cache, "note": note[:300]})

    # ------------------------------------------------------------ reporting
    def usage(self) -> list[dict]:
        out = []
        for name, spec in self.specs.items():
            row = self._row(name)
            out.append({"bucket": name, "today": row["day_count"], "day_limit": spec.per_day,
                        "this_run": row["run_count"], "run_limit": spec.per_run,
                        "per_minute": spec.per_minute})
        return out

    def cooldowns(self) -> list[dict]:
        now = self.clock.now()
        rows = self.db.all("SELECT bucket, key, cooldown_until, cooldown_reason FROM rate_state"
                           " WHERE cooldown_until > ?", (now,))
        return [dict(r) for r in rows]
