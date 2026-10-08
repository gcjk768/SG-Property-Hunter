"""Weekly condo report (resale and new launch): the HDB report layout, built for private condos.

One Claude web-search call per segment returns listings plus each project's recent psf (the benchmark) and a
status check on last week's listings. Code checks the answers like the hunt does (allowed site, budget, lease,
unit listing page), remembers every listing in the tracker (first seen, price moves, gone), ranks asking psf
against the benchmark and writes the PDF. The benchmark is Claude's web-search estimate, not URA data.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from urllib.parse import urlsplit

from . import tracker
from .config import Settings
from .db import DB
from .hunt import LISTING_PATH, budget_max, listing_key
from .pulse import SQFT_PER_SQM
from .render import dot
from .reportlib import Checker, Row, build_pdf, by_prem, lease_text
from .telegram import esc, esc_attr
from .web import is_allowed_domain, is_never_fetch

log = logging.getLogger("propbot.condo")

SEGMENTS = {"resale": ("condo_resale", "Resale condo"), "new_launch": ("condo_new_launch", "New launch condo")}
TOOLS = ["WebSearch", "WebFetch"]
MIN_SALES = 5         # benchmark must rest on this many transactions, from a named source, to count as reliable
MAX_GAP = 0.25        # and the ask must be within 25% of it, else it is a different unit type or a typo


@dataclass
class Condo:
    segment: str
    key: str
    name: str
    area: str
    bedrooms: int | None
    tenure: str
    lease_left: float | None
    price: float
    sqft: float | None
    url: str
    site: str
    snippet_only: bool
    bench_psf: float | None
    bench_n: int
    bench_note: str

    @property
    def ask_psf(self) -> float | None:
        return self.price / self.sqft if self.sqft else None

    @property
    def prem(self) -> float | None:
        return self.ask_psf / self.bench_psf - 1 if self.ask_psf and self.bench_psf else None

    @property
    def reliable(self) -> bool:
        return (self.prem is not None and self.bench_n >= MIN_SALES and bool(self.bench_note.strip())
                and abs(self.prem) <= MAX_GAP)


def brief(segment: str, n: int, today: str) -> str:
    return (f"Run date: {today} (Asia/Singapore). Segment: {segment}. Find {n} real condo "
            f"{'listings for sale, each with its own listing page' if segment == 'resale' else 'new launch projects'} "
            f"priced inside the budget range in stdin, with the benchmark psf for each, and report the status of every "
            f"listing in recheck_previous_listings. Return the object the schema describes.")


def stdin_text(settings: Settings, segment: str, previous: list[dict]) -> str:
    cat = SEGMENTS[segment][0]
    return json.dumps({
        "segment": segment, "budget_min_sgd": settings.search.budget_min_sgd,
        "budget_max_sgd": budget_max(settings, cat),
        "min_remaining_lease_years": settings.search.min_remaining_lease_years,
        "allowed_domains": settings.sources.listing_domains_allowed,
        "never_fetch_domains": settings.sources.never_fetch_domains,
        "recheck_previous_listings": previous}, indent=1)


def validate(cands: list[dict], settings: Settings, segment: str,
             gone: frozenset[str] = frozenset()) -> tuple[list[Condo], list[str]]:
    s, src = settings.search, settings.sources
    hi = budget_max(settings, SEGMENTS[segment][0])
    out, dropped, seen = [], [], set()
    for c in cands:
        url, price = (c.get("url") or "").strip(), c.get("price_sgd")
        why = None
        if not url.startswith(("https://", "http://")):
            why = "no link"
        elif is_never_fetch(url, src.never_fetch_domains) or not is_allowed_domain(url, src.listing_domains_allowed):
            why = f"site not allowed: {urlsplit(url).netloc}"
        elif not price or not (s.budget_min_sgd <= price <= hi):
            why = f"price {price} outside budget"
        elif segment == "resale" and not LISTING_PATH.search(urlsplit(url).path):
            why = "not a unit listing page"
        elif (c.get("remaining_lease_years") or 999) < s.min_remaining_lease_years:
            why = "lease too short"
        key = listing_key(url) if not why else ""
        if not why and key in gone:
            why = "marked gone"
        elif not why and key in seen:
            why = "duplicate"
        if why:
            dropped.append(f"{c.get('name', '?')}: {why}")
            continue
        seen.add(key)
        area = c.get("floor_area")
        sqft = float(area) * (SQFT_PER_SQM if c.get("floor_area_unit") == "sqm" else 1) if area else None
        bench = c.get("benchmark_psf")
        out.append(Condo(segment, key, c.get("name") or "Unnamed", c.get("area") or c.get("district") or "",
                         c.get("bedrooms"), c.get("tenure") or "unknown", c.get("remaining_lease_years"),
                         float(price), sqft, url, c.get("site") or urlsplit(url).netloc,
                         bool(c.get("from_snippet")), float(bench) if bench and 300 <= bench <= 10000 else None,
                         int(c.get("benchmark_sales_count") or 0), c.get("benchmark_note") or ""))
    return out, dropped


def split(items: list[Condo]) -> tuple[list[Condo], list[Condo]]:
    good = [x for x in items if x.reliable]
    return by_prem(good), by_prem([x for x in items if not x.reliable])


def run(claude, settings: Settings, db: DB, segment: str, n: int, today: date) -> tuple[list[Condo], list[str], str]:
    """One Claude call. Records what it found and applies its status checks on last week's listings."""
    cfg, base = settings.claude.discovery, settings.prompts_dir
    kind = SEGMENTS[segment][0]
    res = claude.call(
        label=f"condo-{segment}", brief=brief(segment, n, today.isoformat()),
        stdin_text=stdin_text(settings, segment, tracker.to_recheck(db, kind, today)),
        system_file=base / "condo_report_system.md",
        schema=json.loads((base / "condo_report_schema.json").read_text(encoding="utf-8")),
        allowed_tools=TOOLS, disallowed_tools=[t for t in settings.claude.no_tools if t not in TOOLS],
        max_turns=cfg.max_turns, timeout=cfg.timeout_seconds)
    data = res.structured or {}
    tracker.apply_checks(db, data.get("previous_status") or [], today)
    items, dropped = validate(data.get("candidates") or [], settings, segment, frozenset(tracker.gone_keys(db)))
    tracker.record(db, kind, [SimpleNamespace(key=x.key, name=x.name, area=x.area, url=x.url, bedrooms=x.bedrooms,
                                              tenure=x.tenure, lease_left=x.lease_left, sqft=x.sqft, price=x.price)
                              for x in items], today)
    return items, dropped, data.get("run_note") or ""


# ------------------------------------------------------------ rows, summary, PDF
def to_rows(db: DB, items: list[Condo], segment: str, chk: Checker, today: date) -> list[tuple[Condo, Row]]:
    cat = SEGMENTS[segment][0]
    out = []
    for x in items:
        t = db.one("SELECT * FROM report_listing WHERE key=?", [x.key])
        drag = chk.drag(x.tenure, x.lease_left)
        out.append((x, Row(x.name, x.area, f"{x.bedrooms} BR" if x.bedrooms else "?", x.price, x.sqft, x.bench_psf,
                           x.bench_n, x.reliable, lease_text(x.tenure, x.lease_left, drag), drag,
                           tracker.change_label(t, today) if t else "",
                           chk.afford(cat, x.name, x.price, x.area, x.sqft, x.tenure, x.lease_left),
                           (t["last_seen"] if t else today.isoformat())[5:], x.url)))
    return out


def money(v: float) -> str:
    return f"S${v / 1e6:.2f}M" if v >= 1e6 else f"S${v / 1e3:,.0f}k"


def summary(rows: dict[str, list[Row]], changes: dict[str, list[tuple[str, str, str]]], today: date, top: int = 5) -> str:
    lines = [f"🏙 <b>CONDO WEEKLY REPORT</b> · {today:%a %d %b %Y}"]
    for seg, (_, label) in SEGMENTS.items():
        rs = rows.get(seg, [])
        good = by_prem([r for r in rs if r.reliable])
        ch = changes.get(seg, [])
        lines += ["", f"🏘 <b>{esc(label)}</b> · {len(rs)} found, {len(good)} ranked"]
        marks = [f"🆕 {sum(r.change == '🆕' for r in rs)}", f"🟢 {sum(m == '🟢' for m, *_ in ch)} drops",
                 f"🔴 {sum(m == '🔴' for m, *_ in ch)} rises", f"❌ {sum(m == '❌' for m, *_ in ch)} gone"]
        lines.append("<i>" + " · ".join(marks) + "</i>")
        for i, r in enumerate((good or by_prem(rs))[:top], 1):
            mark = "" if r.prem is None else (" 🟢" if r.prem <= 0 else " 🔴") + f" <i>{r.prem:+.0%} vs project</i>"
            bits = dot(r.type_label if r.type_label != "?" else None, money(r.price),
                       f"{r.ask_psf:,.0f} psf" if r.ask_psf else None, r.area.title() or None, r.afford.split(" ")[0] if r.afford[:1] in "✅⚠❌◐" else None)
            lines.append(f'{i}. <a href="{esc_attr(r.url)}">{esc(r.name)}</a> · {bits}{mark}')
    lines += ["", "<blockquote expandable>" + esc(
        "Found by Claude web search; the project psf is its estimate from recent transactions, not URA data. "
        "Availability is only as good as the last page check (PropertyGuru blocks automated checks); tell me with "
        "/propgone <name> what you find sold. ✅ you can afford it, ⚠ cash short, ❌ not eligible. "
        "Full ranking in the PDF. Not financial advice.") + "</blockquote>"]
    return "\n".join(lines)


def build(path, rows: dict[str, list[tuple[Condo, Row]]], changes: dict, today: date) -> None:
    total = sum(len(v) for v in rows.values())
    intro = [f"Week of {today:%d %b %Y}. {total} listings found by Claude web search: resale condos and new launches.",
             "<b>Availability is only as good as the last page check.</b> PropertyGuru blocks automated checks (Cloudflare 403), so "
             "most listings could not be re-opened. 'Seen' is the last day a search or page check confirmed the listing; listings not "
             "seen for 21 days drop out, and /propgone removes one you found sold. Open the link before relying on any row."]
    note = ("<b>How ranked:</b> asking psf vs the project's recent transacted psf (Claude's web-search estimate from a named source, "
            f"not a URA download), lowest first (green = below the project). Ranked shortlist = {MIN_SALES}+ transactions, a named source, and the "
            f"ask within {MAX_GAP:.0%} of the benchmark. <b>Lease, 5y drag</b> = tenure, lease left and the value the lease decay table "
            "removes over 5 years. <b>Change</b> = new or price move since the last report. <b>Can I?</b> = your Profile.md run through the "
            "eligibility and financing engine: ✅ eligible and cash covers it, ⚠ cash short (in S$), ❌ not eligible. BR = bedrooms (4 = 4 or more).")
    parts, allchanges = [], []
    for seg, (_, label) in SEGMENTS.items():
        pairs = rows.get(seg, [])
        rs = [r for _, r in pairs]
        good = by_prem([r for r in rs if r.reliable])
        rest = by_prem([r for r in rs if not r.reliable])
        secs = [(f"Ranked shortlist ({len(good)})", "", good),
                (f"Not reliably comparable ({len(rest)})",
                 f"Fewer than {MIN_SALES} transactions or no named source behind the project psf, no size or benchmark found, "
                 f"or the ask is more than {MAX_GAP:.0%} away from it. Sorted by premium, for reference only.", rest)]
        for br, name in ((3, "3 BR"), (2, "2 BR"), (1, "1 BR"), (4, "4 BR+")):
            g = by_prem([r for (x, r) in pairs if (min(x.bedrooms, 4) if x.bedrooms else None) == br])
            secs.append((f"Overall {name} ({len(g)})",
                         "Shortlist and not-reliably-comparable combined, ranked by ask vs project (lowest first).", g))
        g = by_prem([r for (x, r) in pairs if not x.bedrooms])
        if g:
            secs.append((f"Overall bedrooms not stated ({len(g)})", "", g))
        parts.append((label, secs))
        allchanges += [(m, f"{label}: {n}", d) for m, n, d in changes.get(seg, [])]
    build_pdf(path, "Condo listings seen this week, ranked", intro, note, allchanges, parts)


def weekly(claude, settings: Settings, db: DB, today: date, pdf_path, notify=lambda *a: None) -> tuple[str, int]:
    """Run both segments, write the PDF, return (Telegram summary, listings found). Progress and errors go to notify."""
    chk = Checker(settings, db, today)
    cfg = settings.condo_report
    rows, changes, found = {}, {}, 0
    for seg, n in (("resale", cfg.resale_count), ("new_launch", cfg.new_launch_count)):
        try:
            items, dropped, _ = run(claude, settings, db, seg, n, today)
            notify(seg, f"{len(items)} listings, {len(dropped)} dropped" + (f" ({'; '.join(dropped)[:300]})" if dropped else ""))
        except Exception as exc:         # a Claude limit or a bad answer for one segment must not lose the other
            notify(seg, exc)
            items = []
        rows[seg] = to_rows(db, items, seg, chk, today)
        changes[seg] = tracker.changes(db, SEGMENTS[seg][0], today)
        found += len(items)
    if not found:
        return "", 0
    build(pdf_path, rows, changes, today)
    return summary({s: [r for _, r in v] for s, v in rows.items()}, changes, today), found
