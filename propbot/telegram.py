"""Telegram Bot API client: send, delete, edit, with polite pacing and 429 handling.

HTML parse mode; every value placed into a message must go through esc(). Sends are paced by
the telegram bucket (3.5 second gap, rolling 17 per minute). A 429 retry_after is honoured
with jitter; when it is longer than max_retry_after_seconds the client raises PublishPaused
so the post job can resume from the first pending item later.
"""
from __future__ import annotations

import html
import random
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings
from .ratelimit import RateLimiter

API = "https://api.telegram.org"
TOMBSTONE_TEXT = "Removed. Today's items are posted below."
MAX_LEN = 4096


def esc(value: Any) -> str:
    """Escape a value for Telegram HTML. None never reaches here; render handles 'unknown'."""
    return html.escape(str(value), quote=False)


def esc_attr(value: Any) -> str:
    return html.escape(str(value), quote=True)


class TelegramError(Exception):
    def __init__(self, method: str, code: int | None, description: str):
        super().__init__(f"{method} failed ({code}): {description}")
        self.method, self.code, self.description = method, code, description


class PublishPaused(Exception):
    def __init__(self, retry_after: float):
        super().__init__(f"Telegram asked to wait {retry_after:.0f} seconds; publishing paused")
        self.retry_after = retry_after


@dataclass
class DeleteOutcome:
    deleted: list[int]
    tombstoned: list[int]
    failed: list[int]


class TelegramClient:
    def __init__(self, token: str, settings: Settings, limiter: RateLimiter,
                 client: httpx.Client | None = None, rng: random.Random | None = None):
        self.token = token
        self.cfg = settings.limits.telegram
        self.limiter = limiter
        self.clock = limiter.clock
        self.http = client or httpx.Client(timeout=40.0)
        self.rng = rng or random.Random()

    def _url(self, method: str) -> str:
        return f"{API}/bot{self.token}/{method}"

    def call(self, method: str, payload: dict | None = None, *, paced: bool = True) -> Any:
        if not self.token:
            raise TelegramError(method, None, "TELEGRAM_BOT_TOKEN is not set")
        payload = payload or {}
        for attempt in range(self.cfg.max_retries + 1):
            if paced:
                self.limiter.acquire("telegram")
            try:
                resp = self.http.post(self._url(method), json=payload)
            except httpx.HTTPError as exc:
                self.limiter.log("telegram", method, method="POST", url=f"{API}/bot<token>/{method}",
                                 status=None, note=type(exc).__name__)
                if attempt >= self.cfg.max_retries:
                    raise TelegramError(method, None, type(exc).__name__) from exc
                self.clock.sleep(min(60, 2 ** attempt) + self.rng.uniform(0, 1))
                continue
            self.limiter.log("telegram", method, method="POST", url=f"{API}/bot<token>/{method}",
                             status=resp.status_code)
            try:
                data = resp.json()
            except ValueError:
                data = {"ok": False, "description": resp.text[:200]}
            if data.get("ok"):
                return data.get("result")
            code = data.get("error_code", resp.status_code)
            if code == 429:
                retry_after = float((data.get("parameters") or {}).get("retry_after", 30))
                if retry_after > self.cfg.max_retry_after_seconds:
                    raise PublishPaused(retry_after)
                if attempt >= self.cfg.max_retries:
                    raise TelegramError(method, code, data.get("description", "Too Many Requests"))
                self.clock.sleep(retry_after + self.rng.uniform(0.5, 3.0))
                continue
            if 500 <= int(code or 0) < 600 and attempt < self.cfg.max_retries:
                self.clock.sleep(min(60, 2 ** attempt) + self.rng.uniform(0, 1))
                continue
            raise TelegramError(method, code, data.get("description", ""))
        raise TelegramError(method, None, "retries exhausted")

    # ------------------------------------------------------------ messages
    def send_message(self, chat_id: str | int, text: str, *, silent: bool = True,
                     link_url: str | None = None, reply_to: int | None = None) -> int:
        if len(text) > MAX_LEN:
            raise ValueError(f"message is {len(text)} characters; the limit is {MAX_LEN}")
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                   "disable_notification": silent}
        if link_url:
            payload["link_preview_options"] = {"url": link_url, "prefer_large_media": True,
                                               "show_above_text": True}
        else:
            payload["link_preview_options"] = {"is_disabled": True}
        if reply_to:
            payload["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        result = self.call("sendMessage", payload)
        return int(result["message_id"])

    def edit_message_text(self, chat_id: str | int, message_id: int, text: str) -> None:
        self.call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text,
                                      "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}})

    def delete_messages(self, chat_id: str | int, ids: list[int], *,
                        old_ids: set[int] | None = None) -> DeleteOutcome:
        """Delete in batches of delete_batch_size with deleteMessages.

        Messages older than 48 hours may not be deletable (deleteMessages skips them silently),
        so those are deleted one by one and, when Telegram refuses, edited into a tombstone.
        """
        old_ids = old_ids or set()
        recent = [i for i in ids if i not in old_ids]
        out = DeleteOutcome([], [], [])
        size = max(1, self.cfg.delete_batch_size)
        for start in range(0, len(recent), size):
            batch = recent[start:start + size]
            try:
                self.call("deleteMessages", {"chat_id": chat_id, "message_ids": batch})
                out.deleted.extend(batch)
            except TelegramError:
                for mid in batch:
                    self._delete_one_or_tombstone(chat_id, mid, out)
        for mid in ids:
            if mid in old_ids:
                self._delete_one_or_tombstone(chat_id, mid, out)
        return out

    def _delete_one_or_tombstone(self, chat_id: str | int, mid: int, out: DeleteOutcome) -> None:
        try:
            self.call("deleteMessage", {"chat_id": chat_id, "message_id": mid})
            out.deleted.append(mid)
            return
        except TelegramError:
            pass
        try:
            self.edit_message_text(chat_id, mid, TOMBSTONE_TEXT)
            out.tombstoned.append(mid)
        except TelegramError:
            out.failed.append(mid)

    def get_me(self) -> dict:
        return self.call("getMe", paced=False)

    def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        return self.call("getUpdates", payload, paced=False) or []
