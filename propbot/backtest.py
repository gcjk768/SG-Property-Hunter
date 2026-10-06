"""Backtest the lease decay table on HDB repeat sales.

A repeat sale is the same flat (town, type, block, street, storey band, floor area) sold twice at least
`min_gap_years` apart. The predicted second price is the first price moved by the town and flat type's
median price per sqm between the two months (the market), then reduced by the lease decay table for
each year held (the model). The error is predicted against actual. If the table is right, the mean
error per lease band is near zero; a positive mean means the table decays too little for that band.
Needs the full history: `propbot backfill` first.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass

from .config import Settings
from .db import DB
from .engine.projection import decay_pct

BANDS = ("above_80_years", "70_to_80_years", "60_to_70_years", "50_to_60_years", "below_50_years")


def band_of(remaining: float) -> str:
    if remaining > 80:
        return "above_80_years"
    if remaining > 70:
        return "70_to_80_years"
    if remaining > 60:
        return "60_to_70_years"
    if remaining > 50:
        return "50_to_60_years"
    return "below_50_years"


@dataclass
class Pair:
    town: str
    flat_type: str
    month0: str
    month1: str
    price0: float
    price1: float
    lease0: float


def _months_between(m0: str, m1: str) -> int:
    y0, mo0 = int(m0[:4]), int(m0[5:7])
    y1, mo1 = int(m1[:4]), int(m1[5:7])
    return (y1 - y0) * 12 + (mo1 - mo0)


def pairs(db: DB, min_gap_years: float = 3) -> list[Pair]:
    rows = db.all("SELECT town, flat_type, block, street, storey, sqm, month, price, remaining_lease FROM hdb_resale "
                  "WHERE remaining_lease IS NOT NULL ORDER BY town, flat_type, block, street, storey, sqm, month")
    out, prev = [], None
    for r in rows:
        key = (r["town"], r["flat_type"], r["block"], r["street"], r["storey"], r["sqm"])
        if prev is not None and prev[0] == key and _months_between(prev[1]["month"], r["month"]) >= min_gap_years * 12:
            p = prev[1]
            out.append(Pair(r["town"], r["flat_type"], p["month"], r["month"], p["price"], r["price"], p["remaining_lease"]))
        prev = (key, r)
    return out


def market_index(db: DB, min_n: int = 5) -> dict[tuple[str, str, str], float]:
    """Median price per sqm by (town, flat type, month), only where at least min_n flats sold."""
    groups: dict[tuple, list[float]] = defaultdict(list)
    for r in db.all("SELECT town, flat_type, month, price, sqm FROM hdb_resale WHERE sqm > 0"):
        groups[(r["town"], r["flat_type"], r["month"])].append(r["price"] / r["sqm"])
    return {k: statistics.median(v) for k, v in groups.items() if len(v) >= min_n}


def predict(price0: float, idx0: float, idx1: float, lease0: float, years: int, table: dict[str, float]) -> float:
    v = price0 * idx1 / idx0
    for t in range(years):
        v *= 1 - decay_pct(lease0 - t, False, table) / 100.0
    return v


def run(db: DB, settings: Settings, *, min_gap_years: float = 3) -> dict:
    """Per lease band at purchase: count, mean and median error % (predicted vs actual)."""
    table = settings.assumptions.lease_decay
    idx = market_index(db)
    errors: dict[str, list[float]] = defaultdict(list)
    held: dict[str, list[int]] = defaultdict(list)
    used = 0
    for p in pairs(db, min_gap_years):
        i0, i1 = idx.get((p.town, p.flat_type, p.month0)), idx.get((p.town, p.flat_type, p.month1))
        if not i0 or not i1:
            continue
        years = round(_months_between(p.month0, p.month1) / 12)
        pred = predict(p.price0, i0, i1, p.lease0, years, table)
        errors[band_of(p.lease0)].append((pred / p.price1 - 1) * 100)
        held[band_of(p.lease0)].append(years)
        used += 1
    bands = {}
    for b in BANDS:
        e, y = errors.get(b, []), held.get(b, [])
        mean = statistics.fmean(e) if e else None
        yrs = statistics.fmean(y) if y else None
        bands[b] = {"n": len(e), "mean_error_pct": mean, "median_error_pct": statistics.median(e) if e else None,
                    "decay_pct": table.get(b, 0.0), "avg_years": yrs,
                    # the decay per year the data points to: the table's figure plus the yearly share of the error
                    "implied_decay_pct": table.get(b, 0.0) + mean / yrs if e and yrs else None}
    return {"pairs": used, "bands": bands}


def report(result: dict) -> str:
    lines = [f"Repeat sales used: {result['pairs']}", "",
             f"{'lease band at purchase':<24} {'table/yr':>8} {'n':>6} {'years':>5} {'mean err':>9} {'median err':>10} {'data says/yr':>12}"]
    for b, r in result["bands"].items():
        mean = f"{r['mean_error_pct']:+.1f}%" if r["mean_error_pct"] is not None else "n/a"
        med = f"{r['median_error_pct']:+.1f}%" if r["median_error_pct"] is not None else "n/a"
        yrs = f"{r['avg_years']:.1f}" if r["avg_years"] is not None else "n/a"
        imp = f"{r['implied_decay_pct']:.2f}%" if r["implied_decay_pct"] is not None else "n/a"
        lines.append(f"{b.replace('_', ' '):<24} {r['decay_pct']:>7.1f}% {r['n']:>6} {yrs:>5} {mean:>9} {med:>10} {imp:>12}")
    lines += ["", "Error = predicted / actual second price, minus one. Positive: the table decays too little for that band",
              "(the model predicted more than the flat fetched). Negative: it decays too much. Near zero: the band is right.",
              "data says/yr = the table's figure plus the error spread over the years held.",
              "Caution: the market side is the town median over flats of every age, so older flats also lag newer ones",
              "for reasons other than the lease. Treat the result as a check on the table, not a replacement for it."]
    return "\n".join(lines)
