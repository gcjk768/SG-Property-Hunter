"""Polite web fetcher.

Order of checks for every URL: scheme, never_fetch_domains (refused before any network call),
allowed domains, domain cooldown, page cache (served without a request when fresh),
robots.txt (read once a day per domain and obeyed), the web rate bucket (one request in
flight across the app, slow random gaps per domain, run and day budgets), then a conditional
GET. 429 or 403 or three failures in a row put the domain on cooldown and raise one admin
alert per domain per day. No logins, no proxies, no captcha services, no stealth headers.
"""
from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib import robotparser
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .db import DB
from .ratelimit import BudgetExceeded, CooldownActive, RateLimiter

_IN_FLIGHT = threading.Lock()   # one website request in flight across the whole app


class FetchRefused(Exception):
    """The URL may not be fetched (policy, robots.txt, cooldown or budget)."""

    def __init__(self, url: str, reason: str):
        super().__init__(f"{reason}: {url}")
        self.url, self.reason = url, reason


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    from_cache: bool
    path: str
    fetched_at: float
    content_type: str = ""


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def domain_matches(host: str, domain: str) -> bool:
    domain = domain.lower().lstrip(".")
    return host == domain or host.endswith("." + domain)


def is_never_fetch(url: str, never: list[str]) -> bool:
    host = host_of(url)
    return any(domain_matches(host, d) for d in never)


def is_allowed_domain(url: str, allowed: list[str]) -> bool:
    host = host_of(url)
    return any(domain_matches(host, d) for d in allowed)


class PoliteFetcher:
    def __init__(self, settings: Settings, db: DB, limiter: RateLimiter,
                 client: httpx.Client | None = None,
                 alert: Callable[[str, str, str], None] | None = None,
                 allowed_domains: list[str] | None = None):
        self.settings = settings
        self.cfg = settings.limits.web
        self.db = db
        self.limiter = limiter
        self.clock = limiter.clock
        self.alert = alert or (lambda kind, key, text: None)
        self.allowed = allowed_domains if allowed_domains is not None else settings.sources.listing_domains_allowed
        self.never = settings.sources.never_fetch_domains
        self.client = client or httpx.Client(timeout=30.0, follow_redirects=False,
                                             headers={"User-Agent": self.cfg.user_agent})
        self.cache_dir = settings.data_dir / "pages"
        self._robots: dict[str, robotparser.RobotFileParser] = {}

    # ------------------------------------------------------------ policy
    def check_policy(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise FetchRefused(url, "not an http(s) URL")
        if is_never_fetch(url, self.never):
            raise FetchRefused(url, "domain is in never_fetch_domains")
        if not is_allowed_domain(url, self.allowed):
            raise FetchRefused(url, "domain is not in the allowed list")

    # ------------------------------------------------------------ cache
    def _cached(self, url: str) -> dict | None:
        row = self.db.one("SELECT * FROM pages WHERE url=?", (url,))
        return dict(row) if row else None

    def _page_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode()).hexdigest()[:32]
        return self.cache_dir / digest[:2] / f"{digest}.html"

    def _store(self, url: str, resp: httpx.Response) -> FetchResult:
        path = self._page_path(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = resp.content
        path.write_bytes(body)
        now = self.clock.now()
        self.db.upsert("pages", {
            "url": url, "domain": host_of(url), "fetched_at": now, "status": resp.status_code,
            "etag": resp.headers.get("etag"), "last_modified": resp.headers.get("last-modified"),
            "content_type": resp.headers.get("content-type", ""), "path": str(path),
            "sha256": hashlib.sha256(body).hexdigest()}, "url")
        return FetchResult(url, resp.status_code, resp.text, False, str(path), now,
                           resp.headers.get("content-type", ""))

    def _from_cache(self, row: dict, touch: bool = False) -> FetchResult:
        text = Path(row["path"]).read_text(encoding="utf-8", errors="replace") if row["path"] and Path(row["path"]).exists() else ""
        if touch:
            self.db.execute("UPDATE pages SET fetched_at=? WHERE url=?", (self.clock.now(), row["url"]))
        return FetchResult(row["url"], row["status"] or 200, text, True, row["path"] or "",
                           row["fetched_at"], row.get("content_type") or "")

    # ------------------------------------------------------------ robots
    def robots_allows(self, url: str) -> bool:
        if not self.cfg.respect_robots_txt:   # the safety floor makes this unreachable
            raise FetchRefused(url, "respect_robots_txt must be true")
        host = host_of(url)
        today = self.limiter.day()
        parser = self._robots.get(host)
        row = self.db.one("SELECT * FROM robots WHERE domain=?", (host,))
        if parser is None or row is None or row["fetched_day"] != today:
            if row is None or row["fetched_day"] != today:
                status, body = self._fetch_robots(url)
                self.db.upsert("robots", {"domain": host, "fetched_day": today, "status": status,
                                          "body": body}, "domain")
            else:
                status, body = row["status"], row["body"] or ""
            parser = robotparser.RobotFileParser()
            if status is not None and 500 <= status:
                parser.disallow_all = True           # server error: assume full disallow
            elif status is None:
                parser.disallow_all = True           # could not read: do not fetch today
            elif status >= 400:
                parser.allow_all = True              # no robots.txt: allowed
            else:
                parser.parse(body.splitlines())
            self._robots[host] = parser
        return parser.can_fetch(self.cfg.user_agent, url)

    def _fetch_robots(self, url: str) -> tuple[int | None, str]:
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        host = host_of(url)
        self.limiter.acquire("web", host)
        started = time.monotonic()
        try:
            with _IN_FLIGHT:
                resp = self.client.get(robots_url, headers={"User-Agent": self.cfg.user_agent})
            self.limiter.log("web", host, url=robots_url, status=resp.status_code,
                             nbytes=len(resp.content), duration_ms=int((time.monotonic() - started) * 1000),
                             note="robots.txt")
            if resp.status_code in (429, 403):
                self._on_failure(host, resp.status_code, robots_url)
            return resp.status_code, resp.text if resp.status_code < 400 else ""
        except httpx.HTTPError as exc:
            self.limiter.log("web", host, url=robots_url, status=None, note=f"robots error {type(exc).__name__}")
            self._on_failure(host, None, robots_url)
            return None, ""

    # ------------------------------------------------------------ fetch
    def _on_failure(self, host: str, status: int | None, url: str) -> None:
        cooled = self.limiter.failure("web", host, status)
        if cooled and self.limiter.first_alert_today("web", host):
            self.alert("domain_cooldown", host,
                       f"{host} is on cooldown for {self.cfg.domain_cooldown_hours:g} hours "
                       f"after {('HTTP ' + str(status)) if status else 'repeated failures'} on {url}")

    def fetch(self, url: str, *, max_age_days: float | None = None) -> FetchResult:
        self.check_policy(url)
        host = host_of(url)
        cooling, until, reason = self.limiter.in_cooldown("web", host)
        if cooling:
            raise FetchRefused(url, f"domain cooldown ({reason})")
        cached = self._cached(url)
        max_age = (self.cfg.page_cache_days if max_age_days is None else max_age_days) * 86400
        if cached and self.clock.now() - cached["fetched_at"] < max_age and cached["path"]:
            self.limiter.log("web", host, url=url, status=cached["status"], from_cache=True, note="page cache")
            return self._from_cache(cached)
        try:
            if not self.robots_allows(url):
                raise FetchRefused(url, "disallowed by robots.txt")
        except (BudgetExceeded, CooldownActive) as exc:
            raise FetchRefused(url, str(exc)) from exc
        headers = {"User-Agent": self.cfg.user_agent}
        if cached:
            if cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
            if cached.get("last_modified"):
                headers["If-Modified-Since"] = cached["last_modified"]
        attempts = 1 + self.cfg.retries
        last_error = ""
        for attempt in range(attempts):
            try:
                self.limiter.acquire("web", host)
            except (BudgetExceeded, CooldownActive) as exc:
                raise FetchRefused(url, str(exc)) from exc
            started = time.monotonic()
            try:
                with _IN_FLIGHT:
                    resp = self.client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                last_error = type(exc).__name__
                self.limiter.log("web", host, url=url, status=None, note=f"error {last_error}")
                self._on_failure(host, None, url)
                if self.limiter.in_cooldown("web", host)[0] or attempt == attempts - 1:
                    break
                self._backoff(attempt)
                continue
            ms = int((time.monotonic() - started) * 1000)
            self.limiter.log("web", host, url=url, status=resp.status_code, nbytes=len(resp.content), duration_ms=ms)
            if resp.status_code == 304 and cached:
                self.limiter.success("web", host)
                return self._from_cache(cached, touch=True)
            if resp.status_code in (429, 403):
                self._on_failure(host, resp.status_code, url)
                raise FetchRefused(url, f"HTTP {resp.status_code}, domain on cooldown")
            if 300 <= resp.status_code < 400:
                self.limiter.success("web", host)
                location = resp.headers.get("location", "")
                raise FetchRefused(url, f"redirect to {location} (fetch it explicitly after policy checks)")
            if resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}"
                self._on_failure(host, resp.status_code, url)
                if self.limiter.in_cooldown("web", host)[0] or attempt == attempts - 1:
                    break
                self._backoff(attempt)
                continue
            self.limiter.success("web", host)
            return self._store(url, resp)
        raise FetchRefused(url, f"failed after retries ({last_error})")

    def _backoff(self, attempt: int) -> None:
        delays = self.cfg.backoff_seconds or [30]
        if attempt < len(delays):
            self.clock.sleep(delays[attempt])
        else:
            self.clock.sleep(delays[-1])
