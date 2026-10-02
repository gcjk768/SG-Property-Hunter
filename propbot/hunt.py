"""Listing hunt: claude -p (haiku, WebSearch/WebFetch) finds real properties for sale, one card each.

Reuses the discovery prompt and schema. The model's answers are checked here before posting: the
URL must be on listing_domains_allowed and not on never_fetch_domains, the price must be inside the
budget, and the listing must not have been posted before (key = URL without query). Cards built from
a search snippet only (the page was not opened) say so. No page is fetched by this module.
"""
from __future__ import annotations

import json
import logging
import re
import statistics
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote_plus, urlsplit

from .config import Settings
from .db import DB
from .pulse import SQFT_PER_SQM, months_back
from .render import REPORT_TITLES, card, dot, note, section_messages
from .telegram import esc, esc_attr
from .web import is_allowed_domain, is_never_fetch

log = logging.getLogger("propbot.hunt")

TITLE = "🏘"
TOOLS = ["WebSearch", "WebFetch"]
# a resale card must link to the unit's own listing (where you contact the agent to buy), e.g.
# edgeprop.sg/listing/..., propertyguru.com.sg/listing/..., commercialguru.com.sg/listing/...
LISTING_PATH = re.compile(r"/listings?/", re.I)
# new launches, BTO and new ECs are bought through the project (showflat booking or HDB), so its page is the link
PROJECT_PAGE_OK = {"condo_new_launch", "bto", "ec"}


@dataclass
class Listing:
    key: str
    category: str
    name: str
    address: str
    area: str
    price: float
    price_label: str
    sqft: float | None
    tenure: str
    lease_left: float | None
    floor: str
    rent: float | None
    url: str
    site: str
    gist: str
    snippet_only: bool
    vs_median_pct: float | None = None   # positive = below the HDB median for the town and lease band


def listing_key(url: str) -> str:
    p = urlsplit(url.strip())
    return "url:" + (p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")).lower()


def brief(settings: Settings, today: str, n: int) -> str:
    s = settings.search
    return (f"Run date: {today} (Asia/Singapore). Find {n + 3} real Singapore properties that are listed for sale "
            f"right now, each with its own listing page, priced S${s.budget_min_sgd:,.0f} to S${s.budget_max_sgd:,.0f}. "
            f"Favour what can be rented out soon or resold for a gain. Mix the categories in stdin. Return the "
            f"discovery object. Include new launch condos. For resale homes, EC resale and commercial units the url "
            f"must be that unit's own listing page where a buyer contacts the agent (for example "
            f"edgeprop.sg/listing/..., propertyguru.com.sg/listing/..., commercialguru.com.sg/listing/...), never a "
            f"project, condo directory or article page. For new launch condos, BTO and new ECs use the project's "
            f"official or listing page.")


def stdin_text(settings: Settings, db: DB) -> str:
    cats = {k: settings.categories[k].label for k in settings.enabled_categories()}
    known = [r["key"] for r in db.all("SELECT key FROM pulse_alerted WHERE key LIKE 'url:%' "
                                      "ORDER BY alerted_at DESC LIMIT 60")]
    return json.dumps({
        "categories": cats, "category_weights": settings.run.category_weights,
        "areas": settings.search.areas or "all of Singapore",
        "min_remaining_lease_years": settings.search.min_remaining_lease_years,
        "allowed_domains": settings.sources.listing_domains_allowed,
        "never_fetch_domains": settings.sources.never_fetch_domains,
        "known_items_do_not_repeat": known,
    }, indent=1)


def _sqft(c: dict) -> float | None:
    a = c.get("floor_area")
    if not a:
        return None
    return float(a) * (SQFT_PER_SQM if c.get("floor_area_unit") == "sqm" else 1)


def validate(cands: list[dict], settings: Settings, db: DB) -> tuple[list[Listing], list[str]]:
    """Keep only checkable, new, in-budget listings. Returns (listings, reasons for drops)."""
    s, src = settings.search, settings.sources
    posted = {r["key"] for r in db.all("SELECT key FROM pulse_alerted WHERE key LIKE 'url:%'")}
    out, dropped, seen = [], [], set()
    for c in cands:
        url = (c.get("url") or "").strip()
        price = c.get("price_sgd")
        why = None
        if not url.startswith("https://") and not url.startswith("http://"):
            why = "no link"
        elif is_never_fetch(url, src.never_fetch_domains) or not is_allowed_domain(url, src.listing_domains_allowed):
            why = f"site not allowed: {urlsplit(url).netloc}"
        elif not price or not (s.budget_min_sgd <= price <= s.budget_max_sgd):
            why = f"price {price} outside budget"
        elif c.get("price_label") == "transacted":
            why = "a past sale, not a listing"
        elif c.get("category_key") not in PROJECT_PAGE_OK and not LISTING_PATH.search(urlsplit(url).path):
            why = "not a unit listing page (project or article page)"
        elif c.get("category_key") not in settings.enabled_categories():
            why = f"category {c.get('category_key')} off"
        elif (c.get("remaining_lease_years") or 99) < s.min_remaining_lease_years:
            why = "lease too short"
        key = listing_key(url) if not why else ""
        if not why and (key in posted or key in seen):
            why = "already posted"
        if why:
            dropped.append(f"{c.get('name', '?')}: {why}")
            continue
        seen.add(key)
        out.append(Listing(key, c["category_key"], c.get("name") or "Unnamed", c.get("address") or "",
                           c.get("area") or "", float(price), c.get("price_label") or "asking", _sqft(c),
                           c.get("tenure") or "unknown", c.get("remaining_lease_years"), c.get("floor") or "",
                           c.get("asking_rent_sgd"), url, c.get("site") or urlsplit(url).netloc,
                           c.get("gist") or "", bool(c.get("from_snippet"))))
    return out, dropped


def hdb_context(listings: list[Listing], db: DB, today) -> None:
    """For HDB resale listings: how the asking psf compares with the town median (same lease band)."""
    window = months_back(today, 13)
    for x in listings:
        if x.category != "hdb_resale" or not x.sqft or not x.area:
            continue
        rows = db.all(f"SELECT price, sqm, remaining_lease FROM hdb_resale WHERE town=? AND month IN "
                      f"({','.join('?' * len(window))})", [x.area.upper(), *window])
        if x.lease_left is not None:
            band = int(x.lease_left // 10) * 10
            rows = [r for r in rows if r["remaining_lease"] is not None and int(r["remaining_lease"] // 10) * 10 == band]
        if len(rows) >= 5:
            med_psf = statistics.median(r["price"] / (r["sqm"] * SQFT_PER_SQM) for r in rows)
            x.vs_median_pct = (1 - (x.price / x.sqft) / med_psf) * 100


def listing_card(n: int, x: Listing, settings: Settings) -> str:
    label = settings.categories[x.category].label if x.category in settings.categories else x.category
    lines = [
        "💰 " + dot(f"S${x.price:,.0f} {x.price_label}", f"S${x.price / x.sqft:,.0f} psf" if x.sqft else None,
                   f"{x.sqft:,.0f} sqft" if x.sqft else "size unknown"),
        "🏠 " + dot(x.tenure, f"{x.lease_left:.0f}y lease left" if x.lease_left else None,
                   f"floor {x.floor}" if x.floor else None, x.area.title() or None),
    ]
    if x.vs_median_pct is not None:
        mark, word, arrow = ("🟢", "below", "▼") if x.vs_median_pct >= 0 else ("🔴", "above", "▲")
        lines.append(f"📉 {mark} {abs(x.vs_median_pct):.0f}% {word} recent {esc(x.area.title())} HDB sales {arrow}")
    if x.rent:
        lines.append("📈 " + dot(f"Asking rent S${x.rent:,.0f}/mo", f"{x.rent * 12 / x.price * 100:.1f}% gross"))
    if x.gist:
        lines.append("💡 " + esc(x.gist[:300]))
    maps = "https://www.google.com/maps/search/?api=1&query=" + quote_plus(f"{x.address or x.name} Singapore")
    buy = "Project page, book a showflat" if x.category in PROJECT_PAGE_OK else "Listing, contact the agent"
    lines.append(f'🔗 <a href="{esc_attr(x.url)}">{esc(buy)}</a> ({esc(x.site)})  ·  <a href="{esc_attr(maps)}">Map</a>')
    if x.snippet_only:
        lines.append("<i>From a search result, the page wasn't opened: check the listing.</i>")
    return card(n, x.name, x.url, lines, tag="NEW", emoji=TITLE, desc=label)


def messages(listings: list[Listing], run_note: str, dropped: int, settings: Settings) -> list[str]:
    """Car-tracker layout: header, summary, numbered linked cards, collapsed notes last."""
    summary = "\n".join([f"🆕 New for sale: <b>{len(listings)}</b>", f"🚫 Dropped by checks: <b>{dropped}</b>"])
    cards = [listing_card(n, x, settings) for n, x in enumerate(listings, 1)] or [
        "⚪ <i>Nothing new for sale that passed the checks this time.</i>"]
    detail = (f"Found by Claude (haiku) web search, then checked here: allowed site, budget, lease, a unit listing "
              f"page for resale (new launches, BTO and ECs link the project), not posted before. Asking prices, "
              f"not valuations. Not financial advice.")
    if run_note:
        detail += f" Search note: {run_note[:300]}"
    return section_messages(REPORT_TITLES["hunt"], datetime.now().strftime("%a %d %b %Y"), [summary, *cards],
                            note(esc(detail)))


def run(claude, settings: Settings, db: DB, today, n: int) -> tuple[list[Listing], list[str], str]:
    """One Claude call; returns (valid new listings, at most n), drop reasons and the model's run note."""
    cfg = settings.claude.discovery
    base = settings.prompts_dir
    res = claude.call(
        label="hunt", brief=brief(settings, today.isoformat(), n), stdin_text=stdin_text(settings, db),
        system_file=base / "discover_system.md",
        schema=json.loads((base / "discover_schema.json").read_text(encoding="utf-8")),
        allowed_tools=TOOLS, disallowed_tools=[t for t in settings.claude.no_tools if t not in TOOLS],
        max_turns=cfg.max_turns, timeout=cfg.timeout_seconds)
    data = res.structured or {}
    listings, dropped = validate(data.get("candidates") or [], settings, db)
    hdb_context(listings, db, today)
    return listings[:n], dropped, data.get("run_note") or ""


def mark_posted(db: DB, listings: list[Listing]) -> None:
    stamp = datetime.now().isoformat(timespec="seconds")
    db.executemany("INSERT OR IGNORE INTO pulse_alerted VALUES (?, ?)", [(x.key, stamp) for x in listings])
