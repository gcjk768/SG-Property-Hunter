"""HDB resale pulse: new resale transactions from data.gov.sg, picked when notably cheap or high yield.

Free official data, no key: one request an hour for the latest two months, a full refresh of the
median window once a day. A deal is notable when its price per sqm is value_discount_pct below the
12 month median for the same town, flat type and lease band, or when HDB's median rent for that town
and flat type gives at least min_yield_pct gross. Each transaction is posted once (pulse_alerted).
"""
from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import quote_plus

import httpx

from .config import Settings
from .db import DB
from .ratelimit import RateLimiter
from .render import REPORT_TITLES, card, dot, note, section_messages
from .telegram import esc, esc_attr

log = logging.getLogger("propbot.pulse")

DATASTORE = "https://data.gov.sg/api/action/datastore_search"
PAGE = 10000
SQFT_PER_SQM = 10.7639
DIVIDER = "━━━━━━━━━━━━━━━━"
RENT_TYPE = {"1 ROOM": "1-RM", "2 ROOM": "2-RM", "3 ROOM": "3-RM", "4 ROOM": "4-RM", "5 ROOM": "5-RM",
             "EXECUTIVE": "EXEC"}


@dataclass
class Deal:
    key: str
    month: str
    town: str
    flat_type: str
    block: str
    street: str
    storey: str
    sqm: float
    remaining_lease: float | None
    price: float
    median_psm: float
    discount_pct: float          # positive = below the median
    rent: float | None
    yield_pct: float | None
    new: bool = True

    @property
    def psf(self) -> float:
        return self.price / (self.sqm * SQFT_PER_SQM)


# ------------------------------------------------------------ fetching
def months_back(today: date, n: int) -> list[str]:
    y, m, out = today.year, today.month, []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def parse_lease(text: str | None) -> float | None:
    """'61 years 04 months' -> 61.33; '61' -> 61."""
    if not text:
        return None
    nums = [int(x) for x in re.findall(r"\d+", str(text))]
    if not nums:
        return None
    return round(nums[0] + (nums[1] / 12 if len(nums) > 1 else 0), 2)


def _get(http: httpx.Client, limiter: RateLimiter, params: dict) -> dict:
    limiter.acquire("datagov")
    resp = http.get(DATASTORE, params=params)
    limiter.log("datagov", params["resource_id"], url=str(resp.request.url), status=resp.status_code,
                nbytes=len(resp.content))
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"data.gov.sg error: {str(data)[:200]}")
    return data["result"]


def refresh(db: DB, settings: Settings, limiter: RateLimiter, http: httpx.Client, today: date,
            *, full: bool) -> int:
    """Store resale rows for the last 2 months (full: the whole median window) and, when full, rents.
    Returns how many transactions were seen for the first time."""
    import json
    ds = settings.sources.datagov.datasets
    months = months_back(today, settings.pulse.median_months + 1 if full else 2)
    stamp = datetime.now().isoformat(timespec="seconds")
    before = db.scalar("SELECT COUNT(*) FROM hdb_resale", default=0)
    offset = 0
    while True:
        res = _get(http, limiter, {"resource_id": ds["hdb_resale_prices"], "limit": PAGE, "offset": offset,
                                   "filters": json.dumps({"month": months})})
        recs = res.get("records", [])
        rows = []
        for r in recs:
            try:
                price, sqm = float(r["resale_price"]), float(r["floor_area_sqm"])
            except (KeyError, TypeError, ValueError):
                continue
            key = "|".join(str(r.get(k, "")) for k in ("month", "town", "flat_type", "block", "street_name",
                                                       "storey_range", "floor_area_sqm", "resale_price"))
            rows.append((key, r["month"], r["town"], r["flat_type"], r.get("block", ""), r.get("street_name", ""),
                         r.get("storey_range", ""), sqm, r.get("flat_model", ""),
                         int(r["lease_commence_date"]) if str(r.get("lease_commence_date", "")).isdigit() else None,
                         parse_lease(r.get("remaining_lease")), price, stamp))
        db.executemany("INSERT OR IGNORE INTO hdb_resale VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        offset += len(recs)
        if len(recs) < PAGE or offset >= res.get("total", 0):
            break
    if full or not db.scalar("SELECT COUNT(*) FROM hdb_rent", default=0):
        rid = ds["hdb_median_rent"]
        total = _get(http, limiter, {"resource_id": rid, "limit": 1}).get("total", 0)
        recs = _get(http, limiter, {"resource_id": rid, "limit": 500, "offset": max(0, total - 500)}).get("records", [])
        rents = []
        for r in recs:
            try:
                rents.append((r["quarter"], r["town"], r["flat_type"], float(r["median_rent"])))
            except (KeyError, TypeError, ValueError):
                continue           # 'na' and '-' mean too few rentals
        db.executemany("INSERT OR REPLACE INTO hdb_rent VALUES (?,?,?,?)", rents)
    return db.scalar("SELECT COUNT(*) FROM hdb_resale", default=0) - before


# ------------------------------------------------------------ picking
def _peer(r) -> tuple:
    """Comparable group: same town, flat type and 10 year lease band (a 55 year lease is not a 90 year one)."""
    lease = r["remaining_lease"]
    return r["town"], r["flat_type"], int(lease // 10) * 10 if lease is not None else None


def picks(db: DB, settings: Settings, today: date) -> tuple[list[Deal], dict]:
    """Notable deals in the latest two months, best first, plus context for the header."""
    cfg, search = settings.pulse, settings.search
    window = months_back(today, cfg.median_months + 1)
    recent = set(months_back(today, 2))
    psm: dict[tuple[str, str], list[float]] = {}
    for r in db.all(f"SELECT town, flat_type, remaining_lease, price, sqm FROM hdb_resale WHERE month IN "
                    f"({','.join('?' * len(window))})", window):
        psm.setdefault(_peer(r), []).append(r["price"] / r["sqm"])
    medians = {k: statistics.median(v) for k, v in psm.items() if len(v) >= cfg.min_sales_for_median}
    quarter = db.scalar("SELECT MAX(quarter) FROM hdb_rent", default="")
    rents = {(r["town"], r["flat_type"]): r["median_rent"]
             for r in db.all("SELECT town, flat_type, median_rent FROM hdb_rent WHERE quarter=?", (quarter,))}
    alerted = {r["key"] for r in db.all("SELECT key FROM pulse_alerted")}
    deals = []
    rows = db.all(f"SELECT * FROM hdb_resale WHERE month IN ({','.join('?' * len(recent))})", sorted(recent))
    for r in rows:
        med = medians.get(_peer(r))
        if not med or not (search.budget_min_sgd <= r["price"] <= search.budget_max_sgd):
            continue
        if r["remaining_lease"] is not None and r["remaining_lease"] < search.min_remaining_lease_years:
            continue
        if search.areas and r["town"].title() not in [a.title() for a in search.areas]:
            continue
        discount = (1 - (r["price"] / r["sqm"]) / med) * 100
        rent = rents.get((r["town"], RENT_TYPE.get(r["flat_type"], "")))
        yld = rent * 12 / r["price"] * 100 if rent else None
        if discount < cfg.value_discount_pct and (yld or 0) < cfg.min_yield_pct:
            continue
        deals.append(Deal(r["key"], r["month"], r["town"], r["flat_type"], r["block"], r["street"], r["storey"],
                          r["sqm"], r["remaining_lease"], r["price"], med, discount, rent, yld,
                          new=r["key"] not in alerted))
    deals.sort(key=lambda d: -(max(d.discount_pct, 0) / cfg.value_discount_pct + (d.yield_pct or 0) / cfg.min_yield_pct))
    ctx = {"rows": len(rows), "latest_month": max((r["month"] for r in rows), default=""), "rent_quarter": quarter,
           "notable": len(deals), "new": sum(d.new for d in deals)}
    return deals, ctx


def mark_alerted(db: DB, deals: list[Deal]) -> None:
    stamp = datetime.now().isoformat(timespec="seconds")
    db.executemany("INSERT OR IGNORE INTO pulse_alerted VALUES (?, ?)", [(d.key, stamp) for d in deals])


# ------------------------------------------------------------ rendering
def _money(x: float) -> str:
    return f"S${x:,.0f}"


def _month_label(m: str) -> str:
    try:
        return datetime.strptime(m, "%Y-%m").strftime("%b %Y")
    except ValueError:
        return m or "no data"


def listings_url(d: Deal) -> str:
    """Flats for sale in the same block: a PropertyGuru search link for the reader (the bot never fetches it)."""
    return ("https://www.propertyguru.com.sg/property-for-sale?market=residential&listing_type=sale&freetext="
            + quote_plus(f"{d.block} {d.street.title()}"))


def deal_card(n: int, d: Deal, cfg) -> str:
    addr = f"Blk {d.block} {d.street.title()}"
    maps = "https://www.google.com/maps/search/?api=1&query=" + quote_plus(f"{addr} Singapore")
    if d.discount_pct >= cfg.value_discount_pct:
        value = f"🟢 {d.discount_pct:.0f}% below median ▼"
    else:
        value = f"⚪ {abs(d.discount_pct):.0f}% {'below' if d.discount_pct >= 0 else 'above'} median"
    rent = None
    if d.yield_pct is not None:
        mark = "🟢" if d.yield_pct >= cfg.min_yield_pct else "⚪"
        rent = f"📈 {mark} " + dot(f"Rent ~{_money(d.rent)}/mo", f"{d.yield_pct:.1f}% gross")
    lease = f"{d.remaining_lease:.0f}y lease left" if d.remaining_lease is not None else "lease unknown"
    return card(n, f"{d.flat_type.title()} · {d.town.title()}", listings_url(d), [
        "💰 " + dot(_money(d.price), f"S${d.psf:,.0f} psf", f"sold {_month_label(d.month)}"),
        f"📉 {esc(value)} · {esc(lease)}",
        rent or "",
        "🏠 " + dot(f"{d.sqm:.0f} sqm", f"floor {d.storey.lower()}") + f' · <a href="{esc_attr(maps)}">Map</a>',
    ], tag="NEW" if d.new else "", emoji="🏢", desc=addr)


def render(deals: list[Deal], ctx: dict, settings: Settings, *, title_note: str = "") -> list[str]:
    """Car-tracker layout: header, numbered linked cards, collapsed method note last; split between cards."""
    cfg = settings.pulse
    shown = deals[:cfg.max_items] if cfg.max_items > 0 else deals     # 0 = list them all
    summary = "\n".join([f"🆕 New: <b>{ctx['new']}</b>", f"📋 Notable in the last two months: <b>{len(deals)}</b>",
                         f"📦 Sales checked: <b>{ctx['rows']:,}</b> · newest {esc(_month_label(ctx['latest_month']))}"])
    cards = [deal_card(n, d, cfg) for n, d in enumerate(shown, 1)] or [
        f"⚪ <i>Nothing beats the bar right now: {cfg.value_discount_pct:g}% under the median or "
        f"{cfg.min_yield_pct:g}% gross yield.</i>"]
    links = ('🌐 <a href="https://data.gov.sg/datasets/d_8b84c4ee58e3cfc0ece0d773c8ca6abc/view">Resale data</a>  ·  '
             '<a href="https://services2.hdb.gov.sg/webapp/BB33RTIS/BB33PReslTrans.jsp">HDB resale prices</a>')
    method = note(
        f"How it's picked: completed HDB resale sales from data.gov.sg in the latest two months. A sale shows when its "
        f"price per sqm is {cfg.value_discount_pct:g}% or more under the {cfg.median_months} month median for the same "
        f"town, flat type and 10 year lease band, or HDB's median rent ({esc(ctx['rent_quarter'] or 'unknown')}) gives "
        f"{cfg.min_yield_pct:g}% gross or more. Budget S${settings.search.budget_min_sgd:,.0f} to "
        f"S${settings.search.budget_max_sgd:,.0f}, lease at least {settings.search.min_remaining_lease_years:g} years. "
        f"The name links to flats for sale in that block now. Renting out the whole flat needs the 5 year MOP "
        f"first. Gross yield is before costs. Not financial advice.")
    sub = title_note or datetime.now().strftime("%a %d %b %Y")
    return section_messages(REPORT_TITLES["pulse"], sub, [summary, *cards, links], method)
