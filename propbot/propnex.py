"""PropNex listings for the reports and the family website: the newest 30 HDB, EC, condo and retail listings
for sale (retail covers shophouses, HDB shops, coffee shops and F&B, mall shops).

propnex.com allows crawling (robots.txt) and its search page renders through Jina Reader, so no browser is
needed. Four pages every hour, one at a time through the web rate bucket (slow gaps, day budget). A
challenge or captcha page stops the run and is logged once; nothing tries to get past it. Paging is not
in the URL, so the newest 30 per type are read each time and the tracker keeps what was seen before.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from types import SimpleNamespace

from . import tracker
from .db import DB
from .hunt import budget_max, listing_key

log = logging.getLogger("propbot.propnex")

JINA = "https://r.jina.ai/"
SEARCH = "https://www.propnex.com/buy?propertyType={}&sortBy=newest&listingType=SALE"
TYPES = {"HDB": "hdb_resale", "EC": "ec", "CONDO": "condo_resale", "RETAIL": None}   # None: kind per listing, see retail_kind
EVERY_S = 3600
CHALLENGE = re.compile(r"human verification|captcha|verify you are human|just a moment|access denied", re.I)
HEAD = re.compile(r"^### \[(.+?)\]\((https://www\.propnex\.com/listing-details/\d+/[^)\s]+)\)", re.M)
IMG = re.compile(r"!\[[^\]]*\]\((https://[^)\s]+/pnimgs/listing/[^)\s]+)\)\]\((https://www\.propnex\.com/listing-details/[^)\s]+)\)")


def retail_kind(ptype: str, name: str) -> str:
    """PropNex retail subtype to a propbot category. An HDB address (block number first) makes a shop an HDB shop."""
    t = ptype.lower()
    if "food" in t or "beverage" in t or re.search(r"coffee|kopitiam|eating house|food court", name, re.I):
        return "coffeeshop"
    if "mall" not in t and (re.match(r"\d+[A-Z]? ", name) or re.search(r"\bhdb\b|corner shop", name, re.I)):
        return "hdb_shop"
    return "shophouse" if "shophouse" in t else "strata_commercial"


def due(db: DB, now: float) -> bool:
    return now - float(db.meta_get("propnex_at") or 0) >= EVERY_S


def parse(text: str) -> list[dict]:
    """Listings on one Jina-rendered search page: name, url, street, district, bedrooms, sqft, price, photo."""
    photos: dict[str, str] = {}
    for img, url in IMG.findall(text):
        photos.setdefault(url, img)
    heads = list(HEAD.finditer(text))
    out = []
    for i, h in enumerate(heads):
        body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)])
        price = re.search(r"####\s*\$([\d,]+)", body)
        if not price:
            continue
        where = re.search(r"\*\s*([^*\n]+?)\s*-\s*D(\d{1,2})\s*\*", body)
        nums = re.search(r"for sale!?\s*\*\s*(\d+)?\s*\*", body, re.I)
        ptype = re.search(r"\*\s*([^*\n]+?)\s+for sale", body, re.I)
        sqft = re.search(r"([\d,]+)\s*sqft", body)
        name = h.group(1).strip()
        out.append({"name": name.title() if name.isupper() else name, "url": h.group(2),
                    "street": where.group(1).strip() if where else "", "district": f"D{where.group(2)}" if where else "",
                    "bedrooms": int(nums.group(1)) if nums and nums.group(1) else None,
                    "sqft": float(sqft.group(1).replace(",", "")) if sqft else None,
                    "price": float(price.group(1).replace(",", "")), "photo": photos.get(h.group(2), ""),
                    "ptype": ptype.group(1).strip() if ptype else ""})
    return out


def run(settings, db: DB, http, limiter, today: date, journal=lambda *a, **k: None) -> int:
    """Read the three search pages and record what is in budget. Returns listings recorded."""
    db.meta_set("propnex_at", str(time.time()))
    n = 0
    for ptype, page_kind in TYPES.items():
        url = SEARCH.format(ptype)
        try:
            limiter.acquire("web", "r.jina.ai")
            resp = http.get(JINA + url, timeout=90)
            text = resp.text
        except Exception as exc:
            log.warning("PropNex %s not read: %s", ptype, exc)
            continue
        if resp.status_code in (403, 429) or CHALLENGE.search(text[:600]):
            journal("blocked", f"PropNex answered {resp.status_code} or a challenge page; stopped, not retried today", {"stream": "web"})
            break
        rows = []
        for x in parse(text):
            x["kind"] = page_kind or retail_kind(x["ptype"], x["name"])
            if settings.search.budget_min_sgd <= x["price"] <= budget_max(settings, x["kind"]):
                rows.append(x)
        for kind in {x["kind"] for x in rows}:
            tracker.record(db, kind, [SimpleNamespace(
                key=listing_key(x["url"]), name=x["name"], area=x["street"] if kind != "hdb_resale" else "", url=x["url"],
                bedrooms=x["bedrooms"], tenure="99 year" if kind in ("hdb_resale", "ec", "hdb_shop") else "unknown",
                lease_left=None, sqft=x["sqft"], price=x["price"]) for x in rows if x["kind"] == kind], today)
        for x in rows:
            if x["photo"]:
                db.upsert("listing_image", {"key": listing_key(x["url"]), "image": x["photo"], "checked_on": today.isoformat()}, "key")
        journal("fetch", f"PropNex {ptype}: {len(rows)} listings in budget", {"stream": "web"})
        n += len(rows)
    return n
