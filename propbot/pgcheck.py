"""PropertyGuru / CommercialGuru listings, opened through the real Chrome on the NAS (tools/pg_verify_server.py).

Those sites answer every plain or headless request with a Cloudflare challenge, so the bot never fetches them
itself: it asks the verifier sidecar, which opens the page in a Chrome the owner cleared by hand. A listing
appears on the website only when its page was opened within the last day, still showed a price, and the page
title matches the listing's name. A challenge stops the run; nothing here works around one.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, timedelta

import httpx

from . import tracker
from .db import DB
from .web import host_of

log = logging.getLogger("propbot.pgcheck")

HOSTS = ("propertyguru.com.sg", "commercialguru.com.sg")
VERIFY_URL = os.environ.get("PG_VERIFY_URL", "http://pg-browser:8080")
MAX_AGE_DAYS = 1        # a card is shown only if its page was opened today or yesterday
KINDS = ("hdb_resale", "condo_resale", "condo_new_launch", "ec", "bto", "shophouse", "hdb_shop", "coffeeshop", "strata_commercial")


def name_matches(name: str, title: str) -> bool:
    """The page is the listing we think it is: every number in the name and most of its words are in the page title."""
    toks = re.findall(r"[a-z0-9]+", re.sub(r"\bblock\b", "", name.lower()))
    t = title.lower()
    return bool(toks) and all(x in t for x in toks if x.isdigit()) and sum(x in t for x in toks) >= 0.6 * len(toks)


def verified_keys(db: DB, today: date) -> set[str]:
    since = (today - timedelta(days=MAX_AGE_DAYS)).isoformat()
    return {r["key"] for r in db.all("SELECT key FROM pg_check WHERE status='listed' AND name_ok=1 AND checked_on>=?", [since])}


def ask(client: httpx.Client, url: str) -> dict:
    r = client.get(f"{VERIFY_URL}/check", params={"url": url}, timeout=90)
    r.raise_for_status()
    return r.json()


def run(db: DB, today: date, limit: int = 25, client=None) -> dict:
    """Open up to `limit` PropertyGuru listings not checked today (never-checked first, then oldest check)."""
    client = client or httpx.Client()
    rows = [dict(r) for k in KINDS for r in tracker.active(db, k, today) if host_of(r["url"]).endswith(HOSTS)]
    checked = {r["key"]: r["checked_on"] for r in db.all("SELECT key, checked_on FROM pg_check")}
    todo = sorted((r for r in rows if checked.get(r["key"], "") < today.isoformat()), key=lambda r: checked.get(r["key"], ""))[:limit]
    n = {"listed": 0, "gone": 0, "unknown": 0, "blocked": 0}
    for r in todo:
        try:
            out = ask(client, r["url"])
        except Exception as exc:                  # verifier down: stop, retry next hour
            log.info("verifier unavailable: %s", exc)
            n["unknown"] += 1
            break
        status = out.get("status", "unknown")
        n[status] = n.get(status, 0) + 1
        if status == "blocked":
            break
        ok = status == "listed" and name_matches(r["name"], out.get("title", ""))
        db.upsert("pg_check", {"key": r["key"], "checked_on": today.isoformat(), "status": status,
                               "price": out.get("price"), "name_ok": int(ok), "title": out.get("title", "")[:200]}, "key")
        if status == "gone":
            tracker.mark_gone(db, r["key"], today, "page")
        elif ok:
            tracker.apply_checks(db, [{"url": r["url"], "status": "listed", "price_now": out.get("price")}], today)
            if out.get("image"):
                db.upsert("listing_image", {"key": r["key"], "image": out["image"], "checked_on": today.isoformat()}, "key")
    return n
