"""Ten year (hold_years) cash flow projection under bear, base and bull scenarios.

Pure functions. A scenario is a growth rate; every year the model computes the market value
(market growth, then lease decay for leasehold), rent or rent saved, running costs, the loan,
equity, and what the buyer would net if the property were sold at the end of that year after
selling costs, SSD, subsidy recovery and the CPF refund. The model does not know the future;
the scenarios only show what follows from the stated assumptions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from ..config import AssumptionsCfg, Profile
from ..models import Candidate, EligibilityResult, Rates
from ..rules.loader import RuleTrace, Rules
from .categories import info
from .costs import (Duties, PurchaseFees, marginal_tax_rate, maintenance_monthly, property_tax_year, purchase_fees,
                    ssd_amount, ssd_rates_for, stamp_duties, tax_on_rent)
from .financing import Financing, finance, instalment


# ------------------------------------------------------------ numeric helpers
def npv(rate: float, flows: list[float]) -> float:
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(flows))


def irr(flows: list[float]) -> float | None:
    """Yearly IRR by scanning for a sign change and bisecting. None when there is no root."""
    if not flows or all(cf >= 0 for cf in flows) or all(cf <= 0 for cf in flows):
        return None
    grid = [-0.99 + i * 0.01 for i in range(0, 300)]   # -99% to +200%
    prev_r, prev_v = grid[0], npv(grid[0], flows)
    for r in grid[1:]:
        v = npv(r, flows)
        if prev_v == 0:
            return prev_r
        if (prev_v < 0) != (v < 0):
            lo, hi, flo = prev_r, r, prev_v
            for _ in range(100):
                mid = (lo + hi) / 2
                fm = npv(mid, flows)
                if (fm < 0) == (flo < 0):
                    lo, flo = mid, fm
                else:
                    hi = mid
            return (lo + hi) / 2
        prev_r, prev_v = r, v
    return None


def decay_pct(remaining: float | None, freehold: bool, table: dict[str, float]) -> float:
    """Yearly lease decay by remaining lease at the start of the year (config bands)."""
    if freehold or remaining is None:
        return 0.0
    if remaining > 80:
        return table.get("above_80_years", 0.0)
    if remaining > 70:
        return table.get("70_to_80_years", 0.0)
    if remaining > 60:
        return table.get("60_to_70_years", 0.0)
    if remaining > 50:
        return table.get("50_to_60_years", 0.0)
    return table.get("below_50_years", 0.0)


def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


# ------------------------------------------------------------ results
@dataclass
class YearRow:
    year: int
    mode: str                     # build, live, rent
    value_start: float
    value_end: float
    market_change: float
    decay_change: float
    decay_pct: float
    rent: float
    rent_saved: float
    maintenance: float
    property_tax: float
    insurance: float
    income_tax: float
    interest: float
    principal: float
    instalments: float
    loan_balance: float
    equity: float
    net_flow: float               # rent + rent saved minus instalments and running costs
    payments: float               # upfront payments due this year (BTO key collection)
    sale_allowed: bool
    net_if_sold: float | None
    sale: dict | None = None

    @property
    def change(self) -> float:
        return self.value_end - self.value_start

    @property
    def change_pct(self) -> float:
        return self.change / self.value_start * 100 if self.value_start else 0.0

    @property
    def costs(self) -> float:
        return self.maintenance + self.property_tax + self.insurance + self.income_tax


@dataclass
class ScenarioResult:
    name: str
    cagr_pct: float
    intent: str
    exit_year: int
    rows: list[YearRow]
    flows: list[float]
    total_cash_in: float
    total_cpf_in: float
    equity_out_cash: float
    equity_out_cpf: float
    net_gain: float
    irr_pct: float | None
    cash_on_cash_y1_pct: float | None
    breakeven_year: int | None
    exit_sale: dict | None
    allowed: bool = True


@dataclass
class Deal:
    """Everything fixed for one candidate at one price."""
    cand: Candidate
    price: float
    profile: Profile
    a: AssumptionsCfg
    rules: Rules
    trace: RuleTrace | None
    rates: Rates
    elig: EligibilityResult
    duties: Duties
    fees: PurchaseFees
    fin: Financing
    today: date
    build_years: int
    mop_years: int | None
    mop_end_year: int | None
    whole_rental_ever: bool
    ssd_rates: list[float]
    subsidy_recovery_pct: float
    subsidy_label: str
    grants_counted: float
    rent0: float | None
    rent_label: str
    maint_monthly: float
    marginal_pct: float
    v0: float
    v0_label: str
    vacancy_months: float
    gst_pct: float
    cpf_oa_pct: float
    notes: list[str] = field(default_factory=list)


@dataclass
class Projection:
    intent: str
    hold_years: int
    exit_year: int
    base_cagr: float | None
    bear_cagr: float | None
    bull_cagr: float | None
    cagr_source: str
    base: ScenarioResult | None
    bear: ScenarioResult | None
    bull: ScenarioResult | None
    fair_price: float | None
    fair_price_note: str
    irr_rate_up_pct: float | None
    irr_growth_down_pct: float | None
    mop_years: int | None
    mop_end_date: str | None
    mop_rule: str
    mop_exit: ScenarioResult | None
    ssd_end_date: str | None
    ssd_rates: list[float]
    intents: dict[str, dict]
    best_intent: str | None
    lease_decay_total: float
    lease_decay_note: str
    v0: float
    v0_label: str
    rent0: float | None
    rent_label: str
    build_years: int
    flags: list[str]
    notes: list[str]
    monthly_if_rented: dict | None

    def as_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------ growth
def scenario_growth(cand: Candidate, a: AssumptionsCfg) -> tuple[float | None, float | None, float | None, str]:
    g = cand.growth
    if g is None or (g.location_cagr is None and g.national_cagr is None):
        return None, None, None, "unknown, no growth history loaded"
    parts = []
    values = []
    if g.location_cagr is not None:
        values.append(g.location_cagr)
        parts.append(g.location_source or "location history")
    if g.national_cagr is not None:
        values.append(g.national_cagr)
        parts.append(g.national_source or "national history")
    base = min(values + [a.base_cagr_cap_pct])
    source = " and ".join(parts)
    if base == a.base_cagr_cap_pct and min(values) > a.base_cagr_cap_pct:
        source += ", capped"
    elif len(values) == 2:
        source += ", the lower of the two"
    bear = max(base - a.bear_offset_pct, -2.0)
    bull = base + a.bull_offset_pct
    return base, bear, bull, source


# ------------------------------------------------------------ deal setup
def build_deal(cand: Candidate, price: float, profile: Profile, a: AssumptionsCfg, rules: Rules,
               trace: RuleTrace | None, rates: Rates, elig: EligibilityResult, today: date,
               *, company: bool = False) -> Deal:
    cat = info(cand.category_key)
    intent0 = profile.intent[0] if profile.intent else "rent_out"
    duties = stamp_duties(cand, price, profile, rules, trace, company=company)
    fees = purchase_fees(cand, price, a, renting_out=intent0 == "rent_out", residential_share=duties.residential_share)
    build_years = 0
    notes: list[str] = []
    if cat.under_construction:
        if cand.completion_year:
            build_years = max(0, cand.completion_year - today.year)
        elif cand.category_key in ("bto", "condo_new_launch"):
            notes.append("completion year unknown, modelled as ready now")
    fin = finance(cand, price, profile, a, rules, trace, rates, duties, fees, elig, build_years=build_years,
                  hold_years=profile.hold_years)
    # MOP
    mop_years = None
    whole_rental_ever = True
    subsidy_pct, subsidy_label = 0.0, ""
    cls = (cand.bto_classification or "standard").lower()
    if cat.mop_key:
        mop = rules.v(trace, "hdb_mop")
        if cand.category_key == "bto":
            mop_years = mop[f"bto_{cls}_years"]
        else:
            mop_years = mop[cat.mop_key]
    if cand.category_key in ("bto", "hdb_resale") and cls in ("plus", "prime"):
        fc = rules.v(trace, "flat_classification", cls)
        whole_rental_ever = fc["whole_flat_rental_after_mop"]
        if cand.category_key == "bto" or cand.subsidy_recovery_pct is not None:
            if fc["subsidy_recovery"]:
                if cand.subsidy_recovery_pct is not None:
                    subsidy_pct, subsidy_label = cand.subsidy_recovery_pct, "project figure"
                else:
                    subsidy_pct = rules.v(trace, "flat_classification", "plh_reference_subsidy_recovery_pct")
                    subsidy_label = "assumed from earlier PLH projects; check the project's figure"
        if cand.category_key == "hdb_resale" and cand.subsidy_recovery_pct is None:
            notes.append("subsidy recovery applies only on the first resale; a resale buyer does not pay it again")
    mop_end_year = None if mop_years is None else (build_years if cand.category_key in ("bto", "ec") else 0) + mop_years
    # furnishing is bought when renting starts: after the MOP for HDB flats and ECs, never for Plus and Prime
    furnish = [p for p in fin.payments if p.label == "Furnishing"]
    if furnish:
        pay = furnish[0]
        if not whole_rental_ever:
            fin.payments.remove(pay)
            fees.furnishing = 0.0
            fin.upfront_total -= pay.amount
            fin.cash_needed -= pay.amount
        elif mop_end_year and pay.year < mop_end_year:
            pay.year = mop_end_year
            fin.upfront_total -= pay.amount
            fin.cash_needed -= pay.amount
            notes.append(f"furnishing for renting out is paid in year {mop_end_year}, when renting is allowed")
        fin.cash_short = max(0.0, fin.cash_needed - fin.cash_available)
    ssd_rates, _ = ssd_rates_for(cand, rules, trace, today, duties.residential_share)
    grants_counted = sum(g["amount"] for g in elig.grants if g.get("counted") and g.get("amount"))
    rent0 = cand.rent_estimate if cand.rent_estimate is not None else cand.rent_asking
    rent_label = cand.rent_source or ("asking rent" if cand.rent_asking is not None else "")
    maint, _ = maintenance_monthly(cand, a)
    if maint == 0:
        notes.append("maintenance not set in config for this category")
    marginal, _ = marginal_tax_rate(profile, rules, trace)
    comps = cand.comparables
    if cand.market_value and comps and comps.n >= 3:
        v0, v0_label = cand.market_value, "the comparable median"
    else:
        v0, v0_label = price, "the price"
    vacancy = a.vacancy_months_per_year.get(cat.vacancy_key, 0.0)
    return Deal(cand, price, profile, a, rules, trace, rates, elig, duties, fees, fin, today, build_years, mop_years,
                mop_end_year, whole_rental_ever, ssd_rates, subsidy_pct, subsidy_label, grants_counted, rent0,
                rent_label, maint, marginal, v0, v0_label, vacancy, rules.v(trace, "gst_rate", "rate_pct"),
                rules.v(trace, "cpf_oa_rate", "rate_pct"), notes)


# ------------------------------------------------------------ simulation
def _mode(d: Deal, t: int, intent: str) -> str:
    cat = info(d.cand.category_key)
    if t <= d.build_years:
        return "build"
    if cat.kind in ("commercial", "industrial"):
        return "rent"
    if cat.kind == "mixed" and d.duties.residential_share < 1:
        return "rent"
    if intent == "live_then_sell":
        return "live"
    if not d.whole_rental_ever:
        return "live"
    if d.mop_end_year is not None and t <= d.mop_end_year:
        return "live"                 # owners must occupy during the MOP
    return "rent"


def simulate(d: Deal, growth_pct: float, intent: str, years: int, *, rate_shift: float = 0.0) -> list[YearRow]:
    a = d.a
    cat = info(d.cand.category_key)
    opt = d.fin.option
    g = growth_pct / 100.0
    rg = a.rent_growth_pct / 100.0
    ci = a.cost_inflation_pct / 100.0
    freehold = d.cand.tenure in ("freehold", "999 year")
    residential_tax = cat.kind == "residential" or d.duties.residential_share >= 1
    payments_by_year: dict[int, float] = {}
    for p in d.fin.payments:
        payments_by_year[p.year] = payments_by_year.get(p.year, 0.0) + p.amount
    payments_by_year[0] = payments_by_year.get(0, 0.0) - d.grants_counted

    bal = 0.0
    months_left = 0
    pay = 0.0
    rate = opt.rate_pct + rate_shift
    loan_months_elapsed = 0
    amort_start = max(d.fin.loan_start_year, d.fin.draw_years)
    rows: list[YearRow] = []
    value = d.v0
    repaid = 0.0
    for t in range(1, years + 1):
        mode = _mode(d, t, intent)
        # lease at the start of the year
        if d.cand.category_key == "bto":
            lease_start_rem = (d.cand.remaining_lease or 99) - max(0, t - 1 - d.build_years)
        elif d.cand.remaining_lease is not None:
            lease_start_rem = d.cand.remaining_lease - (t - 1)
        else:
            lease_start_rem = None
        dp = decay_pct(lease_start_rem, freehold, a.lease_decay)
        market = value * g
        after_market = value + market
        decay = after_market * dp / 100.0
        new_value = after_market - decay
        # loan for the 12 months of this year
        interest = principal = paid = 0.0
        if d.fin.draw_years and t <= d.fin.draw_years:
            drawn = opt.loan * (t - 0.5) / d.fin.draw_years
            interest = drawn * (opt.rate_pct + rate_shift) / 100.0
            paid = interest
            bal = opt.loan * t / d.fin.draw_years
        elif t > amort_start and opt.loan > 0:
            for _ in range(12):
                if months_left == 0 and loan_months_elapsed == 0:
                    bal = opt.loan
                    months_left = opt.tenure_years * 12
                    rate = opt.rate_pct + rate_shift
                    pay = instalment(bal, rate, months_left / 12)
                if months_left <= 0 or bal <= 0.005:
                    break
                if opt.lender != "HDB" and loan_months_elapsed == 24:
                    rate = a.long_run_bank_rate_pct + rate_shift
                    pay = instalment(bal, rate, months_left / 12)
                i = bal * rate / 1200.0
                pr = min(bal, pay - i)
                interest += i
                principal += pr
                paid += i + pr
                bal -= pr
                months_left -= 1
                loan_months_elapsed += 1
        # income and running costs
        rent = saved = maint = ptax = ins = itax = 0.0
        if mode != "build":
            yrs = t - 1
            market_rent_year = (d.rent0 or 0.0) * 12 * (1 + rg) ** yrs
            if mode == "rent":
                rent = market_rent_year * (12 - d.vacancy_months) / 12
            elif a.count_rent_saved_when_living_in:
                saved = market_rent_year
            maint = d.maint_monthly * 12 * (1 + ci) ** yrs
            ins = a.insurance_yearly_sgd * (1 + ci) ** yrs
            ptax = property_tax_year(market_rent_year, residential_share=d.duties.residential_share,
                                     owner_occupied=(mode == "live"), rules=d.rules, trace=d.trace)
            if mode == "rent":
                itax = tax_on_rent(rent, interest, maint + ptax + ins, residential=residential_tax,
                                   marginal_pct=d.marginal_pct, rules=d.rules, trace=d.trace)
        net = rent + saved - paid - maint - ptax - ins - itax
        repaid += principal
        paid_toward_price = sum(p.amount for p in d.fin.payments if p.toward_price and p.year <= t)
        owed = max(0.0, d.price - paid_toward_price - repaid)
        rows.append(YearRow(t, mode, value, new_value, market, decay, dp, rent, saved, maint, ptax, ins, itax,
                            interest, principal, paid, owed, new_value - owed, net, payments_by_year.get(t, 0.0),
                            True, None))
        value = new_value
    # sale outcomes
    for row in rows:
        row.sale_allowed = d.mop_end_year is None or row.year >= d.mop_end_year
        row.sale = sale_at(d, row, rows)
        row.net_if_sold = row.sale["net_gain"] if row.sale_allowed else None
    return rows


def sale_at(d: Deal, row: YearRow, rows: list[YearRow]) -> dict:
    a = d.a
    t = row.year
    value = row.value_end
    agent = value * a.sale_agent_fee_pct / 100.0 * (1 + d.gst_pct / 100.0)
    legal = a.legal_fee_sgd * (1 + a.cost_inflation_pct / 100.0) ** t
    ssd = ssd_amount(value, t, d.ssd_rates, d.duties.residential_share if info(d.cand.category_key).kind == "mixed" else 1.0)
    subsidy = value * d.subsidy_recovery_pct / 100.0
    proceeds = value - agent - legal - ssd - subsidy - row.loan_balance
    oa = d.cpf_oa_pct / 100.0
    cpf_required = sum(p.cpf * (1 + oa) ** max(0, t - p.year) for p in d.fin.payments)
    cpf_required += d.grants_counted * (1 + oa) ** t
    cpf_principal = sum(p.cpf for p in d.fin.payments) + d.grants_counted
    cpf_refund = min(cpf_required, max(0.0, proceeds))
    cash_out = proceeds - cpf_refund
    flows = flows_for_exit(d, rows, t, proceeds)
    return {"year": t, "value": value, "agent_fee_with_gst": agent, "legal": legal, "ssd": ssd,
            "subsidy_recovery": subsidy, "loan_redemption": row.loan_balance, "proceeds": proceeds,
            "cpf_refund": cpf_refund, "cpf_principal": cpf_principal,
            "cpf_accrued_interest": max(0.0, cpf_refund - min(cpf_principal, cpf_refund)),
            "cash_out": cash_out, "net_gain": sum(flows), "flows": flows}


def flows_for_exit(d: Deal, rows: list[YearRow], exit_year: int, proceeds: float) -> list[float]:
    """Total equity cash flows: year 0 purchase, yearly net flows, sale proceeds in the exit year."""
    flows = [0.0] * (exit_year + 1)
    for p in d.fin.payments:
        if p.year <= exit_year:
            flows[p.year] -= p.amount
    flows[0] += d.grants_counted
    for r in rows[:exit_year]:
        flows[r.year] += r.net_flow
    flows[exit_year] += proceeds
    return flows


def scenario(d: Deal, name: str, growth_pct: float, intent: str, exit_year: int, rows: list[YearRow] | None = None,
             *, rate_shift: float = 0.0) -> ScenarioResult:
    years = max(exit_year, d.profile.hold_years)
    rows = rows or simulate(d, growth_pct, intent, years, rate_shift=rate_shift)
    exit_row = rows[exit_year - 1]
    sale = exit_row.sale
    flows = sale["flows"]
    upfront_cash = sum(p.cash for p in d.fin.payments)
    upfront_cpf = sum(p.cpf for p in d.fin.payments)
    topups = -sum(r.net_flow for r in rows[:exit_year] if r.net_flow < 0)
    inflows = sum(r.net_flow for r in rows[:exit_year] if r.net_flow > 0)
    cpf_in = max(0.0, upfront_cpf - d.grants_counted)
    total_cash_in = upfront_cash + topups
    out_cpf = sale["cpf_refund"]
    out_cash = sale["cash_out"] + inflows
    rate = irr(flows)
    first = rows[0]
    equity_in0 = upfront_cash + cpf_in
    coc = (first.rent - first.instalments - first.costs) / equity_in0 * 100 if equity_in0 and first.mode == "rent" else None
    breakeven = next((r.year for r in rows[:max(exit_year, main_exit(d, d.profile.hold_years))]
                      if r.sale_allowed and r.net_if_sold is not None and r.net_if_sold >= 0), None)
    return ScenarioResult(name, growth_pct, intent, exit_year, rows, flows, total_cash_in, cpf_in, out_cash, out_cpf,
                          sum(flows), None if rate is None else rate * 100, coc, breakeven, sale,
                          allowed=exit_row.sale_allowed)


# ------------------------------------------------------------ the full projection
def allowed_intents(d: Deal) -> list[str]:
    out = []
    for intent in d.profile.intent:
        if intent == "rent_out" and not d.whole_rental_ever:
            continue
        if intent == "live_then_sell" and info(d.cand.category_key).kind in ("commercial", "industrial"):
            continue
        if intent == "live_then_sell" and info(d.cand.category_key).kind == "mixed" and d.duties.residential_share < 1:
            continue
        if intent == "flip" and flip_exit(d) is None:
            continue
        out.append(intent)
    return out or ["rent_out"]


FLIP_MAX_YEARS = 5     # a flip is a sale within 5 years; with a longer MOP there is no flip


def flip_exit(d: Deal) -> int | None:
    if d.mop_end_year:
        return d.mop_end_year if d.mop_end_year <= FLIP_MAX_YEARS else None
    return 3


def main_exit(d: Deal, hold_years: int) -> int:
    """hold_years, unless the MOP has not ended by then; then the first year a sale is allowed."""
    if d.mop_end_year and d.mop_end_year > hold_years:
        return d.mop_end_year
    return hold_years


def project(cand: Candidate, price: float, profile: Profile, a: AssumptionsCfg, rules: Rules, trace: RuleTrace | None,
            rates: Rates, elig: EligibilityResult, today: date, *, company: bool = False,
            with_fair_price: bool = True) -> tuple[Deal, Projection]:
    d = build_deal(cand, price, profile, a, rules, trace, rates, elig, today, company=company)
    H = profile.hold_years
    base_g, bear_g, bull_g, source = scenario_growth(cand, a)
    intents = allowed_intents(d)
    primary = intents[0]
    flags: list[str] = []
    if d.rent0 is None:
        flags.append("rent unknown")
    mop_rule = ""
    if d.mop_years:
        cls = (cand.bto_classification or "").lower()
        mop_rule = f"{cls.title()} flat" if cls in ("plus", "prime") and cand.category_key in ("bto", "hdb_resale") \
            else {"bto": "Standard BTO", "hdb_resale": "HDB resale", "ec": "EC"}.get(cand.category_key, "HDB")
    mop_end_date = add_years(today, d.mop_end_year).isoformat()[:7] if d.mop_end_year else None
    ssd_end = add_years(today, len(d.ssd_rates)).isoformat()[:7] if d.ssd_rates else None
    monthly = None
    if base_g is None:
        proj = Projection(primary, H, main_exit(d, H), None, None, None, source, None, None, None, None,
                          "growth unknown", None, None,
                          d.mop_years, mop_end_date, mop_rule, None, ssd_end, d.ssd_rates, {}, None, 0.0, "",
                          d.v0, d.v0_label, d.rent0, d.rent_label, d.build_years, flags + ["growth unknown"],
                          d.notes, None)
        return d, proj
    X = main_exit(d, H)
    years = max(X, d.mop_end_year or 0, flip_exit(d) or 0)
    base_rows = simulate(d, base_g, primary, years)
    base = scenario(d, "base", base_g, primary, X, base_rows)
    bear = scenario(d, "bear", bear_g, primary, X)
    bull = scenario(d, "bull", bull_g, primary, X)
    mop_exit = None
    if d.mop_end_year and d.mop_end_year != X:
        mop_exit = scenario(d, "base", base_g, primary, d.mop_end_year, base_rows)
    # intents
    intent_results: dict[str, dict] = {}
    for intent in intents:
        exit_year = flip_exit(d) if intent == "flip" else X
        res = base if (intent == primary and exit_year == X) else scenario(d, "base", base_g, intent, exit_year)
        ssd = res.exit_sale["ssd"] if res.exit_sale else 0.0
        intent_results[intent] = {"irr_pct": res.irr_pct, "net": res.net_gain, "exit_year": exit_year,
                                  "ssd": ssd, "allowed": res.allowed}
    ranked = [i for i in intents if intent_results[i]["allowed"] and intent_results[i]["irr_pct"] is not None]
    best = max(ranked, key=lambda i: (intent_results[i]["irr_pct"], -intents.index(i))) if ranked else None
    # sensitivities
    up = scenario(d, "base", base_g, primary, X, rate_shift=1.0)
    down = scenario(d, "base", base_g - 1.0, primary, X)
    # lease decay
    decay_total = sum(r.decay_change for r in base_rows[:X])
    rates_used = sorted({r.decay_pct for r in base_rows[:X] if r.decay_pct > 0})
    decay_note = ("none, freehold" if cand.tenure in ("freehold", "999 year")
                  else "unknown, lease not given" if cand.remaining_lease is None and cand.category_key != "bto"
                  else "none in these years" if not rates_used
                  else " then ".join(f"{x:g}% a year" for x in rates_used))
    # monthly if rented
    rent_rows = [r for r in base_rows if r.mode == "rent"]
    if rent_rows:
        r = rent_rows[0]
        monthly = {"year": r.year, "rent": r.rent / 12, "instalment": r.instalments / 12,
                   "costs": (r.maintenance + r.property_tax + r.insurance + r.income_tax) / 12,
                   "net": (r.rent - r.instalments - r.costs) / 12, "rent_before_vacancy": (d.rent0 or 0) * (1 + a.rent_growth_pct / 100) ** (r.year - 1)}
    fair, fair_note = (None, "")
    if with_fair_price:
        fair, fair_note = fair_price(cand, price, profile, a, rules, rates, elig, today, profile.benchmark_return_pct,
                                     company=company)
    proj = Projection(primary, H, X, base_g, bear_g, bull_g, source, base, bear, bull, fair, fair_note,
                      up.irr_pct, down.irr_pct, d.mop_years, mop_end_date, mop_rule, mop_exit, ssd_end, d.ssd_rates,
                      intent_results, best, decay_total, decay_note, d.v0, d.v0_label, d.rent0, d.rent_label,
                      d.build_years, flags, d.notes, monthly)
    return d, proj


def fair_price(cand: Candidate, price: float, profile: Profile, a: AssumptionsCfg, rules: Rules, rates: Rates,
               elig: EligibilityResult, today: date, target_pct: float, *, company: bool = False) -> tuple[float | None, str]:
    """Price at which the base case IRR (primary intent, sold in hold_years) equals the target."""
    base_g, _, _, _ = scenario_growth(cand, a)
    if base_g is None:
        return None, "growth unknown"

    def base_irr(p: float) -> float | None:
        d = build_deal(cand, p, profile, a, rules, None, rates, elig, today, company=company)
        if not (cand.market_value and cand.comparables and cand.comparables.n >= 3):
            d.v0 = p
        intent = allowed_intents(d)[0]
        res = scenario(d, "base", base_g, intent, main_exit(d, profile.hold_years))
        return res.irr_pct

    lo, hi = price * 0.3, price * 1.5
    f_lo, f_hi = base_irr(lo), base_irr(hi)
    if f_lo is None or f_lo < target_pct:
        return None, "below 30% of the price"
    if f_hi is not None and f_hi >= target_pct:
        return hi, "at least 150% of the price"
    for _ in range(40):
        mid = (lo + hi) / 2
        f = base_irr(mid)
        if f is not None and f >= target_pct:
            lo = mid
        else:
            hi = mid
        if hi - lo < 500:
            break
    return round(lo / 1000) * 1000, ""
