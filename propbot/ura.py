"""Private residential transactions from the URA Data Service, picked the same way as the HDB pulse.

Needs URA_ACCESS_KEY (free registration). One token a day, then the four batches of
PMI_Resi_Transaction (every private residential sale caveat of the last five years), refreshed once a
day. A condo resale is notable when its price per sqm is value_discount_pct below the 12 month median
for the same project. The 5 year outlook is the project's own price trend with the lease decay table.
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
from .engine.projection import value_outlook
from .pulse import SQFT_PER_SQM, months_back, trend_cagr
from .ratelimit import RateLimiter
from .render import REPORT_TITLES, card, dot, note, outlook_line, section_messages
from .telegram import esc, esc_attr

log = logging.getLogger("propbot.ura")

TOKEN_URL = "https://eservice.ura.gov.sg/uraDataService/insertNewToken/v1"
DATA_URL = "https://eservice.ura.gov.sg/uraDataService/invokeUraDS/v1"
BATCHES = (1, 2, 3, 4)
SALE_TYPES = {"1": "new", "2": "sub", "3": "resale"}
TREND_YEARS = 4          # the dataset holds five years; four keeps a full window at both ends


@dataclass
class CondoDeal:
    key: str
    month: str
    project: str
    street: str
    district: str
    segment: str
    prop_type: str
    sqm: float
    floor: str
    lease_left: float | None     # None = freehold or unknown
    freehold: bool
    price: float
    median_psm: float
    discount_pct: float
    new: bool = True
    outlook_pct: float | None = None
    outlook_reason: str = ""
    mrt: str = ""

    @property
    def psf(self) -> float:
        return self.price / (self.sqm * SQFT_PER_SQM)


# ------------------------------------------------------------ parsing
def contract_month(mmyy: str) -> str:
    """'0925' -> '2025-09'."""
    m = re.fullmatch(r"(\d{2})(\d{2})", (mmyy or "").strip())
    return f"20{m.group(2)}-{m.group(1)}" if m else ""


def tenure_parts(text: str) -> tuple[bool, int | None, int | None]:
    """'99 yrs lease commencing from 2010' -> (False, 99, 2010); 'Freehold' -> (True, None, None)."""
    t = (text or "").lower()
    if "freehold" in t or t.startswith("9") and "999" in t:
        return True, None, None
    m = re.search(r"(\d+)\s*yrs?.*?(\d{4})", t)
    return (False, int(m.group(1)), int(m.group(2))) if m else (False, None, None)


def lease_left(tenure: str, when: date) -> tuple[bool, float | None]:
    fh, years, start = tenure_parts(tenure)
    if fh:
        return True, None
    if years is None or start is None:
        return False, None
    return False, float(years - (when.year - start))


def _get(http: httpx.Client, limiter: RateLimiter, url: str, headers: dict, params: dict | None = None) -> dict:
    limiter.acquire("ura")
    resp = http.get(url, headers=headers, params=params, timeout=120)
    limiter.log("ura", url.rsplit("/", 1)[-1], url=str(resp.request.url), status=resp.status_code,
                nbytes=len(resp.content))
    resp.raise_for_status()
    data = resp.json()
    if data.get("Status") != "Success":
        raise RuntimeError(f"URA error: {str(data.get('Message') or data)[:200]}")
    return data


def token(db: DB, settings: Settings, limiter: RateLimiter, http: httpx.Client, today: date) -> str | None:
    key = settings.secrets.ura_access_key
    if not key:
        return None
    row = db.one("SELECT token FROM ura_token WHERE day=?", (today.isoformat(),))
    if row:
        return row["token"]
    tok = _get(http, limiter, TOKEN_URL, {"AccessKey": key})["Result"]
    db.execute("DELETE FROM ura_token")
    db.execute("INSERT INTO ura_token VALUES (?, ?)", (today.isoformat(), tok))
    return tok


def refresh(db: DB, settings: Settings, limiter: RateLimiter, http: httpx.Client, today: date) -> int:
    """Fetch the four batches and store every transaction. Returns how many were new."""
    tok = token(db, settings, limiter, http, today)
    if not tok:
        return 0
    headers = {"AccessKey": settings.secrets.ura_access_key, "Token": tok}
    stamp = datetime.now().isoformat(timespec="seconds")
    before = db.scalar("SELECT COUNT(*) FROM ura_resi", default=0)
    for b in BATCHES:
        rows = []
        for proj in _get(http, limiter, DATA_URL, headers, {"service": "PMI_Resi_Transaction", "batch": b})["Result"]:
            for t in proj.get("transaction") or []:
                try:
                    price, sqm = float(t["price"]), float(t["area"])
                except (KeyError, TypeError, ValueError):
                    continue
                month = contract_month(t.get("contractDate", ""))
                fh, years, start = tenure_parts(t.get("tenure", ""))
                key = "|".join(str(x) for x in (proj.get("project"), proj.get("street"), month, t.get("floorRange"),
                                                 t.get("area"), t.get("price"), t.get("noOfUnits")))
                rows.append((key, proj.get("project", ""), proj.get("street", ""), str(t.get("district", "")),
                             proj.get("marketSegment", ""), t.get("propertyType", ""),
                             SALE_TYPES.get(str(t.get("typeOfSale")), "unknown"), month, sqm, t.get("floorRange", ""),
                             t.get("tenure", ""), int(fh), start, price, stamp))
        db.executemany("INSERT OR IGNORE INTO ura_resi VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return db.scalar("SELECT COUNT(*) FROM ura_resi", default=0) - before


# ------------------------------------------------------------ picking
def project_cagr(db: DB, project: str, today: date) -> tuple[float, int] | None:
    """Yearly price per sqm trend of one project over TREND_YEARS, or None with too few sales."""
    return trend_cagr(db, "ura_resi", "project=? AND sale_type IN ('resale','sub')", [project.upper()], today,
                      TREND_YEARS, min_n=3)


def condo_budget(settings: Settings) -> float:
    c = settings.categories.get("condo_resale")
    return (c and c.budget_max_sgd) or settings.search.budget_max_sgd


def picks(db: DB, settings: Settings, today: date) -> tuple[list[CondoDeal], dict]:
    cfg, search = settings.pulse, settings.search
    window = months_back(today, cfg.median_months + 1)
    recent = sorted(months_back(today, 2))
    marks = ",".join("?" * len(window))
    psm: dict[str, list[float]] = {}
    for r in db.all(f"SELECT project, price, sqm FROM ura_resi WHERE sale_type='resale' AND month IN ({marks})", window):
        psm.setdefault(r["project"], []).append(r["price"] / r["sqm"])
    medians = {k: statistics.median(v) for k, v in psm.items() if len(v) >= cfg.min_sales_for_median}
    alerted = {r["key"] for r in db.all("SELECT key FROM pulse_alerted")}
    lo, hi = search.budget_min_sgd, condo_budget(settings)
    deals, n_rows = [], 0
    for r in db.all(f"SELECT * FROM ura_resi WHERE sale_type='resale' AND month IN ({','.join('?' * len(recent))})", recent):
        n_rows += 1
        med = medians.get(r["project"])
        if not med or not (lo <= r["price"] <= hi):
            continue
        fh = bool(r["freehold"])
        left = None
        if not fh and r["lease_start"]:
            yrs = tenure_parts(r["tenure"])[1] or 99
            left = yrs - (today.year - r["lease_start"])
            if left < search.min_remaining_lease_years:
                continue
        discount = (1 - (r["price"] / r["sqm"]) / med) * 100
        if discount < cfg.value_discount_pct:
            continue
        deals.append(CondoDeal("ura:" + r["key"], r["month"], r["project"], r["street"], r["district"], r["segment"],
                               r["prop_type"], r["sqm"], r["floor"], left, fh, r["price"], med, discount,
                               new=("ura:" + r["key"]) not in alerted))
    deals.sort(key=lambda d: -d.discount_pct)
    ctx = {"rows": n_rows, "latest_month": db.scalar("SELECT MAX(month) FROM ura_resi", default="") or "",
           "notable": len(deals), "new": sum(d.new for d in deals)}
    return deals, ctx


def data_outlook(deals: list[CondoDeal], settings: Settings, db: DB, today: date, years: int = 5) -> None:
    a = settings.assumptions
    for d in deals:
        tc = project_cagr(db, d.project, today)
        if not tc:
            continue
        cagr, n = tc
        d.outlook_pct = value_outlook(cagr, d.lease_left, d.freehold, years, a.lease_decay, a.base_cagr_cap_pct)
        d.outlook_reason = f"{d.project.title()} resales {cagr:+.1f}%/yr over {TREND_YEARS}y ({n} sales)" + \
            ("" if d.freehold else ", lease decay applied")


def mark_alerted(db: DB, deals: list[CondoDeal]) -> None:
    stamp = datetime.now().isoformat(timespec="seconds")
    db.executemany("INSERT OR IGNORE INTO pulse_alerted VALUES (?, ?)", [(d.key, stamp) for d in deals])


# ------------------------------------------------------------ rendering
def listings_url(d: CondoDeal) -> str:
    return ("https://www.propertyguru.com.sg/property-for-sale?market=residential&listing_type=sale&freetext="
            + quote_plus(d.project.title()))


def _month_label(m: str) -> str:
    try:
        return datetime.strptime(m, "%Y-%m").strftime("%b %Y")
    except ValueError:
        return m or "no data"


def deal_card(n: int, d: CondoDeal, cfg) -> str:
    maps = "https://www.google.com/maps/search/?api=1&query=" + quote_plus(f"{d.project} {d.street} Singapore")
    lease = "freehold" if d.freehold else (f"{d.lease_left:.0f}y lease left" if d.lease_left is not None else "lease unknown")
    return card(n, d.project.title(), listings_url(d), [
        "💰 " + dot(f"S${d.price:,.0f}", f"S${d.psf:,.0f} psf", f"sold {_month_label(d.month)}"),
        f"📉 🟢 {d.discount_pct:.0f}% below the project median ▼ · {esc(lease)}",
        outlook_line(d.price, d.outlook_pct, 5, d.outlook_reason, source="data"),
        ("🚇 " + esc(d.mrt)) if d.mrt else "",
        "🏠 " + dot(f"{d.sqm:.0f} sqm", f"floor {d.floor}", f"D{d.district}", d.segment) + f' · <a href="{esc_attr(maps)}">Map</a>',
    ], tag="NEW" if d.new else "", emoji="🏙", desc=f"{d.prop_type} · {d.street.title()}")


def render(deals: list[CondoDeal], ctx: dict, settings: Settings) -> list[str]:
    cfg = settings.pulse
    summary = "\n".join([f"🆕 New: <b>{ctx['new']}</b>", f"📋 Notable in the last two months: <b>{len(deals)}</b>",
                         f"📦 Resales checked: <b>{ctx['rows']:,}</b> · newest {esc(_month_label(ctx['latest_month']))}"])
    cards = [deal_card(n, d, cfg) for n, d in enumerate(deals, 1)] or [
        f"⚪ <i>No condo resale beats the bar right now: {cfg.value_discount_pct:g}% under its project's median.</i>"]
    links = '🌐 <a href="https://www.ura.gov.sg/property-market-information/pmiResidentialTransactionSearch">URA transactions</a>'
    method = note(
        f"How it's picked: private residential resale caveats from the URA Data Service, latest two months. A sale shows "
        f"when its price per sqm is {cfg.value_discount_pct:g}% or more under the {cfg.median_months} month median of the "
        f"same project (at least {cfg.min_sales_for_median} sales). Budget S${settings.search.budget_min_sgd:,.0f} to "
        f"S${condo_budget(settings):,.0f}. The name "
        f"links to units for sale in that project now. 🔮 is a data estimate: the project's own resale trend over "
        f"{TREND_YEARS} years, continued 5 years with the lease decay table, capped at "
        f"{settings.assumptions.base_cagr_cap_pct:g}%/yr. Not a forecast, not financial advice.")
    return section_messages(REPORT_TITLES["condo"], datetime.now().strftime("%a %d %b %Y"), [summary, *cards, links], method)
