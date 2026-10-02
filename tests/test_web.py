import httpx
import pytest

from propbot.web import FetchRefused, PoliteFetcher


def make(settings, db, limiter, handler, alerts=None):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return PoliteFetcher(settings, db, limiter, client=client,
                         alert=(lambda k, key, t: alerts.append((k, key, t))) if alerts is not None else None)


def test_never_fetch_refused_before_network(settings, db, limiter):
    calls = []
    f = make(settings, db, limiter, lambda r: calls.append(r) or httpx.Response(200))
    for url in ["https://www.99.co/x", "https://srx.com.sg/a", "https://reddit.com/r/x"]:
        with pytest.raises(FetchRefused):
            f.fetch(url)
    with pytest.raises(FetchRefused):
        f.fetch("https://unknown-portal.example/x")
    with pytest.raises(FetchRefused):
        f.fetch("ftp://edgeprop.sg/x")
    assert calls == []


def test_robots_obeyed_and_read_once_a_day(settings, db, limiter):
    seen = []

    def handler(req):
        seen.append(req.url.path)
        assert req.headers["user-agent"].startswith("propbot/1.0")
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, text="<html>ok</html>", headers={"etag": '"v1"'})

    f = make(settings, db, limiter, handler)
    with pytest.raises(FetchRefused) as exc:
        f.fetch("https://www.edgeprop.sg/private/1")
    assert "robots" in str(exc.value)
    r = f.fetch("https://www.edgeprop.sg/listing/1")
    assert r.status == 200 and not r.from_cache
    f.fetch("https://www.edgeprop.sg/listing/2")
    assert seen.count("/robots.txt") == 1


def test_cache_and_conditional_get(settings, db, limiter, clock):
    headers_seen = []

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        headers_seen.append(dict(req.headers))
        if req.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, text="page", headers={"etag": '"v1"'})

    f = make(settings, db, limiter, handler)
    first = f.fetch("https://www.straitstimes.com/a")
    again = f.fetch("https://www.straitstimes.com/a")
    assert again.from_cache and again.text == "page"
    clock.advance(8 * 86400)
    third = f.fetch("https://www.straitstimes.com/a")
    assert third.from_cache and third.text == "page"
    assert headers_seen[-1].get("if-none-match") == '"v1"'
    assert first.text == "page"


def test_429_puts_domain_on_cooldown_and_alerts_once(settings, db, limiter):
    alerts = []

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        return httpx.Response(429)

    f = make(settings, db, limiter, handler, alerts)
    with pytest.raises(FetchRefused):
        f.fetch("https://www.propertyguru.com.sg/a")
    with pytest.raises(FetchRefused) as exc:
        f.fetch("https://www.propertyguru.com.sg/b")
    assert "cooldown" in str(exc.value)
    assert len(alerts) == 1 and alerts[0][0] == "domain_cooldown"


def test_server_errors_retry_with_backoff_then_give_up(settings, db, limiter, clock):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(503)

    f = make(settings, db, limiter, handler, [])
    with pytest.raises(FetchRefused):
        f.fetch("https://www.era.com.sg/a")
    assert 30 in clock.slept and 120 in clock.slept


def test_robots_5xx_means_no_fetch(settings, db, limiter):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(500)
        raise AssertionError("page must not be fetched")

    f = make(settings, db, limiter, handler, [])
    with pytest.raises(FetchRefused):
        f.fetch("https://www.huttons.com.sg/a")


def test_budget_refusal(settings, db, limiter):
    settings.limits.web.max_requests_per_day = 1
    from propbot.ratelimit import specs_from_settings
    limiter.specs = specs_from_settings(settings)

    def handler(req):
        return httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(200, text="x")

    f = make(settings, db, limiter, handler, [])
    with pytest.raises(FetchRefused) as exc:
        f.fetch("https://www.savills.com.sg/a")
    assert "budget" in str(exc.value)
