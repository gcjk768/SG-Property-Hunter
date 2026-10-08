"""Listings the weekly reports have seen: first and last seen, price changes, and which are gone.

A listing is "gone" only when a page says so (Claude opened it and it showed sold or removed) or you say so
with /propgone. A listing nobody has re-seen for STALE_DAYS drops out of the reports as stale. Nothing here
fetches a page, and a blocked page (403, captcha) is never treated as gone.
"""
from __future__ import annotations

from datetime import date, timedelta

from .db import DB

STALE_DAYS = 21
CHANGE_DAYS = 8        # a price move or a new listing counts as "this week" for this long


def record(db: DB, kind: str, items, today: date) -> None:
    """Upsert listings seen today (objects with key, name, area, url, bedrooms, tenure, lease_left, sqft, price).
    A listing already marked gone is left alone."""
    day = today.isoformat()
    for x in items:
        row = db.one("SELECT price, status FROM report_listing WHERE key=?", [x.key])
        if row is None:
            db.execute("INSERT INTO report_listing (key, kind, name, area, url, bedrooms, tenure, lease_left, sqft, price,"
                       " first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       [x.key, kind, x.name, x.area, x.url, getattr(x, "bedrooms", None), x.tenure, x.lease_left, x.sqft, x.price, day, day])
        elif row["status"] == "listed":
            moved = row["price"] != x.price
            db.execute("UPDATE report_listing SET name=?, area=?, url=?, bedrooms=?, tenure=?, lease_left=?, sqft=?, price=?,"
                       " last_seen=?, prev_price=CASE WHEN ? THEN price ELSE prev_price END,"
                       " price_changed_on=CASE WHEN ? THEN ? ELSE price_changed_on END WHERE key=?",
                       [x.name, x.area, x.url, getattr(x, "bedrooms", None), x.tenure, x.lease_left, x.sqft, x.price, day, moved, moved, day, x.key])


def gone_keys(db: DB) -> set[str]:
    return {r["key"] for r in db.all("SELECT key FROM report_listing WHERE status='gone'")}


def mark_gone(db: DB, key: str, today: date, by: str) -> None:
    db.execute("UPDATE report_listing SET status='gone', gone_on=?, gone_by=? WHERE key=? AND status='listed'",
               [today.isoformat(), by, key])


def apply_checks(db: DB, checks: list[dict], today: date) -> int:
    """Claude's per-listing answers about listings from earlier weeks: {url, status listed|gone|unknown, price_now}.
    Only 'listed' (with the price) and 'gone' change anything; 'unknown' (blocked, not opened) leaves the row as it was."""
    from .hunt import listing_key
    n = 0
    for c in checks:
        key = listing_key(c.get("url") or "")
        row = db.one("SELECT price FROM report_listing WHERE key=? AND status='listed'", [key])
        if row is None:
            continue
        if c.get("status") == "gone":
            mark_gone(db, key, today, "page")
            n += 1
        elif c.get("status") == "listed":
            price = c.get("price_now")
            moved = bool(price) and price != row["price"]
            db.execute("UPDATE report_listing SET last_seen=?, price=COALESCE(?, price),"
                       " prev_price=CASE WHEN ? THEN price ELSE prev_price END,"
                       " price_changed_on=CASE WHEN ? THEN ? ELSE price_changed_on END WHERE key=?",
                       [today.isoformat(), price if moved else None, moved, moved, today.isoformat(), key])
    return n


def to_recheck(db: DB, kind: str, today: date, limit: int = 30) -> list[dict]:
    """Listings from earlier weeks still counted as live, oldest sighting first, for Claude to look at again."""
    since = (today - timedelta(days=STALE_DAYS)).isoformat()
    rows = db.all("SELECT url, name, price FROM report_listing WHERE kind=? AND status='listed' AND last_seen>=? AND last_seen<?"
                  " ORDER BY last_seen LIMIT ?", [kind, since, today.isoformat(), limit])
    return [dict(r) for r in rows]


def active(db: DB, kind: str, today: date) -> list:
    since = (today - timedelta(days=STALE_DAYS)).isoformat()
    return db.all("SELECT * FROM report_listing WHERE kind=? AND status='listed' AND last_seen>=?", [kind, since])


def change_label(row, today: date) -> str:
    """🆕 new this week, 🟢▼ / 🔴▲ price move this week, else empty (colour = good or bad for the buyer)."""
    cutoff = (today - timedelta(days=CHANGE_DAYS)).isoformat()
    if row["price_changed_on"] and row["price_changed_on"] > cutoff and row["prev_price"]:
        pct = (row["price"] / row["prev_price"] - 1) * 100
        return f"{'🟢▼' if pct < 0 else '🔴▲'}{abs(pct):.0f}%"
    return "🆕" if row["first_seen"] > cutoff else ""


def gone_recently(db: DB, kind: str, today: date) -> list:
    cutoff = (today - timedelta(days=CHANGE_DAYS)).isoformat()
    return db.all("SELECT * FROM report_listing WHERE kind=? AND status='gone' AND gone_on>?", [kind, cutoff])


def find(db: DB, text: str) -> list:
    like = f"%{text.lower()}%"
    return db.all("SELECT * FROM report_listing WHERE status='listed' AND (lower(name) LIKE ? OR lower(url) LIKE ?) "
                  "ORDER BY last_seen DESC LIMIT 10", [like, like])


def changes(db: DB, kind: str, today: date) -> list[tuple[str, str, str]]:
    """(marker, name, detail) for price moves and listings found gone since the last report."""
    cutoff = (today - timedelta(days=CHANGE_DAYS)).isoformat()
    out = []
    for r in db.all("SELECT * FROM report_listing WHERE kind=? AND status='listed' AND price_changed_on>? AND prev_price IS NOT NULL"
                    " ORDER BY name", [kind, cutoff]):
        pct = (r["price"] / r["prev_price"] - 1) * 100
        out.append(("🟢" if pct < 0 else "🔴", r["name"], f"price S${r['prev_price']:,.0f} to S${r['price']:,.0f} ({pct:+.1f}%)"))
    for r in gone_recently(db, kind, today):
        who = "you said so" if r["gone_by"] == "user" else "the page showed it"
        out.append(("❌", r["name"], f"no longer listed ({who}), was S${r['price']:,.0f}"))
    return out
