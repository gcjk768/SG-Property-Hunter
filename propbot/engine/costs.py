"""Duties, fees and taxes. Pure functions; every legal figure comes from the rules file."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from ..config import AssumptionsCfg, Profile
from ..models import Candidate
from ..rules.loader import RuleTrace, Rules, marginal_rate, tiered
from .categories import info, residential_share


@dataclass
class Duties:
    price: float
    residential_share: float
    bsd: float
    bsd_residential: float
    bsd_non_residential: float
    absd: float
    absd_pct: float
    absd_buyer: str
    absd_count: int
    gst: float
    gst_status: str          # "none", "seller registered", "if seller is GST registered"

    def as_dict(self) -> dict:
        return asdict(self)


def bsd(price: float, rules: Rules, trace: RuleTrace | None, residential: bool) -> float:
    return tiered(price, rules.bsd_bands(trace, residential))


def absd_buyer_type(citizenship: str, company: bool = False) -> str:
    if company:
        return "entity"
    return {"SC": "SC", "PR": "PR"}.get(citizenship, "foreigner")


def absd_profile(profile: Profile, rules: Rules, trace: RuleTrace | None,
                 company: bool = False) -> tuple[str, int, float]:
    """(buyer type, property count after this purchase, rate) for the buyer whose rate is highest.

    IRAS excludes property outside Singapore from the count; joint buyers pay the highest rate.
    """
    rules.v(trace, "absd_property_count", "excludes_overseas")
    own_count = max(0, profile.properties_owned - profile.properties_owned_outside_sg) + 1
    buyers = [(absd_buyer_type(profile.citizenship, company), own_count)]
    if profile.has_co_buyer and not company:
        buyers.append((absd_buyer_type(profile.co_buyer.citizenship), profile.co_buyer.properties_owned + 1))
    best = None
    for btype, count in buyers:
        rate = rules.absd_rate_pct(trace, btype, count)
        if best is None or rate > best[2]:
            best = (btype, count, rate)
    return best


def price_residential_share(cand: Candidate, rules: Rules, trace: RuleTrace | None) -> float:
    needs_default = (info(cand.category_key).kind == "mixed" and (cand.zoning or "").lower() == "mixed"
                     and cand.residential_share_pct is None)
    default_share = rules.v(trace, "mixed_use_apportionment",
                            "commercial_and_residential_zoned_default_residential_share_pct") if needs_default else 0.0
    return residential_share(cand.category_key, cand.zoning, cand.residential_share_pct, default_share)


def stamp_duties(cand: Candidate, price: float, profile: Profile, rules: Rules, trace: RuleTrace | None,
                 *, company: bool = False) -> Duties:
    share = price_residential_share(cand, rules, trace)
    res_part = price * share
    non_part = price - res_part
    # Mixed use: each rate table is applied to the whole consideration and the result is
    # apportioned by the residential share (propbot's computation method, stated on the card).
    bsd_res = tiered(price, rules.bsd_bands(trace, True)) * share if share > 0 else 0.0
    bsd_non = tiered(price, rules.bsd_bands(trace, False)) * (1 - share) if share < 1 else 0.0
    absd = 0.0
    absd_pct = 0.0
    btype, count = absd_buyer_type(profile.citizenship, company), 1
    if share > 0:
        btype, count, absd_pct = absd_profile(profile, rules, trace, company=company)
        absd = res_part * absd_pct / 100.0
    gst = 0.0
    gst_status = "none"
    if non_part > 0:
        if cand.gst_seller_registered is False:
            gst_status = "seller not GST registered"
        else:
            rate = rules.v(trace, "gst_rate", "rate_pct")
            rules.v(trace, "gst_property", "non_residential_sale_by_registered_seller")
            gst = non_part * rate / 100.0
            gst_status = "seller registered" if cand.gst_seller_registered else "if seller is GST registered"
    return Duties(price, share, bsd_res + bsd_non, bsd_res, bsd_non, absd, absd_pct, btype, count, gst, gst_status)


def ssd_amount(sale_value: float, years_held_index: int, rates_pct: list[float], residential_share: float = 1.0) -> float:
    """SSD when sold in year `years_held_index` (1 = within the first year)."""
    if years_held_index < 1 or years_held_index > len(rates_pct):
        return 0.0
    return sale_value * residential_share * rates_pct[years_held_index - 1] / 100.0


def ssd_rates_for(cand: Candidate, rules: Rules, trace: RuleTrace | None, acquired: date,
                  residential_share: float = 1.0) -> tuple[list[float], str]:
    kind = info(cand.category_key).ssd_kind
    if kind == "none" or (kind == "residential" and residential_share <= 0):
        rules.v(trace, "ssd_scope", "applies_to_types")
        return [], "none"
    return rules.ssd_rates_pct(trace, kind, acquired), kind


@dataclass
class PurchaseFees:
    legal: float
    valuation: float
    agent: float
    renovation: float
    furnishing: float
    option_fee: float = 0.0
    items: list[tuple[str, float]] = field(default_factory=list)

    @property
    def total(self) -> float:
        return self.legal + self.valuation + self.agent + self.renovation + self.furnishing


def purchase_fees(cand: Candidate, price: float, a: AssumptionsCfg, *, renting_out: bool,
                  residential_share: float = 1.0) -> PurchaseFees:
    agent_pct = a.by_key(a.buy_agent_fee_pct, cand.category_key)
    reno = a.by_key(a.renovation_sgd, cand.category_key)
    furnishing = a.furnishing_for_rent_sgd if renting_out and residential_share > 0 else 0.0
    return PurchaseFees(legal=a.legal_fee_sgd, valuation=a.valuation_fee_sgd, agent=price * agent_pct / 100.0,
                        renovation=reno, furnishing=furnishing)


def maintenance_monthly(cand: Candidate, a: AssumptionsCfg) -> tuple[float, str]:
    for key in info(cand.category_key).maintenance_keys:
        if key in a.maintenance_monthly_sgd:
            return float(a.maintenance_monthly_sgd[key]), key
    return 0.0, ""


def property_tax_year(annual_value: float, *, residential_share: float, owner_occupied: bool,
                      rules: Rules, trace: RuleTrace | None) -> float:
    """Property tax on an annual value. Residential part at owner occupied or non owner occupied
    rates; non residential part at the flat rate."""
    tax = 0.0
    if residential_share > 0:
        key = "owner_occupied_residential" if owner_occupied else "non_owner_occupied_residential"
        tax += tiered(annual_value * residential_share, rules.bands(trace, "property_tax", key))
    if residential_share < 1:
        tax += annual_value * (1 - residential_share) * rules.v(trace, "property_tax", "non_residential_pct") / 100.0
    return tax


def marginal_tax_rate(profile: Profile, rules: Rules, trace: RuleTrace | None) -> tuple[float, str]:
    if profile.marginal_income_tax_rate_pct > 0:
        return profile.marginal_income_tax_rate_pct, "your profile"
    yearly = 12 * (profile.gross_monthly_income + profile.variable_monthly_income)
    rate = marginal_rate(yearly, rules.bands(trace, "income_tax_resident", "bands"))
    return rate, "IRAS resident bands on your yearly income"


def tax_on_rent(gross_rent: float, interest: float, other_expenses: float, *, residential: bool,
                marginal_pct: float, rules: Rules, trace: RuleTrace | None) -> float:
    """Residential: deemed 15 percent expenses plus mortgage interest. Others: actual expenses."""
    if gross_rent <= 0:
        return 0.0
    if residential:
        deemed = rules.v(trace, "rental_income_tax", "deemed_expense_pct") / 100.0
        taxable = gross_rent * (1 - deemed) - interest
    else:
        taxable = gross_rent - interest - other_expenses
    return max(0.0, taxable) * marginal_pct / 100.0
