"""Loans and the upfront table. Pure functions; every legal figure comes from the rules file."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..config import AssumptionsCfg, Profile
from ..models import Candidate, EligibilityResult, Rates
from ..rules.loader import RuleTrace, Rules
from .categories import info
from .costs import Duties, PurchaseFees


# ------------------------------------------------------------ formulas
def instalment(principal: float, annual_rate_pct: float, years: float) -> float:
    """Standard amortisation: P r / (1 - (1 + r)^-n) with monthly r and n."""
    n = round(years * 12)
    if principal <= 0 or n <= 0:
        return 0.0
    r = annual_rate_pct / 100.0 / 12.0
    if r == 0:
        return principal / n
    return principal * r / (1 - (1 + r) ** -n)


def loan_from_payment(payment: float, annual_rate_pct: float, years: float) -> float:
    n = round(years * 12)
    if payment <= 0 or n <= 0:
        return 0.0
    r = annual_rate_pct / 100.0 / 12.0
    if r == 0:
        return payment * n
    return payment * (1 - (1 + r) ** -n) / r


def assessed_income(profile: Profile, rules: Rules, trace: RuleTrace | None, *, include_co: bool = True) -> float:
    """Monthly income banks count for TDSR and MSR: variable (and self employed) income is haircut."""
    haircut = rules.v(trace, "mas_tdsr", "variable_income_haircut_pct") / 100.0
    if profile.self_employed:
        own = (profile.gross_monthly_income + profile.variable_monthly_income) * (1 - haircut)
    else:
        own = profile.gross_monthly_income + profile.variable_monthly_income * (1 - haircut)
    co = profile.co_buyer.gross_monthly_income if include_co and profile.has_co_buyer else 0.0
    return own + co


def age_basis(profile: Profile) -> float:
    """Income weighted average age with a co buyer, else the buyer's age."""
    if profile.has_co_buyer and profile.co_buyer.gross_monthly_income > 0:
        a, b = profile.gross_monthly_income, profile.co_buyer.gross_monthly_income
        return (profile.age * a + profile.co_buyer.age * b) / (a + b) if a + b else profile.age
    return float(profile.age)


def youngest_age(profile: Profile) -> int:
    return min(profile.age, profile.co_buyer.age) if profile.has_co_buyer else profile.age


def cpf_lease_factor(remaining_lease: float | None, tenure: str, youngest: int, rules: Rules,
                     trace: RuleTrace | None) -> tuple[float, str]:
    """Share of the Valuation Limit CPF may cover, by CPF's lease to age 95 rule."""
    if tenure in ("freehold", "999 year") or remaining_lease is None:
        return 1.0, "full"
    rule = rules.v(trace, "cpf_housing_usage")
    cover, minimum = rule["lease_cover_age"], rule["min_remaining_lease_years"]
    if remaining_lease < minimum:
        return 0.0, f"no CPF, lease under {minimum} years"
    if remaining_lease >= cover - youngest:
        return 1.0, "full"
    factor = (remaining_lease - minimum) / (cover - youngest - minimum)
    return max(0.0, min(1.0, factor)), f"pro rated to {factor * 100:.1f}% (lease does not reach age {cover})"


# ------------------------------------------------------------ results
@dataclass
class LoanOption:
    lender: str                   # "HDB", "bank", "bank, commercial"
    ltv_pct: float                # the legal or planning cap used
    reduced_ltv: bool
    min_cash_pct: float
    tenure_years: int
    rate_pct: float
    stress_rate_pct: float
    loan: float
    binding: str                  # LTV, TDSR, MSR
    instalment: float
    instalment_stress: float
    tdsr_pct: float
    tdsr_pct_alone: float | None
    msr_pct: float | None
    msr_pct_alone: float | None
    msr_applies: bool
    own_share_pct: float          # today's instalment as a share of assessed income
    planning_figure: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class Payment:
    label: str
    amount: float
    year: int                     # years after purchase (0 = at purchase)
    cpf: float = 0.0              # part paid from CPF OA
    toward_price: bool = False    # part of the purchase price (downpayment), not a fee

    @property
    def cash(self) -> float:
        return self.amount - self.cpf


@dataclass
class Financing:
    option: LoanOption
    alternative: LoanOption | None
    valuation_basis: float
    downpayment: float
    min_cash: float
    cpf_available: float
    cpf_limit: float
    cpf_factor_note: str
    cpf_used: float
    cpf_used_downpayment: float
    cpf_used_duties: float
    extra_cash: float             # downpayment beyond the minimum cash that CPF could not cover
    upfront_total: float
    cash_needed: float
    cash_available: float
    cash_short: float             # how much more cash the buyer needs (0 when affordable)
    payments: list[Payment]
    schedule_note: str = ""
    loan_start_year: int = 0
    draw_years: int = 0           # years of progressive drawdown before amortisation starts

    def as_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------ loan options
def _lease_cap(cand: Candidate, buffer_years: float) -> float | None:
    if cand.tenure in ("freehold", "999 year") or cand.remaining_lease is None:
        return None
    return max(0.0, cand.remaining_lease - buffer_years)


def _ratios(loan: float, rate: float, stress: float, tenure: int, profile: Profile, rules: Rules,
            trace: RuleTrace | None, msr_applies: bool) -> dict:
    inst = instalment(loan, rate, tenure)
    inst_s = instalment(loan, stress, tenure)
    inc = assessed_income(profile, rules, trace)
    inc_alone = assessed_income(profile, rules, trace, include_co=False)
    debts = profile.monthly_debt_repayments
    out = {"instalment": inst, "instalment_stress": inst_s,
           "tdsr_pct": (inst_s + debts) / inc * 100 if inc else float("inf"),
           "tdsr_pct_alone": ((inst_s + debts) / inc_alone * 100 if inc_alone else None) if profile.has_co_buyer else None,
           "msr_pct": inst_s / inc * 100 if msr_applies and inc else None,
           "msr_pct_alone": (inst_s / inc_alone * 100 if inc_alone else None) if msr_applies and profile.has_co_buyer else None,
           "own_share_pct": inst / inc * 100 if inc else float("inf")}
    return out


def bank_residential(cand: Candidate, price: float, valuation: float, profile: Profile, rules: Rules,
                     trace: RuleTrace | None, rates: Rates) -> LoanOption:
    cat = info(cand.category_key)
    hdb_flat = cat.hdb_flat
    reduced_when = rules.v(trace, "mas_ltv", "reduced_when")
    threshold = reduced_when["tenure_over_years"]["hdb_flat" if hdb_flat else "other"]
    max_tenure = rules.v(trace, "mas_max_tenure", "hdb_flat_years" if hdb_flat else "other_years")
    age = age_basis(profile)
    lease_cap = _lease_cap(cand, rules.v(trace, "bank_lease_tenure_planning", "lease_buffer_years"))
    full = min(threshold, reduced_when["beyond_age"] - age)
    if lease_cap is not None:
        full = min(full, lease_cap)
    reduced = full < 5
    tenure = int(full) if not reduced else int(min(max_tenure, lease_cap if lease_cap is not None else max_tenure))
    ltv_pct, min_cash_pct = rules.ltv(trace, profile.housing_loans_outstanding, reduced)
    rate = rates.bank_rate_today
    stress = max(rules.v(trace, "mas_tdsr", "stress_floor_pct", "residential"), rate)
    msr_types = rules.v(trace, "mas_msr", "applies_to_types")
    msr_applies = (hdb_flat and "hdb_flat" in msr_types) or (cand.category_key == "ec" and "ec_within_mop" in msr_types)
    inc = assessed_income(profile, rules, trace)
    tdsr_room = inc * rules.v(trace, "mas_tdsr", "limit_pct") / 100.0 - profile.monthly_debt_repayments
    caps = {"LTV": valuation * ltv_pct / 100.0, "TDSR": loan_from_payment(tdsr_room, stress, tenure)}
    if msr_applies:
        caps["MSR"] = loan_from_payment(inc * rules.v(trace, "mas_msr", "limit_pct") / 100.0, stress, tenure)
    binding = min(caps, key=caps.get)
    loan = max(0.0, caps[binding])
    r = _ratios(loan, rate, stress, tenure, profile, rules, trace, msr_applies)
    notes = []
    if reduced:
        notes.append(f"reduced LTV because a full LTV tenure would be under 5 years")
    return LoanOption("bank", ltv_pct, reduced, min_cash_pct, tenure, rate, stress, loan, binding,
                      r["instalment"], r["instalment_stress"], r["tdsr_pct"], r["tdsr_pct_alone"], r["msr_pct"],
                      r["msr_pct_alone"], msr_applies, r["own_share_pct"], notes=notes)


def hdb_loan(cand: Candidate, price: float, valuation: float, profile: Profile, rules: Rules,
             trace: RuleTrace | None, rates: Rates) -> LoanOption:
    h = rules.v(trace, "hdb_loan")
    age = age_basis(profile)
    tenure = min(h["max_tenure_years"], h["tenure_age_cap"] - age)
    lease_cap = _lease_cap(cand, h["tenure_lease_buffer_years"])
    if lease_cap is not None:
        tenure = min(tenure, lease_cap)
    tenure = int(max(0, tenure))
    rate = rates.hdb_rate
    stress = max(h["assessment_floor_pct"], rate)
    inc = assessed_income(profile, rules, trace)
    caps = {"LTV": valuation * h["ltv_pct"] / 100.0,
            "MSR": loan_from_payment(inc * h["msr_pct"] / 100.0, stress, tenure)}
    binding = min(caps, key=caps.get)
    loan = max(0.0, caps[binding])
    r = _ratios(loan, rate, stress, tenure, profile, rules, trace, True)
    return LoanOption("HDB", h["ltv_pct"], False, 0.0, tenure, rate, stress, loan, binding, r["instalment"],
                      r["instalment_stress"], r["tdsr_pct"], r["tdsr_pct_alone"], r["msr_pct"], r["msr_pct_alone"],
                      True, r["own_share_pct"])


def bank_commercial(cand: Candidate, price: float, valuation: float, profile: Profile, rules: Rules,
                    trace: RuleTrace | None, rates: Rates) -> LoanOption:
    plan = rules.v(trace, "commercial_loan_planning")
    tenure = plan["max_tenure_years"]
    lease_cap = _lease_cap(cand, rules.v(trace, "bank_lease_tenure_planning", "lease_buffer_years"))
    if lease_cap is not None:
        tenure = min(tenure, lease_cap)
    tenure = int(max(0, tenure))
    rate = rates.bank_rate_today
    stress = max(rules.v(trace, "mas_tdsr", "stress_floor_pct", "non_residential"), rate)
    inc = assessed_income(profile, rules, trace)
    tdsr_room = inc * rules.v(trace, "mas_tdsr", "limit_pct") / 100.0 - profile.monthly_debt_repayments
    caps = {"LTV": valuation * plan["ltv_pct"] / 100.0, "TDSR": loan_from_payment(tdsr_room, stress, tenure)}
    binding = min(caps, key=caps.get)
    loan = max(0.0, caps[binding])
    r = _ratios(loan, rate, stress, tenure, profile, rules, trace, False)
    return LoanOption("bank, commercial", plan["ltv_pct"], False, 100 - plan["ltv_pct"], tenure, rate, stress, loan,
                      binding, r["instalment"], r["instalment_stress"], r["tdsr_pct"], r["tdsr_pct_alone"], None, None,
                      False, r["own_share_pct"], planning_figure=True,
                      notes=["LTV is a planning figure; banks decide commercial LTV"])


def _total_interest(opt: LoanOption, years: int, long_run_rate: float) -> float:
    """Interest over the first `years` with the rate path used by the projection."""
    bal, total = opt.loan, 0.0
    n_left = opt.tenure_years * 12
    rate = opt.rate_pct
    pay = instalment(bal, rate, opt.tenure_years)
    for m in range(min(years, opt.tenure_years) * 12):
        if opt.lender != "HDB" and m == 24:
            rate = long_run_rate
            pay = instalment(bal, rate, n_left / 12)
        interest = bal * rate / 1200
        total += interest
        bal -= pay - interest
        n_left -= 1
    return total


# ------------------------------------------------------------ the full financing picture
def finance(cand: Candidate, price: float, profile: Profile, a: AssumptionsCfg, rules: Rules,
            trace: RuleTrace | None, rates: Rates, duties: Duties, fees: PurchaseFees,
            elig: EligibilityResult, *, build_years: int = 0, hold_years: int = 10) -> Financing:
    cat = info(cand.category_key)
    valuation = min(price, cand.market_value) if cand.market_value else price
    residential_only = duties.residential_share >= 1.0
    alternative = None
    if cat.loan == "commercial" and not residential_only:
        option = bank_commercial(cand, price, valuation, profile, rules, trace, rates)
    else:
        option = bank_residential(cand, price, valuation, profile, rules, trace, rates)
        if cat.loan == "hdb_or_bank" and elig.hdb_loan_allowed:
            hdb = hdb_loan(cand, price, valuation, profile, rules, trace, rates)
            years = min(hold_years, 10)
            bank_cost = _total_interest(option, years, a.long_run_bank_rate_pct)
            hdb_cost = _total_interest(hdb, years, a.long_run_bank_rate_pct)
            # prefer the larger loan when the loans differ, else the cheaper interest
            if hdb.loan > option.loan * 1.001 or (abs(hdb.loan - option.loan) <= option.loan * 0.001 and hdb_cost <= bank_cost):
                option, alternative = hdb, option
            else:
                alternative = hdb
    down = price - option.loan
    cpf_ok = residential_only and cat.kind != "commercial"
    if cpf_ok:
        rules.v(trace, "cpf_housing_usage", "allowed_for")
    else:
        rules.v(trace, "cpf_housing_usage", "not_allowed_for")
    min_cash = down if not cpf_ok else price * option.min_cash_pct / 100.0
    min_cash = min(min_cash, down)
    cpf_avail = profile.cpf_oa_balance + (profile.co_buyer.cpf_oa if profile.has_co_buyer else 0.0)
    factor, factor_note = (cpf_lease_factor(cand.remaining_lease, cand.tenure, youngest_age(profile), rules, trace)
                           if cpf_ok else (0.0, "no CPF for this property type"))
    cpf_limit = valuation * factor if cpf_ok else 0.0
    payable_by_cpf = max(0.0, down - min_cash) + duties.bsd + duties.absd
    cpf_used = min(cpf_avail, payable_by_cpf, cpf_limit) if cpf_ok else 0.0
    cpf_down = min(cpf_used, max(0.0, down - min_cash))
    cpf_duties = cpf_used - cpf_down
    extra_cash = max(0.0, down - min_cash - cpf_down)
    upfront = down + duties.bsd + duties.absd + duties.gst + fees.total
    cash_needed = upfront - cpf_used
    cash_short = max(0.0, cash_needed - profile.cash_available)

    payments: list[Payment] = []
    schedule_note = ""
    loan_start, draw_years = 0, 0
    if cand.category_key == "bto":
        sched = rules.v(trace, "bto_payment")
        rooms = (cand.flat_type or "").upper()
        fee_key = "two_room" if rooms.startswith("2") else "three_room" if rooms.startswith("3") else "four_room_or_larger"
        option_fee = sched["option_fee"][fee_key]
        afl_pct = sched["downpayment_at_agreement_for_lease_pct"]["hdb_loan" if option.lender == "HDB" else "bank_loan"]
        afl = price * afl_pct / 100.0
        afl = min(afl, down)
        key_balance = max(0.0, down - afl)
        # the option fee is cash and counts toward the minimum cash; CPF covers the rest where allowed
        cash_min_left = max(0.0, min_cash - option_fee)
        afl_rest = max(0.0, afl - option_fee)
        afl_cash_min = min(afl_rest, cash_min_left)
        cash_min_left -= afl_cash_min
        cpf_left = cpf_down
        afl_cpf = min(cpf_left, afl_rest - afl_cash_min)
        cpf_left -= afl_cpf
        key_cpf = min(cpf_left, max(0.0, key_balance - cash_min_left))
        cpf_left -= key_cpf
        if cpf_left > 0.005:          # CPF that the staged downpayment could not use goes to the duties
            cpf_duties += cpf_left
            cpf_down -= cpf_left
            extra_cash = max(0.0, down - min_cash - cpf_down)
        payments += [Payment("Option fee", option_fee, 0, 0.0, True),
                     Payment(f"Agreement for Lease, {afl_pct:g}% less option fee", afl - option_fee, 0, afl_cpf, True),
                     Payment("BSD and ABSD", duties.bsd + duties.absd, 0, cpf_duties),
                     Payment("Legal and valuation", fees.legal + fees.valuation, 0),
                     Payment("Balance of downpayment at key collection", key_balance, build_years, key_cpf, True),
                     Payment("Renovation at key collection", fees.renovation, build_years)]
        if fees.furnishing:
            payments.append(Payment("Furnishing", fees.furnishing, build_years))
        schedule_note = (f"Option fee S${option_fee:,.0f}, {afl_pct:g}% at the Agreement for Lease, "
                         f"the rest of the downpayment and the loan at key collection")
        loan_start = build_years
    elif cat.under_construction and build_years > 0:
        sched = rules.v(trace, "new_launch_payment")
        early = price * sched["within_8_weeks_total_pct"] / 100.0
        early = min(early, down)
        rest = down - early
        cpf_early = min(cpf_down, max(0.0, early - min_cash))
        payments += [Payment(f"Booking and exercise, {sched['within_8_weeks_total_pct']:g}% within 8 weeks", early, 0, cpf_early, True),
                     Payment("Rest of the downpayment at foundation", rest, 0, cpf_down - cpf_early, True),
                     Payment("BSD and ABSD", duties.bsd + duties.absd, 0, cpf_duties),
                     Payment("Legal and valuation", fees.legal + fees.valuation, 0),
                     Payment("Renovation at completion", fees.renovation, build_years)]
        if fees.furnishing:
            payments.append(Payment("Furnishing", fees.furnishing, build_years))
        if duties.gst:
            payments.append(Payment("GST", duties.gst, 0))
        schedule_note = (f"{sched['booking_fee_pct']:g}% booking fee in cash, {sched['within_8_weeks_total_pct']:g}% "
                         f"in total within 8 weeks, then the loan is drawn stage by stage until completion")
        draw_years = build_years
    else:
        payments += [Payment("Downpayment", down, 0, cpf_down, True),
                     Payment("BSD and ABSD", duties.bsd + duties.absd, 0, cpf_duties),
                     Payment("Legal, valuation and agent", fees.legal + fees.valuation + fees.agent, 0),
                     Payment("Renovation", fees.renovation, 0)]
        if fees.furnishing:
            payments.append(Payment("Furnishing", fees.furnishing, 0))
        if duties.gst:
            payments.append(Payment("GST", duties.gst, 0))
    payments = [p for p in payments if p.amount > 0.005]
    return Financing(option, alternative, valuation, down, min_cash, cpf_avail, cpf_limit, factor_note, cpf_used,
                     cpf_down, cpf_duties, extra_cash, upfront, cash_needed, profile.cash_available, cash_short,
                     payments, schedule_note, loan_start, draw_years)
