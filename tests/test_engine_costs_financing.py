"""Costs and financing against hand computed figures."""
from datetime import date

import pytest

from propbot.config import apply_profile_overrides
from propbot.engine.costs import absd_profile, bsd, property_tax_year, ssd_amount, stamp_duties, tax_on_rent
from propbot.engine.financing import (assessed_income, bank_commercial, bank_residential, cpf_lease_factor, hdb_loan,
                                      instalment, loan_from_payment)
from propbot.models import Candidate, Rates
from propbot.rules.loader import RuleTrace, Rules

from .conftest import ROOT

RULES = Rules.load(ROOT / "rules" / "sg_property_rules.yaml")
RATES = Rates(date="2026-10-02", bank_rate_today=3.0, hdb_rate=2.6, cpf_oa=2.5, source="test")


def prof(settings, **kw):
    return apply_profile_overrides(settings, kw).profile


# ------------------------------------------------------------ BSD by hand
# 600,000: 1% of 180,000 = 1,800; 2% of 180,000 = 3,600; 3% of 240,000 = 7,200. Total 12,600.
# 1,500,000: 1,800 + 3,600 + 3% of 640,000 = 19,200 + 4% of 500,000 = 20,000. Total 44,600.
# 4,000,000 residential: 44,600 + 5% of 1,500,000 = 75,000 + 6% of 1,000,000 = 60,000. Total 179,600.
# 4,000,000 non residential: 44,600 + 5% of 2,500,000 = 125,000. Total 169,600.
@pytest.mark.parametrize("price,residential,expected", [
    (600_000, True, 12_600), (1_500_000, True, 44_600), (4_000_000, True, 179_600), (4_000_000, False, 169_600),
    (180_000, True, 1_800), (0, True, 0)])
def test_bsd_hand_computed(price, residential, expected):
    assert bsd(price, RULES, None, residential) == pytest.approx(expected)


@pytest.mark.parametrize("over,expected_type,expected_rate", [
    ({}, "SC", 0), ({"properties_owned": 1}, "SC", 20), ({"properties_owned": 2}, "SC", 30),
    ({"citizenship": "PR"}, "PR", 5), ({"citizenship": "PR", "properties_owned": 1}, "PR", 30),
    ({"citizenship": "foreigner"}, "foreigner", 60),
    ({"properties_owned": 1, "properties_owned_outside_sg": 1}, "SC", 0),   # overseas homes do not count
])
def test_absd_profiles(settings, over, expected_type, expected_rate):
    btype, _, rate = absd_profile(prof(settings, **over), RULES, None)
    assert (btype, rate) == (expected_type, expected_rate)


def test_absd_entity_and_co_buyer(settings):
    assert absd_profile(prof(settings), RULES, None, company=True)[2] == 65
    p = prof(settings, buying_with="spouse", **{"co_buyer.citizenship": "PR", "co_buyer.age": 30,
                                                 "co_buyer.gross_monthly_income": 5000})
    assert absd_profile(p, RULES, None)[2] == 5          # the higher profile (PR first property) applies


def test_absd_amount_on_residential_purchase(settings):
    c = Candidate("condo_resale", "x", 1_500_000)
    d = stamp_duties(c, 1_500_000, prof(settings, properties_owned=1), RULES, None)
    assert d.absd == pytest.approx(300_000) and d.bsd == pytest.approx(44_600)


# ------------------------------------------------------------ SSD by month of sale
@pytest.mark.parametrize("acquired,months_held,expected_pct", [
    (date(2026, 1, 10), 6, 16), (date(2026, 1, 10), 12, 16), (date(2026, 1, 10), 13, 12), (date(2026, 1, 10), 30, 8),
    (date(2026, 1, 10), 47, 4), (date(2026, 1, 10), 49, 0),
    (date(2024, 5, 1), 11, 12), (date(2024, 5, 1), 20, 8), (date(2024, 5, 1), 35, 4), (date(2024, 5, 1), 37, 0),
])
def test_ssd_by_month_both_regimes(acquired, months_held, expected_pct):
    rates = RULES.ssd_rates_pct(None, "residential", acquired)
    year_index = -(-months_held // 12)        # sold in month 13 means within the second year
    assert ssd_amount(1_000_000, year_index, rates) == pytest.approx(1_000_000 * expected_pct / 100)


def test_no_ssd_on_commercial_and_ssd_on_industrial():
    assert RULES.ssd_rates_pct(None, "commercial", date(2026, 1, 1)) == []
    assert RULES.ssd_rates_pct(None, "industrial", date(2026, 1, 1)) == [15, 10, 5]


def test_gst_toggle(settings):
    p = prof(settings)
    c = Candidate("strata_commercial", "x", 1_000_000)
    assert stamp_duties(c, 1_000_000, p, RULES, None).gst == pytest.approx(90_000)
    c.gst_seller_registered = False
    assert stamp_duties(c, 1_000_000, p, RULES, None).gst == 0
    res = Candidate("condo_resale", "x", 1_000_000)
    assert stamp_duties(res, 1_000_000, p, RULES, None).gst == 0


# ------------------------------------------------------------ instalment against a known table
def test_instalment_formula_known_values():
    # 450,000 over 25 years at 3%: r = 0.0025, n = 300, payment = 2,133.95 (standard amortisation table)
    assert instalment(450_000, 3.0, 25) == pytest.approx(2133.95, abs=0.01)
    # 1,125,000 over 30 years at 3% = 4,743.05; at 4% = 5,370.92
    assert instalment(1_125_000, 3.0, 30) == pytest.approx(4743.05, abs=0.01)
    assert instalment(1_125_000, 4.0, 30) == pytest.approx(5370.92, abs=0.01)
    # 100,000 over 1 year at 0% = 8,333.33
    assert instalment(100_000, 0.0, 1) == pytest.approx(8333.33, abs=0.01)
    assert loan_from_payment(instalment(500_000, 4.0, 30), 4.0, 30) == pytest.approx(500_000)


def test_amortisation_table_first_months():
    p, r = 450_000.0, 0.03 / 12
    pay = instalment(p, 3.0, 25)
    interest1 = p * r                       # 1,125.00
    principal1 = pay - interest1            # 1,008.95
    assert interest1 == pytest.approx(1125.0)
    assert principal1 == pytest.approx(1008.95, abs=0.01)
    bal = p
    for _ in range(300):
        bal -= pay - bal * r
    assert bal == pytest.approx(0, abs=0.01)


# ------------------------------------------------------------ LTV, tenure, TDSR, MSR, CPF
def cand(cat="condo_resale", lease=None, tenure="99 year"):
    return Candidate(cat, "x", 1_000_000, tenure=tenure, remaining_lease=lease)


@pytest.mark.parametrize("loans,ltv,cash", [(0, 75, 5), (1, 45, 25), (2, 35, 25), (3, 35, 25)])
def test_ltv_and_min_cash_by_loans(settings, loans, ltv, cash):
    p = prof(settings, housing_loans_outstanding=loans, gross_monthly_income=50_000)
    o = bank_residential(cand(lease=90), 1_000_000, 1_000_000, p, RULES, None, RATES)
    assert (o.ltv_pct, o.min_cash_pct, o.loan) == (ltv, cash, pytest.approx(1_000_000 * ltv / 100))


def test_age_65_cap_and_long_tenure_reduction(settings):
    p = prof(settings, age=50, gross_monthly_income=50_000)
    o = bank_residential(cand(lease=95), 1_000_000, 1_000_000, p, RULES, None, RATES)
    assert o.tenure_years == 15 and o.ltv_pct == 75          # 65 minus 50
    p = prof(settings, age=62, gross_monthly_income=50_000)
    o = bank_residential(cand(lease=95), 1_000_000, 1_000_000, p, RULES, None, RATES)
    assert o.reduced_ltv and o.ltv_pct == 55 and o.min_cash_pct == 10 and o.tenure_years == 35


def test_hdb_flat_bank_loan_full_ltv_tenure_25(settings):
    p = prof(settings, gross_monthly_income=50_000)
    o = bank_residential(Candidate("hdb_resale", "x", 600_000, tenure="99 year", remaining_lease=80),
                         600_000, 600_000, p, RULES, None, RATES)
    assert o.tenure_years == 25 and o.msr_applies


def test_hdb_loan_caps(settings):
    p = prof(settings, gross_monthly_income=50_000)
    o = hdb_loan(Candidate("hdb_resale", "x", 600_000, tenure="99 year", remaining_lease=80), 600_000, 600_000, p,
                 RULES, None, RATES)
    assert o.tenure_years == 25 and o.ltv_pct == 75 and o.rate_pct == 2.6 and o.stress_rate_pct == 3.0
    # lease cap: remaining lease 40 minus 20 = 20 years
    o = hdb_loan(Candidate("hdb_resale", "x", 600_000, tenure="99 year", remaining_lease=40), 600_000, 600_000, p,
                 RULES, None, RATES)
    assert o.tenure_years == 20


def test_bank_lease_cap(settings):
    p = prof(settings, gross_monthly_income=50_000)
    o = bank_residential(cand(lease=45), 1_000_000, 1_000_000, p, RULES, None, RATES)
    assert o.tenure_years == 25              # 45 minus the 20 year planning buffer


def test_tdsr_with_debts_and_variable_income(settings):
    p = prof(settings, gross_monthly_income=6000, variable_monthly_income=2000, monthly_debt_repayments=800)
    assert assessed_income(p, RULES, None) == pytest.approx(6000 + 2000 * 0.7)
    o = bank_residential(cand(lease=95), 1_500_000, 1_500_000, p, RULES, None, RATES)
    # TDSR room: 55% of 7,400 = 4,070 minus 800 debts = 3,270 a month at 4% over 30 years
    assert o.binding == "TDSR"
    assert o.loan == pytest.approx(loan_from_payment(3270, 4.0, 30))
    assert o.tdsr_pct == pytest.approx(55.0, abs=0.01)


def test_msr_for_hdb_bank_loan(settings):
    p = prof(settings, gross_monthly_income=5000)
    o = bank_residential(Candidate("hdb_resale", "x", 900_000, tenure="99 year", remaining_lease=80),
                         900_000, 900_000, p, RULES, None, RATES)
    # MSR room: 30% of 5,000 = 1,500 a month at 4% over 25 years
    assert o.binding == "MSR" and o.loan == pytest.approx(loan_from_payment(1500, 4.0, 25))


def test_commercial_loan_planning_and_floor(settings):
    p = prof(settings, gross_monthly_income=50_000)
    o = bank_commercial(Candidate("strata_commercial", "x", 1_000_000, tenure="99 year", remaining_lease=60),
                        1_000_000, 1_000_000, p, RULES, None, RATES)
    assert o.ltv_pct == 70 and o.stress_rate_pct == 5.0 and o.planning_figure and o.tenure_years == 30


def test_cpf_lease_rule_matches_cpf_example():
    # CPF's own example: two 25 year olds, 65 years of lease, may use 90% of the price
    factor, _ = cpf_lease_factor(65, "99 year", 25, RULES, None)
    assert factor == pytest.approx(0.90)
    assert cpf_lease_factor(19, "99 year", 25, RULES, None)[0] == 0
    assert cpf_lease_factor(70, "99 year", 25, RULES, None)[0] == 1
    assert cpf_lease_factor(None, "freehold", 25, RULES, None)[0] == 1


# ------------------------------------------------------------ taxes
def test_property_tax_hand_computed():
    # owner occupied AV 38,400: 0% on 12,000, 4% on 26,400 = 1,056
    assert property_tax_year(38_400, residential_share=1, owner_occupied=True, rules=RULES, trace=None) == pytest.approx(1056)
    # non owner occupied AV 54,000: 12% of 30,000 + 20% of 15,000 + 28% of 9,000 = 9,120
    assert property_tax_year(54_000, residential_share=1, owner_occupied=False, rules=RULES, trace=None) == pytest.approx(9120)
    # non residential: 10% of 144,000
    assert property_tax_year(144_000, residential_share=0, owner_occupied=False, rules=RULES, trace=None) == pytest.approx(14400)


def test_tax_on_rent_deemed_expenses():
    t = RuleTrace()
    # 49,500 rent, 15% deemed, 33,429 interest, 15% marginal: (42,075 minus 33,429) x 15% = 1,296.90
    assert tax_on_rent(49_500, 33_429, 0, residential=True, marginal_pct=15, rules=RULES, trace=t) == pytest.approx(1296.9)
    assert tax_on_rent(10_000, 20_000, 0, residential=True, marginal_pct=15, rules=RULES, trace=t) == 0
    assert "rental_income_tax" in t.used
