import pytest

from propbot.ratelimit import BudgetExceeded, CooldownActive, redact_url


def test_web_gap_is_per_domain_with_jitter(limiter, clock):
    limiter.acquire("web", "a.sg")
    t0 = clock.now()
    limiter.acquire("web", "a.sg")
    gap = clock.now() - t0
    assert 6 <= gap <= 12
    t1 = clock.now()
    limiter.acquire("web", "b.sg")          # other domain: no wait
    assert clock.now() == t1


def test_day_and_run_caps(limiter, settings, clock):
    settings.limits.web.max_requests_per_run = 3
    limiter.specs = __import__("propbot.ratelimit", fromlist=["x"]).specs_from_settings(settings)
    for i in range(3):
        limiter.acquire("web", f"d{i}.sg")
    with pytest.raises(BudgetExceeded) as exc:
        limiter.acquire("web", "d9.sg")
    assert exc.value.scope == "run"
    limiter.set_run("run-2")                # new run resets the run count, not the day count
    limiter.acquire("web", "d9.sg")
    assert limiter.usage()[0]["today"] == 4


def test_midnight_rollover_singapore(limiter, clock, db):
    # 23:59:30 SGT on 1 Oct 2026 is 15:59:30 UTC
    from datetime import datetime, timezone
    clock.t = datetime(2026, 10, 1, 15, 59, 30, tzinfo=timezone.utc).timestamp()
    limiter.acquire("claude")
    assert limiter.remaining("claude")["day"] == 11
    clock.advance(60)                        # now 2 Oct in Singapore
    assert limiter.day() == "2026-10-02"
    assert limiter.remaining("claude")["day"] == 12


def test_claude_caps(limiter):
    for _ in range(10):
        limiter.acquire("claude")
    with pytest.raises(BudgetExceeded):
        limiter.acquire("claude")


def test_telegram_rolling_minute(limiter, clock):
    start = clock.now()
    for _ in range(18):
        limiter.acquire("telegram")
    # 17 per minute: the 18th must start at least 60 s after the first
    assert clock.now() - start >= 60
    gaps = clock.slept
    assert all(g >= 0 for g in gaps)


def test_cooldown_on_429_and_three_failures(limiter, clock):
    assert limiter.failure("web", "x.sg", 429) is True
    with pytest.raises(CooldownActive):
        limiter.check("web", "x.sg")
    assert limiter.failure("web", "y.sg", 500) is False
    assert limiter.failure("web", "y.sg", 500) is False
    assert limiter.failure("web", "y.sg", None) is True
    clock.advance(24 * 3600 + 1)
    limiter.check("web", "x.sg")


def test_alert_once_per_day(limiter, clock):
    assert limiter.first_alert_today("web", "x.sg") is True
    assert limiter.first_alert_today("web", "x.sg") is False
    clock.advance(86400)
    assert limiter.first_alert_today("web", "x.sg") is True


def test_state_persists_in_sqlite(db, settings, clock):
    from propbot.ratelimit import RateLimiter
    a = RateLimiter(db, settings, clock=clock, run_id="r1")
    a.acquire("ura")
    b = RateLimiter(db, settings, clock=clock, run_id="r1")
    assert b.remaining("ura")["day"] == 99


def test_log_redacts_secrets(limiter, db):
    limiter.log("ura", "", url="https://x.gov.sg/api?service=a&AccessKey=abc&token=zzz")
    url = db.scalar("SELECT url FROM requests_log")
    assert "abc" not in url and "zzz" not in url and "service=a" in url
    assert redact_url("https://a.sg/p") == "https://a.sg/p"
