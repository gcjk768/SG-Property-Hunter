"""One template per item type: listing (also BTO), outlook and rules_update.

Formatting rules: HTML parse mode with every value escaped; S$ with thousands separators and
no decimals; percentages with one decimal; negative money as "loss of S$x"; negative
percentages as "minus x%" in prose and "dep x%" in the year table; "unknown" for a missing
figure; never None; no dashes in prose; every message under 4096 characters.
"""
from __future__ import annotations

import re
from typing import Iterable

from .config import Settings
from .engine.card import Card
from .engine.categories import info
from .telegram import esc, esc_attr

DISCLAIMER = "Model estimate, not financial advice. Rules as of {date}."
_DASHES = "‐‑‒–—―−﹘﹣－"
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
BANNED_WORDS = re.compile(r"\b(guaranteed?|will rise|cannot lose|can't lose|sure to|risk free|risk-free)\b", re.I)
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


# ------------------------------------------------------------ formatting
def clean(text: str | None) -> str:
    """Remove dashes from outside text: 'Blk 5-12' becomes 'Blk 5 12', 'a - b' becomes 'a, b'."""
    if not text:
        return ""
    s = str(text)
    for ch in _DASHES:
        s = s.replace(ch, "-")
    s = re.sub(r"\s+-+\s+", ", ", s)
    s = re.sub(r"(?<=\w)-+(?=\w)", " ", s)
    s = s.replace("-", " ")
    return re.sub(r"\s{2,}", " ", s).strip()


def num(x: float | None, decimals: int = 0) -> str:
    if x is None:
        return "unknown"
    return f"{x:,.{decimals}f}"


def money(x: float | None) -> str:
    if x is None:
        return "unknown"
    if round(x) < 0:
        return f"loss of S${abs(x):,.0f}"
    return f"S${abs(x):,.0f}"


def money_round(x: float | None, to: int = 1000) -> str:
    if x is None:
        return "unknown"
    return money(round(x / to) * to)


def pct(x: float | None, decimals: int = 1) -> str:
    if x is None:
        return "unknown"
    if round(x, decimals) < 0:
        return f"minus {abs(x):.{decimals}f}%"
    return f"{abs(x):.{decimals}f}%"


def kfmt(x: float | None) -> str:
    if x is None:
        return "unknown"
    return f"{round(x / 1000):,.0f}k"


def table_change(p: float) -> str:
    return f"+{p:.1f}%" if round(p, 1) >= 0 else f"dep {abs(p):.1f}%"


def table_sold(x: float | None, allowed: bool) -> str:
    if not allowed:
        return "locked"
    if x is None:
        return "unknown"
    k = round(x / 1000)
    return f"+{k:,.0f}k" if k >= 0 else f"loss {abs(k):,.0f}k"


def table_equity(x: float) -> str:
    k = round(x / 1000)
    return f"{k:,.0f}k" if k >= 0 else f"neg {abs(k):,.0f}k"


def prose_without_exempt(text: str) -> str:
    """Text a reader reads, minus tags, link targets and ISO dates (the dates the brief uses)."""
    t = re.sub(r'href="[^"]*"', "", text)
    t = re.sub(r"<[^>]+>", "", t)
    return _ISO_DATE.sub("", t)


def has_dash(text: str) -> bool:
    t = prose_without_exempt(text)
    return any(ch in t for ch in "-" + _DASHES)


def join(parts: Iterable[str], sep: str = " · ") -> str:
    return sep.join(p for p in parts if p)


# ------------------------------------------------------------ listing
def _eligibility_line(card: Card) -> str:
    e = card.elig
    notes = [clean(n) for n in e.notes]
    loan_word = "HDB or bank loan" if e.hdb_loan_allowed else ""
    cat = info(card.cand.category_key)
    if cat.loan == "bank" or (cat.loan == "hdb_or_bank" and not e.hdb_loan_allowed):
        loan_word = "bank loan only"
    if e.eligible is True:
        return join(["you can buy this", *notes, loan_word], ", ")
    if e.eligible == "conditional":
        return join(["conditional", *[clean(r) for r in e.reasons if r], *notes], ", ")
    reasons = [clean(r) for r in e.reasons[:2]]
    change = clean(e.what_would_change_it[0]) if e.what_would_change_it else ""
    return join(["Not eligible yet", *reasons, change], ", ")


def _loan_type(card: Card) -> str:
    o = card.fin.option
    if o.lender == "HDB":
        return "HDB loan"
    if o.planning_figure:
        return "bank, planning LTV"
    return "bank"


def render_listing(card: Card, settings: Settings, *, rank: int = 1, of: int = 1, run_time: str = "") -> str:
    c, fin, d, proj, v = card.cand, card.fin, card.duties, card.proj, card.verdict
    cat_cfg = settings.categories.get(c.category_key)
    opt = fin.option
    lines: list[str] = []
    area = clean(c.area) or "Singapore"
    lines.append(f"{esc(cat_cfg.tag if cat_cfg else '#' + c.category_key)} {esc(cat_cfg.label if cat_cfg else c.category_key)}"
                 f" · {rank} of {of} · {esc(area)}{esc(clean(c.repeat_note))}")
    lines.append(f"<b>{esc(clean(c.name))}</b>")
    tenure = c.tenure if c.tenure != "unknown" else "tenure unknown"
    lease = ""
    if c.tenure not in ("freehold", "999 year") and c.remaining_lease is not None:
        lease = f", {c.remaining_lease:.0f} years left"
    size = f"{num(c.size_sqft)} sqft" if c.size_sqft else "size unknown"
    age = f"built {c.built_year}" if c.built_year else (f"completion {c.completion_year}" if c.completion_year else "")
    lines.append(esc(join([clean(c.address), tenure + lease, size, age])))
    psf = f" ({num(c.psf)} psf)" if c.psf else ""
    src = join([clean(c.price_label), f"from {clean(c.price_source)}" if c.price_source else "",
                f"on {c.price_date}" if c.price_date else ""], " ")
    lines.append(esc(f"Price: {money(c.price)}{psf}, {src}"))
    comps = c.comparables
    if comps and comps.median_psf and c.psf:
        prem = (c.psf / comps.median_psf - 1) * 100
        word = "above" if prem >= 0 else "below"
        few = ", fewer than three comparables" if comps.n < 3 else ""
        lines.append(esc(f"Market: median S${num(comps.median_psf)} psf in {clean(comps.area_label or area)} over "
                         f"{comps.period} ({comps.n} sales, {clean(comps.source)}), this is {abs(prem):.1f}% {word}{few}"))
    else:
        lines.append("Market: unknown, no comparable sales loaded")
    if proj.rent0:
        gy = proj.rent0 * 12 / c.price * 100 if c.price else None
        lines.append(esc(f"Rent: about S${num(proj.rent0)}/month ({pct(gy)} gross), {clean(proj.rent_label) or 'source unknown'}"))
    else:
        lines.append("Rent: unknown")
    lines.append(esc(f"Eligibility: {_eligibility_line(card)}"))
    lines.append(esc(f"Upfront: {money(fin.upfront_total)}"))
    lines.append(esc(f"Downpayment {money(fin.downpayment)} (cash at least {money(fin.min_cash)}, CPF up to "
                     f"{money(fin.cpf_used_downpayment)}, extra cash {money(fin.extra_cash)})"))
    fees = card.deal.fees
    agent_part = f"Agent {money(fees.agent)}" if fees.agent else ""
    reno_part = f"Renovation {money(fees.renovation)} budgeted" if fees.renovation else ""
    furn_pay = [p for p in fin.payments if p.label == "Furnishing"]
    furn_part = ""
    if furn_pay:
        furn_part = (f"Furnishing {money(furn_pay[0].amount)}" if furn_pay[0].year == 0
                     else f"Furnishing {money(furn_pay[0].amount)} in year {furn_pay[0].year}, not upfront")
    gst_part = (f"GST {money(d.gst)} ({d.gst_status})" if d.gst else "")
    lines.append(esc(join([f"BSD {money(d.bsd)}", f"ABSD {money(d.absd)} ({d.absd_pct:.0f}%)",
                           f"Legal and valuation {money(fees.legal + fees.valuation)}", agent_part, reno_part,
                           furn_part, gst_part])))
    if fin.schedule_note:
        lines.append(esc(clean(fin.schedule_note)))
    if fin.cpf_factor_note and fin.cpf_factor_note != "full":
        lines.append(esc(f"CPF: {clean(fin.cpf_factor_note)}"))
    grants = [g for g in card.elig.grants]
    if grants:
        gl = [f"{g['name']} up to {money(g.get('up_to'))}" + ("" if g.get("counted") else ", not counted") for g in grants]
        lines.append(esc("Grants: " + join(gl, "; ")))
    if fin.cash_short > 0:
        lines.append(esc(f"Cash needed {money(fin.cash_needed)} against your {money(fin.cash_available)}, "
                         f"short by {money(fin.cash_short)}"))
    eff_ltv = opt.loan / c.price * 100 if c.price else None
    cap = "" if opt.binding == "LTV" else f", capped by {opt.binding}"
    rate_note = "" if card.rates.source != "assumed" or opt.lender == "HDB" else ", assumed"
    lines.append(esc(f"Loan: {money(opt.loan)} ({pct(eff_ltv)} LTV, {_loan_type(card)}{cap}) over {opt.tenure_years} "
                     f"years at {opt.rate_pct:.1f}%{rate_note}") + f" → {esc(money(opt.instalment))}/month")
    tdsr = f"TDSR {pct(opt.tdsr_pct)} (limit 55"
    tdsr += f", {pct(opt.tdsr_pct_alone)} without your co buyer)" if opt.tdsr_pct_alone is not None else ")"
    msr = ""
    if opt.msr_applies and opt.msr_pct is not None:
        msr = f", MSR {pct(opt.msr_pct)} (limit 30" + (f", {pct(opt.msr_pct_alone)} without your co buyer)"
                                                       if opt.msr_pct_alone is not None else ")")
    lines.append(esc(f"Stress test at {opt.stress_rate_pct:.1f}%: {money(opt.instalment_stress)}/month, {tdsr}{msr}, "
                     f"your own limit {settings.profile.max_monthly_commitment_pct:.0f}%, this loan uses "
                     f"{pct(opt.own_share_pct)}"))
    m = proj.monthly_if_rented
    if m:
        when = "" if m["year"] == 1 else f" from year {m['year']}"
        lines.append(esc(f"Monthly if rented out{when}: rent {money(m['rent_before_vacancy'])} minus instalment, maintenance, "
                         f"tax and vacancy = {money(m['net'])}"))
    else:
        why = "the whole flat cannot be rented out" if not card.deal.whole_rental_ever else "not modelled"
        lines.append(esc(f"Monthly if rented out: not allowed, {why}"))
    lines.append("")
    H = proj.hold_years
    X = proj.exit_year
    if proj.base:
        if X == H:
            lines.append(f"{H} year outlook, sell in year {H}:")
        else:
            lines.append(f"{H} year outlook: a sale in year {H} is not allowed because the MOP ends in year {X}, "
                         f"so these sell in year {X}:")
        for s, nm in ((proj.bear, "Bear"), (proj.base, "Base"), (proj.bull, "Bull")):
            extra = f" (growth from {clean(proj.cagr_source)})" if nm == "Base" else ""
            lines.append(esc(f"{nm} {pct(s.cagr_pct)}/yr: net {money_round(s.net_gain)}, IRR {pct(s.irr_pct)}{extra}"))
        be = f"year {proj.base.breakeven_year}" if proj.base.breakeven_year else f"none within {X} years"
        if proj.fair_price and proj.fair_price_note.startswith("at least"):
            fair = f"above {money_round(proj.fair_price)}"
        elif proj.fair_price:
            fair = money_round(proj.fair_price)
        else:
            fair = clean(proj.fair_price_note) or "unknown"

        lines.append(esc(f"Break even {be} · Fair price for your {settings.profile.benchmark_return_pct:g}% target: {fair}"))
        lines.append(esc(f"If rates are 1 point higher: base IRR {pct(proj.irr_rate_up_pct)} · "
                         f"If growth is 1 point lower: {pct(proj.irr_growth_down_pct)}"))
    else:
        lines.append(esc(f"{H} year outlook: unknown, {clean(proj.cagr_source)}"))
    if proj.mop_years:
        ssd = f" · SSD until {proj.ssd_end_date}" if proj.ssd_end_date else ""
        what = ("earliest sale or whole unit rental" if card.deal.whole_rental_ever
                else "earliest sale, whole flat rental never allowed,")
        lines.append(esc(f"MOP: {proj.mop_years} years from key collection, {what} {proj.mop_end_date} "
                         f"({clean(proj.mop_rule)}){ssd}" if card.proj.build_years and c.category_key in ("bto", "ec")
                         else f"MOP: {proj.mop_years} years, {what} {proj.mop_end_date} ({clean(proj.mop_rule)}){ssd}"))
        if proj.mop_exit:
            lines.append(esc(f"Sell at MOP in year {proj.mop_exit.exit_year}: base net {money_round(proj.mop_exit.net_gain)}, "
                             f"IRR {pct(proj.mop_exit.irr_pct)}"))
    elif proj.ssd_end_date:
        part = " on the residential part" if 0 < card.duties.residential_share < 1 else ""
        lines.append(esc(f"SSD{part} until {proj.ssd_end_date} (no MOP for private)"))
    else:
        lines.append("No MOP and no SSD for this property type")
    if card.deal.subsidy_recovery_pct:
        lines.append(esc(f"Subsidy recovery on resale: {card.deal.subsidy_recovery_pct:g}% of the price, "
                         f"{clean(card.deal.subsidy_label)}"))
    if proj.base:
        lines.append(f"Year by year, base case (value starts at {esc(proj.v0_label)}):")
        lines.append("<pre>" + esc(year_table(card)) + "</pre>")
        rows = proj.base.rows[:max(H, X)]
        lo = min(r.change_pct for r in rows)
        hi = max(r.change_pct for r in rows)
        worst = next(r for r in rows if r.change_pct <= lo + 0.05)   # first of the tied years
        best = next(r for r in rows if r.change_pct >= hi - 0.05)
        drop = (f"year {worst.year} ({table_change(worst.change_pct).replace('dep ', 'minus ')})"
                if worst.change_pct < 0 else "none in the base case")
        decay_total = proj.lease_decay_total
        decay = (f"{money(decay_total)}, {clean(proj.lease_decay_note)}" if decay_total > 0
                 else clean(proj.lease_decay_note))
        best_txt = (f"every year about {table_change(best.change_pct)}" if best.change_pct - worst.change_pct < 0.05
                    else f"{best.year} ({table_change(best.change_pct)})")
        lines.append(esc(f"Biggest drop: {drop} · Best year: {best_txt} · "
                         f"Lease decay over {max(H, X)} years: {decay}"))
    signals = [clean(s) for s in c.signals]
    if c.remaining_lease is not None and c.tenure not in ("freehold", "999 year"):
        built = proj.build_years if c.category_key == "bto" else 0
        signals.append(f"lease {c.remaining_lease - max(0, X - built):.0f} years at exit")
    lines.append(esc("Signals: " + (join(signals) if signals else "none loaded yet")))
    flags = [f for f in v.flags]
    if flags:
        lines.append(esc("Flags: " + join(flags, ", ")))
    cur = card.curated or {}
    if v.label == "Not eligible yet":
        note = clean(cur.get("eligibility_note")) or (clean(card.elig.what_would_change_it[0]) if card.elig.what_would_change_it else "")
        lines.append(f"Verdict: <b>{esc(v.label)}</b>" + (esc(f", {note}") if note else ""))
    else:
        lines.append(f"Verdict: <b>{esc(v.label)}</b> ({v.score:.1f}/5), best as {esc(v.best_intent_words)}")
        if v.override == "cash short":
            lines.append(esc(f"Needs {money(card.fin.cash_short)} more cash than you have."))
        if cur.get("verdict_why"):
            lines.append(esc(clean(cur["verdict_why"])))
        if cur.get("risks"):
            lines.append(esc("Risks: " + "; ".join(clean(r) for r in cur["risks"])))
        if cur.get("what_would_make_it_work"):
            lines.append(esc("What would make it work: " + clean(cur["what_would_make_it_work"])))
        if cur.get("mop_note"):
            lines.append(esc(clean(cur["mop_note"])))
    links = []
    if c.url:
        links.append(f'<a href="{esc_attr(c.url)}">Listing</a>')
    if comps and comps.url:
        links.append(f'<a href="{esc_attr(comps.url)}">Transactions</a>')
    for label, url in c.more_sources[:2]:
        links.append(f'<a href="{esc_attr(url)}">{esc(clean(label))}</a>')
    if links:
        lines.append(" · ".join(links))
    found = f" Found {run_time} SGT" if run_time else ""
    lines.append(esc(DISCLAIMER.format(date=card.rules_date)) + esc(found))
    out = []
    for line in lines:
        if line == "" and (not out or out[-1] == ""):
            continue
        out.append(line)
    return "\n".join(out).strip()


def year_table(card: Card) -> str:
    proj = card.proj
    rows = proj.base.rows[:max(proj.hold_years, proj.exit_year)]
    head = f"{'Yr':>2} {'Value':>8} {'Change':>9} {'Equity':>7} {'If sold':>10}"
    body = [head]
    for r in rows:
        body.append(f"{r.year:>2} {kfmt(r.value_end):>8} {table_change(r.change_pct):>9} {table_equity(r.equity):>7} "
                    f"{table_sold(r.net_if_sold, r.sale_allowed):>10}")
    return "\n".join(body)


# ------------------------------------------------------------ checks
def numbers_in(text: str) -> set[str]:
    return set(NUMBER.findall(prose_without_exempt(text)))


def allowed_numbers(message: str) -> list[str]:
    """Every numeric string the deterministic card prints; Claude may quote only these."""
    return sorted(numbers_in(message))


def check_generated_text(text: str, allowed: Iterable[str]) -> list[str]:
    """Problems with Claude's words: unknown numbers, URLs, dashes or promises."""
    problems = []
    allowed_set = set(allowed)
    for n in numbers_in(text):
        if n not in allowed_set and n.rstrip(".") not in allowed_set:
            problems.append(f"number {n} is not on the card")
    if re.search(r"https?://|www\.", text):
        problems.append("contains a link")
    if any(ch in text for ch in "-" + _DASHES):
        problems.append("contains a dash")
    m = BANNED_WORDS.search(text)
    if m:
        problems.append(f"promise word: {m.group(0)}")
    if re.search(r"\bwill\b", text, re.I):
        problems.append("uses 'will' (a promise)")
    return problems
