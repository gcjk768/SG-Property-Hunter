"""Family website data: every live listing the bot tracks, written to data/site/data.json for the
property-site container (nginx, site/index.html). Rebuilt every hour after the hunt.

HDB listings are compared with the last 12 months of sales in the same block (hdb_report.match), so
the site can rank them by how far the ask is under the block median. Photos come from the listing
page's og:image through the polite fetcher (robots.txt, slow per-domain gaps, page cache). PropertyGuru
and CommercialGuru answer every automated request (plain, Jina and the NAS Playwright browser) with a
Cloudflare challenge, so they are never fetched; their cards get a drawn plate instead.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from . import bto, fengshui, tracker
from .db import DB
from .hdb_report import MIN_SALES, match, norm
from .web import host_of

log = logging.getLogger("propbot.site")

KINDS = {"hdb_resale": "HDB resale", "condo_resale": "Resale condo", "condo_new_launch": "New launch",
         "ec": "Executive condo", "bto": "BTO", "shophouse": "Shophouse", "hdb_shop": "HDB shop",
         "coffeeshop": "Coffee shop & F&B", "strata_commercial": "Office / retail"}
NO_PHOTO_HOSTS = ("propertyguru.com.sg", "commercialguru.com.sg")   # Cloudflare challenge, checked 2026-10-09
PHOTO_RETRY_DAYS = 7
MAP = ("https://www.onemap.gov.sg/api/staticmap/getStaticImage?layerchosen=original"
       "&latitude={:.6f}&longitude={:.6f}&zoom=17&height=400&width=512")    # public OneMap static map, loaded by the browser
CPF_AGE = 95          # full CPF use and loan only when the lease covers the youngest buyer to this age (HDB)
OG_IMAGE = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](https://[^"\']+)', re.I)


def _photos(db: DB) -> dict[str, str]:
    return {r["key"]: r["image"] for r in db.all("SELECT key, image FROM listing_image WHERE image != ''")}


def fetch_photos(db: DB, fetcher, today: date, limit: int = 6) -> int:
    """Look up og:image for a few listings that have none yet. Failures are remembered for a week."""
    retry = (today - timedelta(days=PHOTO_RETRY_DAYS)).isoformat()
    done = {r["key"] for r in db.all("SELECT key FROM listing_image WHERE image != '' OR checked_on > ?", [retry])}
    todo = [r for k in KINDS for r in tracker.active(db, k, today)
            if r["key"] not in done and not host_of(r["url"]).endswith(NO_PHOTO_HOSTS)][:limit]
    found = 0
    for r in todo:
        image = ""
        try:
            m = OG_IMAGE.search(fetcher.fetch(r["url"]).text)
            image = m.group(1) if m and "logo" not in m.group(1).lower() else ""   # no photo: the site logo
        except Exception as exc:     # refused, cooldown, 403: no photo, the card still shows
            log.info("no photo for %s: %s", r["url"], exc)
        db.upsert("listing_image", {"key": r["key"], "image": image, "checked_on": today.isoformat()}, "key")
        found += bool(image)
    return found


def _change(row, today: date) -> str:
    label = tracker.change_label(row, today)
    return "new" if label == "🆕" else "drop" if label.startswith("🟢") else "up" if label.startswith("🔴") else ""


def _street_towns(db: DB) -> dict[str, str]:
    return {r["street"]: r["town"] for r in db.all("SELECT DISTINCT street, town FROM hdb_resale")}


def _hdb_town(name: str, towns: dict[str, str]) -> str:
    """Town of an HDB listing that had no sale of its block to match: any sale on the same street."""
    m = re.match(r"\d+[A-Z]? (.+)", name)
    return (towns.get(norm(m.group(1))) or towns.get(m.group(1).upper(), "")) if m else ""


def _money(v: float) -> str:
    return f"S${v / 1e6:.2f}M" if v >= 1e6 else f"S${v / 1e3:,.0f}k"


def verdict(it: dict) -> tuple[str, int, list[str]]:
    """(tier, score, reasons in plain words) from the facts the card already shows. No model call, so it is
    the same every hour and every reason can be checked on the card."""
    score, why = 0.0, []
    deal, lease, freehold = it["deal_pct"], it["lease_left"], "freehold" in (it["tenure"] or "") or "999" in (it["tenure"] or "")
    if deal is not None:
        gap = _money(abs(it["median"] - it["price"]))
        n = it["sales"]
        where = f"the median of {n} sale{'s' if n != 1 else ''} of {it['type'] or 'similar'} flats in this block over 12 months"
        if it["reliable"]:
            score += max(min(deal, 20), -20)
            why.append(f"{gap} ({abs(deal):.0f}%) {'under' if deal > 0 else 'over'} {where}")
        else:
            score += max(min(deal * .3, 5), -5)
            why.append(f"{gap} {'under' if deal > 0 else 'over'} {where}, a rough guide only")
    elif it["kind"] == "hdb_resale":
        why.append("No sale in this block in the last year to compare with: check nearby blocks")
    else:
        why.append("No transaction benchmark yet: compare with nearby projects before deciding")
    if freehold:
        score += 2
        why.append("Freehold: no lease decay")
    elif lease:
        if lease < 60:
            score -= 8
            why.append(f"Only {lease:.0f} years lease left: CPF use and loans are limited and value falls faster")
        else:
            score -= 3 if lease < 65 else 0
            age = CPF_AGE - lease
            why.append(f"{lease:.0f} years lease left: " + ("full CPF use for any adult buyer" if age <= 21
                                                             else f"full CPF use for buyers aged {age:.0f} and above"))
    if it["change"] == "drop" and it["prev_price"]:
        score += 2
        why.append(f"Price cut from {_money(it['prev_price'])} to {_money(it['price'])}")
    if it.get("mrt_m") is not None:
        score += 2 if it["mrt_m"] <= 500 else -1 if it["mrt_m"] > 1200 else 0
        why.append(f"{it['mrt']} {it['mrt_m']:,} m away")
    lease_ok = freehold or not lease or lease >= 60
    if it["reliable"] and deal >= 5 and lease_ok:
        tier = "Top pick"
    elif score >= 2 and lease_ok:
        tier = "Worth a look"
    elif not lease_ok:
        tier = "Short lease"
    elif it["reliable"] and deal <= -5:
        tier = "Above market"
    elif deal is not None:
        tier = "Fair price"
    else:
        tier = "Not enough data"
    return tier, round(score, 1), why


def _place(geo, it: dict) -> None:
    """Map picture and nearest MRT from the OneMap geocode cache (a new address costs one OneMap call)."""
    try:
        hit = geo.geocode(it["name"] if it["kind"] == "hdb_resale" else f"{it['name']} {it['area']}".strip())
        if not hit:
            hit = geo.geocode(it["name"])
    except Exception as exc:          # budget, no token, network: the card shows the drawn plate
        log.info("no location for %s: %s", it["name"], exc)
        return
    if hit:
        it["lat"], it["lon"] = hit
        it["map"] = MAP.format(*hit)
        near = geo.nearest(*hit)
        if near:
            it["mrt"], it["mrt_m"] = near[0], int(round(near[1], -1))


def items(db: DB, today: date, geo=None) -> list[dict]:
    photos, towns = _photos(db), None
    if geo is not None:
        try:
            geo.ensure_stations(today)
            fengshui.ensure(db, geo, today)
        except Exception as exc:
            log.info("MRT exits or places not loaded: %s", exc)
    out = []
    for kind, label in KINDS.items():
        rows = [dict(r) for r in tracker.active(db, kind, today)]
        comp = {m["key"]: m for m in match(db, today, rows)} if kind == "hdb_resale" else {}
        for r in rows:
            m = comp.get(r["key"])
            if kind == "hdb_resale" and not m and not r["area"]:
                towns = towns if towns is not None else _street_towns(db)
                r["area"] = _hdb_town(r["name"], towns)
            sqft = (m and m["sqft"]) or r["sqft"]
            out.append({
                "key": r["key"], "kind": kind, "label": label, "name": r["name"],
                "area": ((m and m["town"]) or r["area"] or "").title(),
                "type": m["ftype"].title() if m else (f"{r['bedrooms']} bedroom" if r["bedrooms"] else ""),
                "price": r["price"], "prev_price": r["prev_price"], "sqft": round(sqft) if sqft else None,
                "psf": round(r["price"] / sqft) if sqft else None,
                "median": m and m["med"], "sales": m and m["n"],
                "deal_pct": round((1 - r["price"] / m["med"]) * 100, 1) if m else None,
                "reliable": bool(m and m["inrange"] and m["n"] >= MIN_SALES),
                "tenure": r["tenure"], "lease_left": round(m["rem"]) if m else r["lease_left"],
                "first_seen": r["first_seen"], "last_seen": r["last_seen"], "change": _change(r, today),
                "url": r["url"], "site": host_of(r["url"]).removeprefix("www."), "photo": photos.get(r["key"], ""),
                "map": "", "mrt": "", "mrt_m": None,
            })
            if geo is not None:
                _place(geo, out[-1])
            it = out[-1]
            it["tier"], it["score"], it["why"] = verdict(it)
            near = fengshui.nearest_by_kind(db, it["lat"], it["lon"]) if it.get("lat") else {}
            it["fs"] = fengshui.reading(it["name"], it["price"], near, it["mrt"], it["mrt_m"])
            it.pop("lat", None), it.pop("lon", None)
    return dedupe(out)


def dedupe(items_: list[dict]) -> list[dict]:
    """One card per home: the same kind, name and price found on several sites (or twice) is merged,
    newest sighting first, and the other links are kept under 'also'."""
    out, by = [], {}
    for it in sorted(items_, key=lambda x: x["last_seen"], reverse=True):
        k = (it["kind"], re.sub(r"\W+", " ", it["name"].lower()).strip(), round(it["price"] or 0, -3))
        if k in by:
            keep = by[k]
            if it["url"] != keep["url"] and it["url"] not in (a["url"] for a in keep["also"]):
                keep["also"].append({"site": it["site"], "url": it["url"]})
            keep["photo"] = keep["photo"] or it["photo"]
            keep["first_seen"] = min(keep["first_seen"], it["first_seen"])
            continue
        by[k] = dict(it, also=[])
        out.append(by[k])
    return out


def export(db: DB, out_dir: Path, today: date, now: datetime | None = None, geo=None) -> int:
    """Write data.json atomically (the browser never sees half a file). Returns listings written."""
    data = items(db, today, geo)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "data.json.tmp"
    tmp.write_text(json.dumps({"updated": (now or datetime.now()).isoformat(timespec="minutes"), "items": data, "bto": bto.listed(db)},
                              ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o644)              # nginx in property-site reads it as another user
    os.replace(tmp, out_dir / "data.json")
    return len(data)
