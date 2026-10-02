"""What the law lets this buyer buy. Pure functions over profile, category and candidate.

Returns eligible (True, False or "conditional"), the reasons, the rule ids used and what would
change the answer. Every threshold comes from the rules file through the trace.
"""
from __future__ import annotations

from datetime import date

from ..config import Profile
from ..models import Candidate, EligibilityResult
from ..rules.loader import RuleTrace, Rules
from .categories import info


def _months_between(earlier: date, later: date) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month) - (1 if later.day < earlier.day else 0)


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    day = min(d.day, 28)
    return date(d.year + y, m + 1, day)


def _parse(d: str) -> date | None:
    try:
        return date.fromisoformat(d) if d else None
    except ValueError:
        return None


def household_income(p: Profile) -> float:
    own = p.gross_monthly_income + p.variable_monthly_income
    co = p.co_buyer.gross_monthly_income if p.has_co_buyer else 0.0
    return own + co


def is_family(p: Profile) -> bool:
    return p.buying_with in ("fiance", "spouse", "parents")


def is_single(p: Profile) -> bool:
    return p.buying_with in ("none", "joint_singles")


def flat_rooms(flat_type: str) -> str:
    t = (flat_type or "").upper().replace("-", " ")
    if "FLEXI" in t or t.startswith("2"):
        return "2"
    for n in ("3", "4", "5"):
        if t.startswith(n):
            return n
    if "EXEC" in t or "JUMBO" in t or "MULTI" in t:
        return "5"
    return ""


class _Acc:
    """Collects the result while each check runs."""

    def __init__(self) -> None:
        self.state: bool | str = True
        self.reasons: list[str] = []
        self.change: list[str] = []
        self.notes: list[str] = []
        self.grants: list[dict] = []

    def no(self, reason: str, change: str | None = None) -> None:
        self.state = False
        self.reasons.append(reason)
        if change:
            self.change.append(change)

    def maybe(self, reason: str, change: str | None = None) -> None:
        if self.state is True:
            self.state = "conditional"
        self.reasons.append(reason)
        if change:
            self.change.append(change)

    def ok(self, reason: str) -> None:
        self.reasons.append(reason)


def check(profile: Profile, cand: Candidate, rules: Rules, trace: RuleTrace, today: date,
          *, company: bool = False) -> EligibilityResult:
    cat = info(cand.category_key)
    acc = _Acc()
    hdb_loan_allowed = False
    key = cand.category_key
    if key == "bto":
        hdb_loan_allowed = _bto(profile, cand, rules, trace, today, acc)
    elif key == "hdb_resale":
        hdb_loan_allowed = _resale(profile, cand, rules, trace, today, acc)
    elif key == "ec":
        _ec(profile, cand, rules, trace, today, acc)
    elif key in ("condo_resale", "condo_new_launch"):
        _private(profile, cand, rules, trace, today, acc, landed=False)
    elif key == "landed":
        _private(profile, cand, rules, trace, today, acc, landed=True)
    else:
        _commercial(profile, cand, rules, trace, today, acc, company=company)
    from .costs import absd_profile, price_residential_share
    if price_residential_share(cand, rules, trace) > 0 and not company:
        btype, count, rate = absd_profile(profile, rules, trace)
        label = {1: "first", 2: "second"}.get(count, f"property number {count}")
        acc.notes.append(f"{label} property, ABSD {rate:g}%")
        absd_buyer, absd_count = btype, count
    else:
        absd_buyer, absd_count = ("entity" if company else profile.citizenship), 1
    _lease_notes(cand, profile, rules, trace, acc)
    return EligibilityResult(eligible=acc.state, reasons=acc.reasons, rule_ids=trace.ids(),
                             what_would_change_it=acc.change, hdb_loan_allowed=hdb_loan_allowed,
                             grants=acc.grants, absd_buyer=absd_buyer, absd_count=absd_count, notes=acc.notes)


# ------------------------------------------------------------ HDB
def _nucleus(p: Profile, rules: Rules, trace: RuleTrace, today: date, acc: _Acc, *, allow_singles: bool,
             single_note: str) -> str:
    """Return 'family', 'single' or '' after recording reasons."""
    singles = rules.v(trace, "hdb_singles")
    nucleus = rules.v(trace, "hdb_family_nucleus")
    if is_family(p):
        if not p.has_co_buyer:
            acc.maybe(f"buying with your {p.buying_with}, but the co buyer details are empty",
                      "fill in co_buyer in your profile")
            return "family"
        if p.age < nucleus["min_age"] or p.co_buyer.age < nucleus["min_age"]:
            acc.no(f"every applicant must be at least {nucleus['min_age']}")
            return "family"
        if "SC" not in (p.citizenship, p.co_buyer.citizenship) or p.co_buyer.citizenship not in ("SC", "PR") \
                or p.citizenship not in ("SC", "PR"):
            acc.no("the household needs one Singapore citizen and one citizen or PR")
            return "family"
        acc.ok(f"family nucleus with your {p.buying_with}")
        return "family"
    if p.buying_with == "sibling":
        acc.maybe("buying with a sibling is allowed only under the Orphans Scheme",
                  "check the Orphans Scheme conditions with HDB")
        return "family"
    # singles
    min_age = singles["min_age"]
    if p.citizenship != singles["citizenship"]:
        acc.no("singles must be Singapore citizens to buy an HDB flat")
        return "single"
    if p.age < min_age or (p.buying_with == "joint_singles" and p.has_co_buyer and p.co_buyer.age < min_age):
        years = min_age - p.age
        acc.no(f"singles must be {min_age} or older; you are {p.age}",
               f"eligible from age {min_age}, in about {years} years ({today.year + years}), "
               f"or now if you buy with a fiance, a spouse or your parents")
        return "single"
    if not allow_singles:
        acc.no(single_note, "eligible if buying with a fiance or spouse")
        return "single"
    acc.ok(f"single aged {p.age}, under the Single Singapore Citizen or Joint Singles Scheme")
    return "single"


def _income_ceiling(p: Profile, rules: Rules, trace: RuleTrace, acc: _Acc, key: str, label: str) -> bool:
    ceiling = rules.v(trace, "hdb_income_ceiling", key)
    income = household_income(p)
    pending = (rules.get("hdb_income_ceiling").pending_change or {})
    new = (pending.get("value") or {}).get(key)
    if income <= ceiling:
        acc.ok(f"household income within the {label} ceiling of S${ceiling:,.0f}")
        return True
    if new and income <= new and not pending.get("verified", False):
        acc.maybe(f"household income S${income:,.0f} is above the confirmed {label} ceiling of S${ceiling:,.0f} "
                  f"but within the announced S${new:,.0f}, pending confirmation",
                  "eligible once HDB confirms the raised ceiling")
        return True
    acc.no(f"household income S${income:,.0f} is above the {label} ceiling of S${ceiling:,.0f}")
    return False


def _private_wait(p: Profile, rules: Rules, trace: RuleTrace, today: date, acc: _Acc, *, subsidised: bool,
                  with_hdb_loan: bool = True) -> bool:
    wait = rules.v(trace, "private_owner_wait_out")
    if p.owns_private_now:
        if subsidised:
            months = wait["subsidised_flats"]["months"]
            acc.no(f"you own private property; subsidised flats need it sold {months} months before applying",
                   f"sell the private property and wait {months} months")
            return False
        resale = wait["resale_non_subsidised"]
        if resale["applies_only_with_hdb_loan"]:
            acc.maybe("you own private property; without an HDB loan there is no wait out (2026 change), "
                      f"with an HDB loan you must wait {resale['months']} months after selling",
                      "sell the private property, or buy without an HDB loan")
            return False
        acc.no(f"you own private property; wait {resale['months']} months after selling")
        return False
    disposed = _parse(p.disposed_private_date)
    if disposed:
        months_since = _months_between(disposed, today)
        if subsidised:
            months = wait["subsidised_flats"]["months"]
            if months_since < months:
                acc.no(f"sold private property {months_since} months ago; subsidised flats need {months}",
                       f"eligible from {_add_months(disposed, months).isoformat()}")
                return False
        else:
            resale = wait["resale_non_subsidised"]
            if months_since < resale["months"] and p.age < resale["exempt_from_age"]:
                if resale["applies_only_with_hdb_loan"]:
                    acc.maybe(f"sold private property {months_since} months ago: no HDB loan until "
                              f"{_add_months(disposed, resale['months']).isoformat()}; a bank loan is fine",
                              f"HDB loan allowed from {_add_months(disposed, resale['months']).isoformat()}")
                    return False
                acc.no(f"sold private property {months_since} months ago; wait {resale['months']} months")
                return False
    return True


def _hdb_loan_ok(p: Profile, rules: Rules, trace: RuleTrace, today: date, family: bool) -> bool:
    if p.citizenship != "SC" and not (p.has_co_buyer and p.co_buyer.citizenship == "SC"):
        return False
    key = "hdb_loan_family" if family else "hdb_loan_singles"
    if household_income(p) > rules.v(trace, "hdb_income_ceiling", key):
        return False
    if p.owns_private_now:
        return False
    disposed = _parse(p.disposed_private_date)
    wait = rules.v(trace, "private_owner_wait_out", "resale_non_subsidised")
    if disposed and _months_between(disposed, today) < wait["months"] and p.age < wait["exempt_from_age"]:
        return False
    return True


def _bto(p: Profile, cand: Candidate, rules: Rules, trace: RuleTrace, today: date, acc: _Acc) -> bool:
    rooms = flat_rooms(cand.flat_type)
    singles = rules.v(trace, "hdb_singles")
    kind = _nucleus(p, rules, trace, today, acc, allow_singles=True, single_note="")
    if kind == "single" and acc.state is not False and rooms and rooms != "2":
        acc.no(f"singles may buy only {', '.join(singles['bto_flat_types']).replace('-', ' ')} BTO flats",
               "eligible for a 4 room BTO if buying with a fiance or spouse")
    family = kind == "family"
    _income_ceiling(p, rules, trace, acc, "bto_family" if family else "bto_singles",
                    "BTO family" if family else "BTO singles")
    _private_wait(p, rules, trace, today, acc, subsidised=True)
    if p.hdb_flat_mop_end:
        acc.maybe("you own an HDB flat; second timer rules apply and the current flat must be sold",
                  "check second timer priority and the resale levy with HDB")
    if p.first_timer:
        ehg = rules.v(trace, "ehg")
        cap = ehg["family_income_ceiling"] if family else ehg["singles_income_ceiling"]
        top = ehg["family_max"] if family else ehg["singles_max"]
        if household_income(p) <= cap:
            acc.grants.append({"name": "EHG", "amount": None, "up_to": top, "counted": False,
                               "note": "tier by income not confirmed yet, not counted"})
    return _hdb_loan_ok(p, rules, trace, today, family)


def _resale(p: Profile, cand: Candidate, rules: Rules, trace: RuleTrace, today: date, acc: _Acc) -> bool:
    cls = (cand.bto_classification or "").lower()
    if cls in ("plus", "prime"):
        conds = rules.v(trace, "plus_prime_resale_buyers")
        kind = _nucleus(p, rules, trace, today, acc, allow_singles=False,
                        single_note=f"resale {cls.title()} flats follow BTO conditions and need a family nucleus")
        _income_ceiling(p, rules, trace, acc, "bto_family", "BTO family")
        _private_wait(p, rules, trace, today, acc, subsidised=True)
        acc.notes.append(f"{cls.title()} resale: same conditions as BTO, wait out {conds['private_wait_out_months']} months")
    else:
        kind = _nucleus(p, rules, trace, today, acc, allow_singles=True, single_note="")
        _private_wait(p, rules, trace, today, acc, subsidised=False)
    family = kind == "family"
    if p.first_timer and acc.state is not False:
        g = rules.v(trace, "cpf_housing_grant_resale")
        rooms = flat_rooms(cand.flat_type)
        big = rooms == "5"
        table = g["family"] if family else g["singles"]
        amount = table["five_room_or_larger" if big else "up_to_4_room"]
        acc.grants.append({"name": "CPF Housing Grant", "amount": None, "up_to": amount, "counted": False,
                           "note": "income ceiling for this grant not in the rules file yet, not counted"})
        acc.grants.append({"name": "Proximity Housing Grant", "amount": None, "counted": False,
                           "up_to": rules.v(trace, "proximity_housing_grant",
                                            "family_live_with" if family else "singles_live_with"),
                           "note": "only if living with or near parents, not counted"})
    return _hdb_loan_ok(p, rules, trace, today, family)


def _ec(p: Profile, cand: Candidate, rules: Rules, trace: RuleTrace, today: date, acc: _Acc) -> None:
    ec = rules.v(trace, "ec_rules")
    if is_single(p) and not ec["singles_new_ec"]:
        _nucleus(p, rules, trace, today, acc, allow_singles=False,
                 single_note="singles cannot buy a new EC from the developer")
    else:
        _nucleus(p, rules, trace, today, acc, allow_singles=True, single_note="")
    _income_ceiling(p, rules, trace, acc, "ec", "EC")
    _private_wait(p, rules, trace, today, acc, subsidised=True)
    acc.notes.append("bank loan only, MSR applies until the MOP ends")


def _private(p: Profile, cand: Candidate, rules: Rules, trace: RuleTrace, today: date, acc: _Acc,
             *, landed: bool) -> None:
    if landed:
        allowed = rules.v(trace, "landed_eligibility", p.citizenship if p.citizenship in ("SC", "PR") else "foreigner")
        if allowed == "allowed":
            acc.ok("Singapore citizens may buy landed homes")
        else:
            acc.maybe("landed homes need Land Dealings Approval Unit approval for non citizens",
                      "apply to the Land Dealings Approval Unit")
    else:
        acc.ok("anyone may buy a private condo")
    mop_end = _parse(p.hdb_flat_mop_end)
    if mop_end and mop_end > today:
        rules.v(trace, "hdb_mop", "restrictions")
        acc.no(f"you own an HDB flat still in its MOP until {mop_end.isoformat()}",
               f"eligible from {mop_end.isoformat()}, or after selling the flat")


def _commercial(p: Profile, cand: Candidate, rules: Rules, trace: RuleTrace, today: date, acc: _Acc,
                *, company: bool) -> None:
    key = cand.category_key
    rules.v(trace, "commercial_loan_planning", "cpf_allowed")
    if key == "shophouse":
        z = (cand.zoning or "").lower()
        if z == "commercial":
            acc.ok("commercial zoned, anyone may buy, no ABSD")
        elif z in ("residential", "mixed"):
            rules.v(trace, "mixed_use_apportionment", "residential_rates_on_residential_part")
            if p.citizenship == "SC" or company:
                acc.ok(f"{z} zoning: ABSD and residential rules apply to the residential part")
            else:
                rules.v(trace, "landed_eligibility", "PR")
                acc.maybe(f"{z} zoning: the residential part is restricted for non citizens",
                          "apply to the Land Dealings Approval Unit, or choose a commercial zoned unit")
        else:
            acc.maybe("zoning unknown: ABSD and the loan type depend on it",
                      "check the zoning on the URA Master Plan before an offer")
        acc.notes.append("conservation status may restrict works")
    elif key == "hdb_shop":
        rules.v(trace, "hdb_commercial", "sale_needs_hdb_approval")
        acc.maybe("the sale and the trade need HDB approval", "HDB approves the transfer and the trade")
    elif key == "coffeeshop":
        rules.v(trace, "hdb_commercial", "rental_coffeeshops_tendered_not_sold")
        acc.maybe("only privately owned coffee shops can be bought; HDB approval and an SFA licence are needed",
                  "HDB approves the transfer; the operator holds the SFA licence")
    elif key == "industrial":
        acc.maybe("JTC or HDB lease conditions apply and the buyer often must be an approved end user",
                  "confirm end user approval with JTC or HDB")
    else:
        acc.ok("anyone may buy commercial strata units")
    if company:
        acc.notes.append("as a company: no ABSD on commercial, 65% ABSD on any residential part, no CPF")
    else:
        acc.notes.append("no CPF for commercial property")


def _lease_notes(cand: Candidate, p: Profile, rules: Rules, trace: RuleTrace, acc: _Acc) -> None:
    if cand.remaining_lease is None or cand.tenure in ("freehold", "999 year"):
        return
    cpf = rules.v(trace, "cpf_housing_usage")
    youngest = min(p.age, p.co_buyer.age) if p.has_co_buyer else p.age
    if info(cand.category_key).kind == "residential":
        if cand.remaining_lease < cpf["min_remaining_lease_years"]:
            acc.notes.append(f"no CPF: remaining lease under {cpf['min_remaining_lease_years']} years")
        elif cand.remaining_lease < cpf["lease_cover_age"] - youngest:
            acc.notes.append(f"CPF use pro rated: the lease does not cover you to age {cpf['lease_cover_age']}")
