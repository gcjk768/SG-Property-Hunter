"""Shared pieces of the weekly PDF reports (condo and HDB): one row model, the lease and affordability
columns, and the PDF writer, so both reports have the same layout."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from xml.sax.saxutils import escape

from .config import Settings
from .db import DB

log = logging.getLogger("propbot.reportlib")

HEADERS = ["#", "Project / area", "Type", "Ask S$", "Size sqft", "Ask psf", "Benchmark psf (n)", "Ask vs bench",
           "Lease, 5y drag", "Change", "Can I?", "Seen", "Link"]
WIDTHS = [22, 132, 40, 56, 38, 38, 66, 46, 66, 40, 60, 34, 36]


@dataclass
class Row:
    name: str
    area: str
    type_label: str            # "3 BR", "4 Room"
    price: float
    sqft: float | None
    bench_psf: float | None
    bench_n: int
    reliable: bool
    tenure_txt: str            # "99 year, 80y left"
    drag: float | None         # lease decay over 5 years, percent (negative)
    change: str                # 🆕, 🟢▼5%, 🔴▲3% or empty
    afford: str
    seen: str                  # MM-DD of the last sighting
    url: str

    @property
    def ask_psf(self) -> float | None:
        return self.price / self.sqft if self.sqft else None

    @property
    def prem(self) -> float | None:
        return self.ask_psf / self.bench_psf - 1 if self.ask_psf and self.bench_psf else None


_PDF_WORDS = [("🆕", "NEW"), ("🟢▼", "DROP "), ("🔴▲", "UP "), ("✅", "YES"), ("⚠ short", "SHORT"), ("❌ not eligible", "NO (not eligible)"),
              ("◐ check rules", "CHECK"), ("❌", "GONE"), ("🟢", "DROP"), ("🔴", "UP"), ("⚠", "SHORT"), ("▼", "-"), ("▲", "+")]


def pdf_text(text: str) -> str:
    """The PDF's built-in fonts have no emoji: spell the markers out, and never print a black box for anything else."""
    for emoji, word in _PDF_WORDS:
        text = text.replace(emoji, word)
    return text.encode("cp1252", "replace").decode("cp1252")


def by_prem(rows: list[Row]) -> list[Row]:
    """Cheapest against its benchmark first; rows with no comparison go last."""
    return sorted(rows, key=lambda r: (r.prem is None, r.prem if r.prem is not None else 0))


class Checker:
    """The lease drag and 'can I afford it' columns, from the engine already behind /propanalyse."""

    def __init__(self, settings: Settings, db: DB, today: date):
        from .engine.card import rates_from_db
        from .rules.loader import Rules
        self.s, self.today = settings, today
        self.ready = settings.profile.gross_monthly_income > 0
        self.rules = Rules.load(settings.rules_dir / "sg_property_rules.yaml")
        self.rates = rates_from_db(db, settings, self.rules, None)

    def drag(self, tenure: str, lease_left: float | None, years: int = 5) -> float | None:
        from .engine.projection import value_outlook
        freehold = tenure in ("freehold", "999 year")
        if not freehold and lease_left is None:
            lease_left = 99 if tenure == "99 year" else None
        if not freehold and lease_left is None:
            return None
        return value_outlook(0.0, lease_left, freehold, years, self.s.assumptions.lease_decay, None)

    def afford(self, category: str, name: str, price: float, area: str, sqft: float | None, tenure: str,
               lease_left: float | None, flat_type: str = "") -> str:
        if not self.ready:
            return "fill Profile.md"
        from .engine.card import build_card
        from .models import Candidate
        try:
            cand = Candidate(category_key=category, name=name, price=price, area=area, size_sqft=sqft, tenure=tenure,
                             remaining_lease=lease_left, flat_type=flat_type, evidence_level="typed")
            card = build_card(cand, self.s, self.rules, self.rates, self.today, with_fair_price=False)
        except Exception as exc:       # one odd listing must not stop the report
            log.warning("affordability failed for %s: %s", name, exc)
            return "n/a"
        e = card.elig.eligible
        if e is False:
            return "❌ not eligible"
        if card.fin.cash_short > 0:
            return f"⚠ short {card.fin.cash_short / 1000:,.0f}k"
        return "✅" if e is True else "◐ check rules"


def lease_text(tenure: str, lease_left: float | None, drag: float | None) -> str:
    bits = [tenure if tenure != "unknown" else "tenure ?"]
    if lease_left:
        bits[0] += f", {lease_left:.0f}y left"
    if drag is not None:
        bits.append(f"{drag:+.1f}%")
    return " / ".join(bits)


def build_pdf(path, title: str, intro: list[str], note: str, changes: list[tuple[str, str, str]],
              parts: list[tuple[str | None, list[tuple[str, str, list[Row]]]]]) -> None:
    """parts = [(part title, [(section title, note, rows)])]; each section starts on its own page."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ss = getSampleStyleSheet()
    sm = ParagraphStyle("sm", parent=ss["BodyText"], fontSize=7.5, leading=9)
    P = lambda t, st=sm: Paragraph(pdf_text(t), st)

    def row(i, r: Row):
        p = "n/a" if r.prem is None else f"{r.prem:+.0%}"
        col = "#6b7280" if r.prem is None else ("#1a7f37" if r.prem <= 0 else "#b42318")
        return [i, P(f"<b>{escape(r.name)}</b><br/>{escape(r.area.title())}"), r.type_label, f"{r.price:,.0f}",
                f"{r.sqft:,.0f}" if r.sqft else "?", f"{r.ask_psf:,.0f}" if r.ask_psf else "?",
                f"{r.bench_psf:,.0f} ({r.bench_n})" if r.bench_psf else "none",
                P(f"<font color='{col}'><b>{p}</b></font>"), P(escape(r.tenure_txt)), pdf_text(r.change), P(escape(r.afford)),
                r.seen, P(f"<link href='{escape(r.url, {chr(39): '&apos;'})}' color='blue'>open</link>")]

    def tbl(rows):
        t = Table([HEADERS] + [row(i + 1, r) for i, r in enumerate(rows)], repeatRows=1, colWidths=WIDTHS)
        t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef7")),
                               ("GRID", (0, 0), (-1, -1), .25, colors.grey), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")])]))
        return t

    S = [P(escape(title), ss["Title"])] + [P(t, ss["BodyText"]) for t in intro] + [P(note)]
    if changes:
        S += [Spacer(1, 6), P("Changes since the last report", ss["Heading2"])]
        ct = Table([[pdf_text(m), P(escape(n)), P(escape(d))] for m, n, d in changes], colWidths=[30, 260, 420])
        ct.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7.5), ("GRID", (0, 0), (-1, -1), .25, colors.grey)]))
        S.append(ct)
    for part, sections in parts:
        if part:
            S += [PageBreak(), P(escape(part), ss["Title"])]
        for k, (head, text, rows) in enumerate(sections):
            if k or not part:
                S.append(PageBreak())
            S += [P(escape(head), ss["Heading2"])] + ([P(text), Spacer(1, 4)] if text else []) + [tbl(rows)]
    SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24,
                      bottomMargin=24).build(S)
