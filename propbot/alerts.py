"""Admin alerts. Admin chat only, once per kind (and key) per run. Never in the channel."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .db import DB

log = logging.getLogger("propbot.alerts")

KINDS = {
    "dataset_refresh_failed", "ura_token_failed", "web_budget_reached", "domain_cooldown",
    "discovery_fallback", "curation_fallback", "rules_page_unreadable", "rules_changed",
    "publishing_paused", "run_failed", "quota_short", "lock_stale", "info",
}


class Alerts:
    def __init__(self, db: DB, admin_chat_id: str = "", telegram=None, run_id: str = "", vault=None):
        self.db = db
        self.vault = vault
        self.admin_chat_id = admin_chat_id
        self.telegram = telegram
        self.run_id = run_id
        self._sent: set[tuple[str, str]] = set()

    def set_run(self, run_id: str) -> None:
        self.run_id = run_id
        self._sent.clear()

    def __call__(self, kind: str, key: str, text: str) -> bool:
        return self.send(kind, text, key=key)

    def send(self, kind: str, text: str, *, key: str = "") -> bool:
        """Send once per (kind, key) per run. Returns True when delivered to Telegram."""
        if (kind, key) in self._sent:
            return False
        self._sent.add((kind, key))
        delivered = False
        message = f"propbot admin: {kind.replace('_', ' ')}\n{text}"
        if self.telegram is not None and self.admin_chat_id:
            try:
                self.telegram.send_message(self.admin_chat_id, _plain(message), silent=False)
                delivered = True
            except Exception as exc:  # alerts must never crash a run
                log.warning("admin alert failed: %s", exc)
        log.warning("%s", message)
        if self.vault is not None:
            self.vault.activity("alert", f"{kind.replace('_', ' ')}: {text[:300]}", run_id=self.run_id,
                                sent="yes" if delivered else "no")
        self.db.insert("alerts_log", {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                      "run_id": self.run_id, "kind": kind, "key": key,
                                      "text": text[:2000], "sent": delivered})
        return delivered


def _plain(text: str) -> str:
    import html
    return html.escape(text, quote=False)[:4000]
