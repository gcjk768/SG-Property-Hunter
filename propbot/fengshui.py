"""Feng shui reading for the family website: the good and the bad of a home's numbers and surroundings.

Traditional beliefs, scored from open data: water, hills and parks nearby (NParks parks on data.gov.sg),
cemeteries, columbaria and crematoria (NEA after-death facilities), big hospitals (a fixed list, located
with the OneMap geocoder), the nearest MRT, and the block number and price digits (8 prosper, 4 death).
Places are loaded once a month into the poi table. Unit facing and floor are unknown, so they are not judged.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date

from .db import DB
from .geo import POLL_DOWNLOAD, haversine_m
from .ratelimit import BudgetExceeded

log = logging.getLogger("propbot.fengshui")

PARKS = "d_0542d48f0991541706b58059381a6eca"          # NParks Parks (points)
AFTER_DEATH = "d_8057b4f4c7eca22c3c51c4ac05440f21"    # NEA cemeteries, crematoria, columbaria
COLUMBARIA = "d_9b0752e9d3f1f9d957d5d8be2b58dfff"     # NEA dedicated columbaria
# everyday places for the website card (propbot/insights.py), loaded with the rest
HAWKERS = "d_4a086da0a5553be1d89383cd90d07ecd"       # NEA hawker centres
POLYCLINICS = "d_0cdfbf7e277e8bfa1ef79fadf4b71b56"   # HPB screening centres: the polyclinics
SCHOOLS = "d_688b934f82c1059ed0a6993d2a829089"       # MOE school directory (CSV, postal codes)
HOSPITALS = ["Singapore General Hospital", "Tan Tock Seng Hospital", "National University Hospital", "Changi General Hospital",
             "Khoo Teck Puat Hospital", "Ng Teng Fong General Hospital", "Sengkang General Hospital",
             "KK Women's and Children's Hospital", "Alexandra Hospital", "Woodlands Hospital", "Mount Elizabeth Hospital",
             "Mount Elizabeth Novena Hospital", "Gleneagles Hospital", "Raffles Hospital", "Mount Alvernia Hospital",
             "Parkway East Hospital", "Farrer Park Hospital", "Institute of Mental Health"]
WATER = re.compile(r"RESERVOIR|WATERWAY|LAKE|RIVER|BEACH|COAST|\bBAY\b|POND|MARINA", re.I)
HILL = re.compile(r"\bHILLS?\b|HILLOCK|\bBUKIT\b|\bMOUNT\b|NATURE RESERVE", re.I)
PARK = re.compile(r"\bPARK\b|\bPK\b|GARDEN|RESERVE", re.I)
NOT_PARK = re.compile(r"CAR PARK|PLAYGROUND|\bPG\b|FITNESS CORNER", re.I)
NEAR = {"water": 800, "hill": 1000, "park": 400, "yin": 1500, "hospital": 500}
DIGITS = {"8": (1, "8 sounds like prosper (发)"), "9": (.5, "9 sounds like long-lasting (久)"),
          "6": (.5, "6 means smooth sailing (顺)"), "3": (.25, "3 sounds like life and growth (生)")}


def kind_of_park(name: str) -> str | None:
    if NOT_PARK.search(name) and not (WATER.search(name) or HILL.search(name)):
        return None
    return "water" if WATER.search(name) else "hill" if HILL.search(name) else "park" if PARK.search(name) else None


def _download_url(geo, dataset: str) -> str | None:
    """The file link for a dataset. data.gov.sg answers 429 when asked too fast, so wait and ask once more."""
    for attempt in range(2):
        geo.limiter.acquire("datagov")
        url = (geo.http.get(POLL_DOWNLOAD.format(id=dataset), timeout=30).json().get("data") or {}).get("url")
        if url:
            return url
        time.sleep(12)
    return None


def _points(geo, dataset: str) -> list[tuple[str, float, float]]:
    url = _download_url(geo, dataset)
    geo.limiter.acquire("datagov")
    out = []
    for f in (geo.http.get(url, timeout=120).json().get("features") or []) if url else []:
        g, p = f.get("geometry") or {}, f.get("properties") or {}
        if g.get("type") == "Point" and p.get("NAME"):
            lon, lat = g["coordinates"][:2]
            out.append((p["NAME"], float(lat), float(lon)))
    return out


def _primary_schools(geo) -> tuple[list[tuple[str, float, float]], bool]:
    """MOE primary schools located from their postal code with the OneMap geocoder. Every answer is cached, so when
    the daily OneMap budget runs out this returns what it has and False, and the next hour carries on."""
    import csv, io
    url = _download_url(geo, SCHOOLS)
    if not url:
        return [], False
    geo.limiter.acquire("datagov")
    out, complete = [], True
    for r in csv.DictReader(io.StringIO(geo.http.get(url, timeout=120).text)):
        if "PRIMARY" in r.get("mainlevel_code", "") or "P1" in r.get("mainlevel_code", ""):
            try:
                hit = geo.geocode(r["postal_code"])
            except BudgetExceeded:
                complete = False
                continue                       # cached ones still come through; the rest wait for the next hour
            if hit:
                out.append((r["school_name"].title(), *hit))
    return out, complete


def ensure(db: DB, geo, today: date) -> int:
    """Load the places once a month. Best effort: a failure keeps last month's places."""
    month = today.strftime("%Y-%m")
    if db.meta_get("poi_month") != month:
        try:
            rows = [(k, n, la, lo) for n, la, lo in _points(geo, PARKS) if (k := kind_of_park(n))]
            seen = set()
            for ds in (AFTER_DEATH, COLUMBARIA):
                for n, la, lo in _points(geo, ds):
                    if n.lower() not in seen:
                        seen.add(n.lower())
                        rows.append(("yin", n, la, lo))
            rows += [("hawker", n, la, lo) for n, la, lo in _points(geo, HAWKERS)]
            rows += [("clinic", n, la, lo) for n, la, lo in _points(geo, POLYCLINICS) if "olyclinic" in n]
            schools, complete = _primary_schools(geo)
            rows += [("school", n, la, lo) for n, la, lo in schools]
            for name in HOSPITALS:
                hit = geo.geocode(name)
                if hit:
                    rows.append(("hospital", name, *hit))
            kinds = {r[0] for r in rows}
            if {"park", "yin"} <= kinds:          # a throttled download returns no file: keep last month, retry next hour
                db.execute("DELETE FROM poi")
                db.executemany("INSERT OR REPLACE INTO poi VALUES (?,?,?,?)", rows)
                if complete and {"hawker", "clinic"} <= kinds:
                    db.meta_set("poi_month", month)
            else:
                log.warning("feng shui places incomplete (%s), retrying next hour", sorted(kinds))
        except Exception as exc:
            log.warning("feng shui places not loaded: %s", exc)
    return db.scalar("SELECT COUNT(*) FROM poi", default=0)


def around(db: DB, lat: float, lon: float) -> dict[str, list[tuple[str, int]]]:
    """Every place by kind, nearest first: [(name, metres)]."""
    # ponytail: scans ~800 places per listing; fine for a few hundred listings an hour, add a grid index if it grows
    out: dict[str, list[tuple[str, int]]] = {}
    for r in db.all("SELECT kind, name, lat, lon FROM poi"):
        out.setdefault(r["kind"], []).append((r["name"], int(haversine_m(lat, lon, r["lat"], r["lon"]))))
    return {k: sorted(v, key=lambda x: x[1]) for k, v in out.items()}


def nearest_by_kind(db: DB, lat: float, lon: float, places: dict | None = None) -> dict[str, tuple[str, int]]:
    return {k: v[0] for k, v in (places or around(db, lat, lon)).items() if v}


def _nice(name: str) -> str:
    return re.sub(r"\bPk\b", "Park", name.title() if name.isupper() else name)


def reading(name: str, price: float, near: dict[str, tuple[str, int]], mrt: str = "", mrt_m: int | None = None) -> dict:
    """{stars 1..5, score, good: [...], bad: [...]} in plain words."""
    score, good, bad = 3.0, [], []
    for kind, pts, text in (("water", 1, "Water nearby gathers wealth (水为财)"),
                            ("hill", .75, "A hill behind gives support, the black tortoise (靠山)"),
                            ("park", .5, "Greenery nearby brings fresh, living qi (生气)")):
        if kind in near and near[kind][1] <= NEAR[kind]:
            score += pts
            good.append(f"{text}: {_nice(near[kind][0])}, {near[kind][1]:,} m")
    if mrt_m is not None and mrt_m <= 80:
        score -= .5
        bad.append(f"{mrt} only {mrt_m:,} m away: rushing qi and train noise, check the unit does not face the track")
    elif mrt_m is not None and mrt_m <= 400:
        score += .5
        good.append(f"{mrt} {mrt_m:,} m away: qi and people flow to the door")
    if "yin" in near and near["yin"][1] <= NEAR["yin"]:
        score -= 1.5
        bad.append(f"{_nice(near['yin'][0])} {near['yin'][1]:,} m away: heavy yin energy (阴气)")
    if "hospital" in near and near["hospital"][1] <= NEAR["hospital"]:
        score -= 1
        bad.append(f"{near['hospital'][0]} {near['hospital'][1]:,} m away: yin energy and sirens")

    blk = (re.match(r"(\d+)", name) or [None, ""])[1]
    digits = str(round(price or 0)).rstrip("0")
    for d in sorted(set(blk), key=blk.index):
        if d in DIGITS:
            score += DIGITS[d][0] * blk.count(d)
            good.append(f"Block {blk}: {DIGITS[d][1]}")
    if "4" in blk:
        score -= 1.5 * blk.count("4")
        bad.append(f"Block {blk}: 4 sounds like death (死), which many buyers avoid")
    if blk.endswith("14"):
        score -= .5
        bad.append(f"Block {blk} ends in 14, which sounds like 'sure to die' (实死)")
    if "888" in digits:
        score += 1.5
        good.insert(0, "Triple 8 in the price: huat ah! 🧧")
    elif digits.count("8"):
        score += min(digits.count("8") * .5, 1)
        good.append(f"{digits.count('8')} lucky 8{'s' if digits.count('8') > 1 else ''} in the asking price")
    if "4" in digits:
        score -= .5 * digits.count("4")
        bad.append("A 4 hides in the asking price")
    return {"stars": max(1, min(5, round(score))), "score": round(score, 2), "good": good, "bad": bad}
