import json

import httpx
import pytest

from propbot.telegram import PublishPaused, TOMBSTONE_TEXT, TelegramClient, esc


def client(settings, limiter, handler):
    return TelegramClient("123:ABC", settings, limiter, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_escape():
    assert esc("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_send_paced_and_silent(settings, limiter, clock):
    sent = []

    def handler(req):
        body = json.loads(req.content)
        sent.append(body)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})

    tg = client(settings, limiter, handler)
    t0 = clock.now()
    ids = [tg.send_message("@c", f"m{i}", link_url="https://edgeprop.sg/x") for i in range(3)]
    assert ids == [1, 2, 3]
    assert clock.now() - t0 >= 7.0
    assert sent[0]["disable_notification"] is True
    assert sent[0]["parse_mode"] == "HTML"
    assert sent[0]["link_preview_options"] == {"url": "https://edgeprop.sg/x", "prefer_large_media": True,
                                               "show_above_text": True}


def test_429_retry_after_honoured(settings, limiter, clock):
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, json={"ok": False, "error_code": 429, "description": "Too Many Requests",
                                             "parameters": {"retry_after": 12}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    tg = client(settings, limiter, handler)
    assert tg.send_message("@c", "x") == 5
    assert any(12.5 <= s <= 15 for s in clock.slept)


def test_long_retry_after_pauses_publishing(settings, limiter):
    def handler(req):
        return httpx.Response(429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 3600}})

    with pytest.raises(PublishPaused):
        client(settings, limiter, handler).send_message("@c", "x")


def test_message_length_limit(settings, limiter):
    tg = client(settings, limiter, lambda r: httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}))
    with pytest.raises(ValueError):
        tg.send_message("@c", "x" * 4097)


def test_delete_batches_and_tombstone_fallback(settings, limiter):
    calls = []

    def handler(req):
        method = req.url.path.rsplit("/", 1)[1]
        body = json.loads(req.content)
        calls.append((method, body))
        if method == "deleteMessage" and body["message_id"] == 999:
            return httpx.Response(400, json={"ok": False, "error_code": 400,
                                             "description": "message can't be deleted"})
        return httpx.Response(200, json={"ok": True, "result": True})

    tg = client(settings, limiter, handler)
    ids = list(range(1, 151))
    out = tg.delete_messages("@c", ids + [999], old_ids={999})
    batch_calls = [c for c in calls if c[0] == "deleteMessages"]
    assert [len(c[1]["message_ids"]) for c in batch_calls] == [100, 50]
    assert out.tombstoned == [999]
    edit = [c for c in calls if c[0] == "editMessageText"][0]
    assert edit[1]["text"] == TOMBSTONE_TEXT
