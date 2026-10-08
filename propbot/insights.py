"""Extra facts for each website card: the proof behind the verdict, the money a typical buyer needs, rent and
yield, the town's price trend, and what is nearby. All from data the bot already keeps or official open data.

Money is worked out for a generic first-timer couple (Singapore citizens, no other property), never from
Profile.md, because the website is public. Every figure comes from rules/sg_property_rules.yaml through the
same engine functions /propanalyse uses.
"""
from __future__ import annotations

import statistics as st
from datetime import date

from .db import DB
from .engine.costs import bsd
from .engine.financing import instalment
from .pulse import SQFT_PER_SQM, TREND_YEARS, town_cagr

HDB_KINDS = {"hdb_resale", "bto"}
COMMERCIAL = {"shophouse", "hdb_shop", "coffeeshop", "strata_commercial"}
RENT_TYPE = {"2 ROOM": "2-RM", "3 ROOM": "3-RM", "4 ROOM": "4-RM", "5 ROOM": "5-RM", "EXECUTIVE": "EXEC"}
BANDS = (("Low (1 to 6)", 1, 6), ("Mid (7 to 12)", 7, 12), ("High (13 up)", 13, 99))


def block_proof(sales) -> dict:
    """The latest 5 sales of the matched flat type in the block, and the median by floor band."""
    rows = sorted((dict(r) for r in sales), key=lambda r: r["month"], reverse=True)
    recent = [{"month": r["month"], "storey": r.get("storey") or "", "sqft": round(r["sqm"] * SQFT_PER_SQM),
               "price": r["price"]} for r in rows[:5]]
    bands = []
    for label, lo, hi in BANDS:
        ps = [r["price"] for r in rows if r.get("storey") and lo <= int(r["storey"][:2]) <= hi]
        if ps:
            bands.append({"band": label, "median": st.median(ps), "n": len(ps)})
    return {"recent": recent, "bands": bands}


def money(it: dict, rules, rates) -> dict | None:
    """Upfront cash and monthly instalment for a first-timer couple; HDB loan and bank loan side by side."""
    price, kind, lease = it["price"], it["kind"], it.get("lease_left")
    if not price:
        return None
    out = {"bsd": round(bsd(price, rules, None, kind not in COMMERCIAL))}
    if kind in COMMERCIAL:
        c = rules.v(None, "commercial_loan_planning")
        loan = price * c["ltv_pct"] / 100
        out["bank"] = {"ltv": c["ltv_pct"], "down": round(price - loan), "cash_only": True, "years": c["max_tenure_years"],
                       "rate": rates.bank_rate_today, "monthly": round(instalment(loan, rates.bank_rate_today, c["max_tenure_years"]))}
        return out
    hdb_flat = kind in HDB_KINDS
    if hdb_flat:
        h = rules.v(None, "hdb_loan")
        years = min(h["max_tenure_years"], (lease - h["tenure_lease_buffer_years"]) if lease else h["max_tenure_years"])
        if years >= 5:
            loan = price * h["ltv_pct"] / 100
            out["hdb"] = {"ltv": h["ltv_pct"], "down": round(price - loan), "years": int(years), "rate": rates.hdb_rate,
                          "monthly": round(instalment(loan, rates.hdb_rate, years))}
    ltv, min_cash = rules.ltv(None, 0, False)
    years = rules.v(None, "mas_max_tenure", "hdb_flat_years" if hdb_flat else "other_years")
    if lease:
        years = min(years, max(5, lease - 20))
    loan = price * ltv / 100
    out["bank"] = {"ltv": ltv, "down": round(price - loan), "cash_min": round(price * min_cash / 100), "years": int(years),
                   "rate": rates.bank_rate_today, "monthly": round(instalment(loan, rates.bank_rate_today, years))}
    if kind == "hdb_resale":
        g = rules.v(None, "cpf_housing_grant_resale", "family")
        big = "5" in (it.get("type") or "") or "EXEC" in (it.get("type") or "").upper()
        out["grants"] = {"cpf_housing_grant": g["five_room_or_larger" if big else "up_to_4_room"],
                         "ehg_max": rules.v(None, "ehg", "family_max"),
                         "ehg_income_ceiling": rules.v(None, "ehg", "family_income_ceiling")}
    return out


def rent(db: DB, town: str, flat_type: str, price: float) -> dict | None:
    """HDB median rent for the town and flat type (latest quarter) and the gross yield at the asking price."""
    t = RENT_TYPE.get((flat_type or "").upper())
    if not (t and town and price):
        return None
    r = db.one("SELECT quarter, median_rent FROM hdb_rent WHERE town=? AND flat_type=? AND median_rent > 0 "
               "ORDER BY quarter DESC LIMIT 1", [town.upper(), t])
    if not r:
        return None
    return {"monthly": r["median_rent"], "quarter": r["quarter"], "yield_pct": round(r["median_rent"] * 12 / price * 100, 1)}


def trend(db: DB, town: str, flat_type: str, today: date) -> dict | None:
    """Yearly price growth of this flat type in this town over the trend window (HDB sales)."""
    if not town:
        return None
    tc = town_cagr(db, town, flat_type or None, today) or town_cagr(db, town, None, today)
    return {"cagr_pct": round(tc[0], 1), "sales": tc[1]} if tc else None


def nearby(near_all: dict[str, list[tuple[str, int]]]) -> dict:
    """Primary schools within 1 and 2 km, and the nearest hawker centre and polyclinic."""
    schools = near_all.get("school", [])
    first = lambda k: (lambda xs: {"name": xs[0][0], "m": xs[0][1]} if xs else None)(near_all.get(k, []))
    return {"schools_1km": [n for n, d in schools if d <= 1000], "schools_2km": sum(d <= 2000 for _, d in schools),
            "hawker": first("hawker"), "clinic": first("clinic")}


def outlook(it: dict, settings, years: int = 5) -> dict:
    """Likely value change over `years` and why, from data only: the town's resale price trend (capped at the finance
    engine's base growth cap), the lease decay table, and planned MRT stations. No model call, so it never changes
    between refreshes unless the data does. Condos and commercial units have no trend data here, so only the lease and
    MRT reasons are shown, and the label says so."""
    from .engine.projection import value_outlook
    a = settings.assumptions
    freehold = "freehold" in (it["tenure"] or "") or "999" in (it["tenure"] or "")
    lease = it.get("lease_left")
    tr = it.get("trend")
    cagr = tr["cagr_pct"] if tr else 0.0
    pct = value_outlook(cagr, lease, freehold, years, a.lease_decay, a.base_cagr_cap_pct)
    drag = value_outlook(0.0, lease, freehold, years, a.lease_decay, None) if (lease or freehold) else None
    why = []
    if tr:
        capped = min(cagr, a.base_cagr_cap_pct)
        why.append({"good": capped >= 0, "text": f"{it['area']} {it['type'] or 'flat'} prices moved {cagr:+.1f}% a year over the last "
                    f"{TREND_YEARS} years ({tr['sales']:,} sales)" + (f"; we count at most {capped:+.1f}% a year" if cagr > capped else "")})
    if freehold:
        why.append({"good": True, "text": "Freehold: the land lease never runs down"})
    elif drag is not None and lease:
        why.append({"good": drag > -1, "text": f"{lease:.0f} years of lease left: ageing takes about {abs(drag):.1f}% off over {years} years"
                    if drag < -0.05 else f"{lease:.0f} years of lease left: no ageing discount yet"})
    for m in (it.get("upcoming_mrt") or [])[:2]:
        why.append({"good": True, "text": f"Planned MRT: {m}"})
    if it.get("deal_pct") is not None and it.get("reliable") and it["deal_pct"] >= 5:
        why.append({"good": True, "text": f"Priced {it['deal_pct']:.0f}% under the block, a cushion if prices stall"})
    if it.get("deal_pct") is not None and it.get("reliable") and it["deal_pct"] <= -5:
        why.append({"good": False, "text": f"Priced {abs(it['deal_pct']):.0f}% over the block: little room to gain at that price"})
    known = bool(tr) or drag is not None
    if not known:
        return {"label": "Not enough data", "pct": None, "years": years, "why": why, "basis": "no sales trend or lease data for this listing"}
    label = "Likely to rise" if pct >= 3 else "Likely to fall" if pct <= -3 else "Roughly flat"
    return {"label": label, "pct": round(pct, 1), "years": years, "why": why,
            "basis": "town price trend and lease ageing" if tr else "lease ageing only (no sales trend for this type)"}
