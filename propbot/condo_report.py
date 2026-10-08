"""Weekly condo report (resale and new launch): the HDB report layout, built for private condos.

One Claude web-search call per segment returns listings plus each project's recent psf (the benchmark).
Code checks the answers like the hunt does (allowed site, budget, lease, unit listing page), ranks asking psf
against the benchmark and writes the PDF. The benchmark is Claude's web-search estimate, not URA data.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

from .config import Settings
from .hunt import LISTING_PATH, budget_max, listing_key
from .pulse import SQFT_PER_SQM
from .render import dot
from .telegram import esc, esc_attr
from .web import is_allowed_domain, is_never_fetch

log = logging.getLogger("propbot.condo")

SEGMENTS = {"resale": ("condo_resale", "Resale condo"), "new_launch": ("condo_new_launch", "New launch condo")}
TOOLS = ["WebSearch", "WebFetch"]
MIN_SALES = 3         # benchmark must rest on this many transactions to count as reliable
MAX_GAP = 0.25        # and the ask must be within 25% of it, else it is a different unit type or a typo


@dataclass
class Condo:
    segment: str
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
        """Ask psf over benchmark psf minus 1 (negative = cheaper than the project); None when not comparable."""
        return self.ask_psf / self.bench_psf - 1 if self.ask_psf and self.bench_psf else None

    @property
    def reliable(self) -> bool:
        return self.prem is not None and self.bench_n >= MIN_SALES and abs(self.prem) <= MAX_GAP


def brief(segment: str, n: int, today: str) -> str:
    return (f"Run date: {today} (Asia/Singapore). Segment: {segment}. Find {n} real condo "
            f"{'listings for sale, each with its own listing page' if segment == 'resale' else 'new launch projects'} "
            f"priced inside the budget range in stdin, with the benchmark psf for each. Return the object the schema describes.")


def stdin_text(settings: Settings, segment: str) -> str:
    cat = SEGMENTS[segment][0]
    return json.dumps({
        "segment": segment, "budget_min_sgd": settings.search.budget_min_sgd,
        "budget_max_sgd": budget_max(settings, cat),
        "min_remaining_lease_years": settings.search.min_remaining_lease_years,
        "allowed_domains": settings.sources.listing_domains_allowed,
        "never_fetch_domains": settings.sources.never_fetch_domains}, indent=1)


def validate(cands: list[dict], settings: Settings, segment: str) -> tuple[list[Condo], list[str]]:
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
        if not why and key in seen:
            why = "duplicate"
        if why:
            dropped.append(f"{c.get('name', '?')}: {why}")
            continue
        seen.add(key)
        area = c.get("floor_area")
        sqft = float(area) * (SQFT_PER_SQM if c.get("floor_area_unit") == "sqm" else 1) if area else None
        bench = c.get("benchmark_psf")
        out.append(Condo(segment, c.get("name") or "Unnamed", c.get("area") or c.get("district") or "",
                         c.get("bedrooms"), c.get("tenure") or "unknown", c.get("remaining_lease_years"),
                         float(price), sqft, url, c.get("site") or urlsplit(url).netloc,
                         bool(c.get("from_snippet")), float(bench) if bench and 300 <= bench <= 10000 else None,
                         int(c.get("benchmark_sales_count") or 0), c.get("benchmark_note") or ""))
    return out, dropped


def by_prem(items: list[Condo]) -> list[Condo]:
    """Cheapest against its project first; listings with no comparison go last."""
    return sorted(items, key=lambda x: (x.prem is None, x.prem if x.prem is not None else 0))


def split(items: list[Condo]) -> tuple[list[Condo], list[Condo]]:
    return by_prem([x for x in items if x.reliable]), by_prem([x for x in items if not x.reliable])


def run(claude, settings: Settings, segment: str, n: int, today: date) -> tuple[list[Condo], list[str], str]:
    cfg, base = settings.claude.discovery, settings.prompts_dir
    res = claude.call(
        label=f"condo-{segment}", brief=brief(segment, n, today.isoformat()), stdin_text=stdin_text(settings, segment),
        system_file=base / "condo_report_system.md",
        schema=json.loads((base / "condo_report_schema.json").read_text(encoding="utf-8")),
        allowed_tools=TOOLS, disallowed_tools=[t for t in settings.claude.no_tools if t not in TOOLS],
        max_turns=cfg.max_turns, timeout=cfg.timeout_seconds)
    data = res.structured or {}
    items, dropped = validate(data.get("candidates") or [], settings, segment)
    return items, dropped, data.get("run_note") or ""


# ------------------------------------------------------------ Telegram summary
def money(v: float) -> str:
    return f"S${v / 1e6:.2f}M" if v >= 1e6 else f"S${v / 1e3:,.0f}k"


def summary(data: dict[str, list[Condo]], today: date, top: int = 5) -> str:
    lines = [f"🏙 <b>CONDO WEEKLY REPORT</b> · {today:%a %d %b %Y}"]
    for seg, (_, label) in SEGMENTS.items():
        good, rest = split(data.get(seg, []))
        lines += ["", f"🏘 <b>{esc(label)}</b> · {len(good) + len(rest)} found, {len(good)} ranked"]
        for i, x in enumerate(good[:top] or rest[:top], 1):
            mark = "" if x.prem is None else (" 🟢" if x.prem <= 0 else " 🔴") + f" <i>{x.prem:+.0%} vs project</i>"
            bits = dot(f"{x.bedrooms}BR" if x.bedrooms else None, money(x.price),
                       f"{x.ask_psf:,.0f} psf" if x.ask_psf else None, x.area.title() or None)
            lines.append(f'{i}. <a href="{esc_attr(x.url)}">{esc(x.name)}</a> · {bits}{mark}')
    lines += ["", "<blockquote expandable>" + esc(
        "Found by Claude web search; the project psf is its estimate from recent transactions, not URA data. "
        "Availability is not verified (PropertyGuru blocks automated checks). Full ranking in the PDF. "
        "Not financial advice.") + "</blockquote>"]
    return "\n".join(lines)


# ------------------------------------------------------------ PDF
def build_pdf(path, data: dict[str, list[Condo]], today: date) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ss = getSampleStyleSheet()
    sm = ParagraphStyle("sm", parent=ss["BodyText"], fontSize=7.5, leading=9)
    H = lambda t: Paragraph(t, ss["Heading2"])
    hdr = ["#", "Project / area", "BR", "Ask S$", "Size sqft", "Ask psf", "Project psf (sales)", "Ask vs project", "Tenure", "Link"]

    def row(i, x: Condo):
        p = "n/a" if x.prem is None else f"{x.prem:+.0%}"
        col = "#6b7280" if x.prem is None else ("#1a7f37" if x.prem <= 0 else "#b42318")
        tenure = x.tenure + (f", {x.lease_left:.0f}y left" if x.lease_left else "")
        return [i, Paragraph(f"<b>{esc(x.name)}</b><br/>{esc(x.area.title())}", sm), x.bedrooms or "?", f"{x.price:,.0f}",
                f"{x.sqft:,.0f}" if x.sqft else "?", f"{x.ask_psf:,.0f}" if x.ask_psf else "?",
                f"{x.bench_psf:,.0f} ({x.bench_n})" if x.bench_psf else "none",
                Paragraph(f"<font color='{col}'><b>{p}</b></font>", sm), Paragraph(esc(tenure), sm),
                Paragraph(f"<link href='{esc_attr(x.url)}' color='blue'>open</link>", sm)]

    def tbl(items):
        t = Table([hdr] + [row(i + 1, x) for i, x in enumerate(items)], repeatRows=1,
                  colWidths=[22, 170, 28, 62, 50, 48, 85, 62, 80, 36])
        t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7.5), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef7")),
                               ("GRID", (0, 0), (-1, -1), .25, colors.grey), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")])]))
        return t

    total = sum(len(v) for v in data.values())
    S = [Paragraph("Condo listings seen this week, ranked", ss["Title"]),
         Paragraph(f"Week of {today:%d %b %Y}. {total} listings found by Claude web search: resale condos and new launches.", ss["BodyText"]),
         Paragraph("<b>Availability is NOT verified.</b> PropertyGuru blocks automated checks (Cloudflare 403), so listings were not re-checked. "
                   "Open the link, or ask the agent, before relying on any row.", ss["BodyText"]),
         Paragraph("<b>How ranked:</b> asking psf vs the project's recent transacted psf (Claude's web-search estimate from EdgeProp, URA and "
                   "similar pages, not a URA download), lowest first (green = below the project). Ranked shortlist = the estimate rests on "
                   f"{MIN_SALES}+ transactions and the ask is within {MAX_GAP:.0%} of it. New launches with no resale yet are compared with the "
                   "closest comparable projects, so treat them as a shortlist filter, not a valuation. BR = bedrooms (4 = 4 or more).", sm)]
    for seg, (_, label) in SEGMENTS.items():
        items = data.get(seg, [])
        good, rest = split(items)
        S += [PageBreak(), Paragraph(label, ss["Title"]), H(f"Ranked shortlist ({len(good)})"), tbl(good),
              PageBreak(), H(f"Not reliably comparable ({len(rest)})"),
              Paragraph("Fewer than 3 transactions behind the project psf, no size or benchmark found, or the ask is more than 25% away from it. "
                        "Sorted by premium, for reference only.", sm), Spacer(1, 4), tbl(rest)]
        for br, name in ((3, "3 BR"), (2, "2 BR"), (1, "1 BR"), (4, "4 BR+")):
            g = by_prem([x for x in items if (min(x.bedrooms, 4) if x.bedrooms else None) == br])
            S += [PageBreak(), H(f"Overall {name} ({len(g)})"),
                  Paragraph("Shortlist and not-reliably-comparable listings combined, ranked by ask vs project (lowest first).", sm),
                  Spacer(1, 4), tbl(g)]
        g = by_prem([x for x in items if not x.bedrooms])
        if g:
            S += [PageBreak(), H(f"Overall bedrooms not stated ({len(g)})"), tbl(g)]
    SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24).build(S)
