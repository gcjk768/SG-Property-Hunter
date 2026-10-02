"""Projection, yearly table and the four worked examples against an independent hand calculation.

The reference functions below are written straight from the brief's definitions, without
using the engine, so a mistake in the engine shows up as a mismatch.
"""
from dataclasses import replace

import pytest

from propbot.config import apply_profile_overrides
from propbot.engine.card import build_card
from propbot.engine.projection import decay_pct, irr, npv
from propbot.models import Rates
from propbot.rules.loader import Rules

from . import worked_examples as W
from .conftest import ROOT

RULES = Rules.load(ROOT / "rules" / "sg_property_rules.yaml")
RATES = Rates(date="2026-10-02", bank_rate_today=3.0, hdb_rate=2.6, cpf_oa=2.5, source="test")


@pytest.fixture
def s(settings):
    return apply_profile_overrides(settings, W.PLACEHOLDERS)


def card_for(s, cand, **profile):
    if profile:
        s = apply_profile_overrides(s, profile)
    return build_card(cand, s, RULES, RATES, W.TODAY)


# ------------------------------------------------------------ independent reference pieces
def ref_balance(principal, rate_pct, years, months_paid):
    r = rate_pct / 1200
    n = years * 12
    pay = principal * r / (1 - (1 + r) ** -n)
    return principal * (1 + r) ** months_paid - pay * ((1 + r) ** months_paid - 1) / r


def ref_interest_in_year(principal, rate_pct, years, year):
    r = rate_pct / 1200
    n = years * 12
    pay = principal * r / (1 - (1 + r) ** -n)
    bal, total = principal, 0.0
    for m in range(1, year * 12 + 1):
        i = bal * r
        if m > (year - 1) * 12:
            total += i
        bal -= pay - i
    return total


def tier(amount, bands):
    out, rem = 0.0, amount
    for width, rate in bands:
        part = rem if width is None else min(rem, width)
        out += part * rate / 100
        rem -= part
    return out


OO = [(12000, 0), (28000, 4), (10000, 6), (25000, 10), (10000, 14), (15000, 20), (40000, 26), (None, 32)]
NOO = [(30000, 12), (15000, 20), (15000, 28), (None, 36)]


# ------------------------------------------------------------ worked example 1: 600,000 four room resale
def test_worked_hdb_resale_600k(s):
    c = card_for(s, W.hdb_resale_600k())
    base = c.proj.base
    # value: 3% a year with lease decay 0.7% (68 to 61 years left) then 1.2% (60 and 59 years left)
    v10 = 600_000 * 1.03 ** 10 * 0.993 ** 8 * 0.988 ** 2
    assert base.rows[9].value_end == pytest.approx(v10, abs=1)
    assert round(v10) == 744_100
    # loan: 450,000 bank loan over 25 years at 3% (today and long run rates are both 3% here)
    assert c.fin.option.loan == pytest.approx(450_000)
    b120 = ref_balance(450_000, 3.0, 25, 120)
    assert base.rows[9].loan_balance == pytest.approx(b120, abs=1)
    # flows: years 1 to 5 lived in (MOP), rent saved; years 6 to 10 rented with 1 month vacancy
    pay_year = 12 * 450_000 * 0.0025 / (1 - 1.0025 ** -300)
    flows = [-(150_000 + 12_600 + 3_500 + 6_000 + 40_000)]
    for t in range(1, 11):
        market_rent = 3200 * 12 * 1.015 ** (t - 1)
        maint = 80 * 12 * 1.02 ** (t - 1)
        ins = 300 * 1.02 ** (t - 1)
        if t <= 5:
            cf = market_rent - pay_year - maint - ins - tier(market_rent, OO)
        else:
            rent = market_rent * 11 / 12
            ptax = tier(market_rent, NOO)
            itax = max(0, rent * 0.85 - ref_interest_in_year(450_000, 3.0, 25, t)) * 0.15
            cf = rent - pay_year - maint - ins - ptax - itax
        flows.append(cf)
    flows[5] -= 10_000                                  # furnishing when renting starts, after the MOP
    proceeds = v10 - v10 * 0.02 * 1.09 - 3000 * 1.02 ** 10 - b120
    flows[10] += proceeds
    assert base.net_gain == pytest.approx(sum(flows), abs=2)
    assert base.irr_pct == pytest.approx(irr(flows) * 100, abs=0.01)
    # CPF refund: 100,000 used upfront plus 2.5% a year for 10 years
    assert base.exit_sale["cpf_refund"] == pytest.approx(100_000 * 1.025 ** 10, abs=1)
    # MOP: locked until year 5, sell at MOP exit in year 5
    assert [r.sale_allowed for r in base.rows[:5]] == [False, False, False, False, True]
    assert c.proj.mop_exit.exit_year == 5


# ------------------------------------------------------------ worked example 2: 1,500,000 OCR condo
def test_worked_condo_1500k(s):
    c = card_for(s, W.condo_ocr_1500k())
    base = c.proj.base
    v10 = 1_500_000 * 1.025 ** 10 * 0.997 ** 2        # 0.3% decay when 80 and 79 years are left
    assert round(v10) == 1_908_623
    assert base.rows[9].value_end == pytest.approx(v10, abs=1)
    b120 = ref_balance(1_125_000, 3.0, 30, 120)
    pay_year = 12 * 1_125_000 * 0.0025 / (1 - 1.0025 ** -360)
    flows = [-(375_000 + 44_600 + 3_500 + 30_000 + 10_000)]
    for t in range(1, 11):
        market_rent = 4500 * 12 * 1.015 ** (t - 1)
        rent = market_rent * 11 / 12
        maint = 350 * 12 * 1.02 ** (t - 1)
        ins = 300 * 1.02 ** (t - 1)
        itax = max(0, rent * 0.85 - ref_interest_in_year(1_125_000, 3.0, 30, t)) * 0.15
        flows.append(rent - pay_year - maint - ins - tier(market_rent, NOO) - itax)
    flows[10] += v10 - v10 * 0.02 * 1.09 - 3000 * 1.02 ** 10 - b120
    assert base.net_gain == pytest.approx(sum(flows), abs=2)
    assert base.irr_pct == pytest.approx(irr(flows) * 100, abs=0.01)
    assert c.proj.mop_years is None and c.proj.ssd_end_date == "2030-10"


def test_condo_flip_pays_ssd(s):
    c = card_for(s, W.condo_ocr_1500k())
    flip = c.proj.intents["flip"]
    assert flip["exit_year"] == 3
    v3 = 1_500_000 * 1.025 ** 3
    assert flip["ssd"] == pytest.approx(v3 * 0.08, abs=1)      # sold within 3 years: 8%


# ------------------------------------------------------------ worked example 3: 4,000,000 shophouse
def test_worked_shophouse_4m(s):
    c = card_for(s, W.shophouse_4m())
    d, base = c.duties, c.proj.base
    assert d.bsd == pytest.approx(169_600) and d.absd == 0 and d.gst == 0
    assert c.fin.option.lender == "bank, commercial" and c.fin.option.stress_rate_pct == 5.0
    # TDSR room 5,500 a month at the 5% non residential floor over 30 years
    r = 0.05 / 12
    loan = 5500 * (1 - (1 + r) ** -360) / r
    assert c.fin.option.loan == pytest.approx(loan, abs=0.01)
    v10 = 4_000_000 * 1.03 ** 10
    assert base.rows[9].value_end == pytest.approx(v10, abs=1)
    assert c.proj.ssd_rates == [] and c.proj.mop_years is None
    # commercial: 2 months vacancy, 10% property tax, tax on rent after actual expenses
    y1 = base.rows[0]
    assert y1.rent == pytest.approx(12000 * 10)
    assert y1.property_tax == pytest.approx(144_000 * 0.10)
    assert c.fin.cpf_used == 0 and c.deal.fees.furnishing == 0


# ------------------------------------------------------------ worked example 4: 500,000 BTO four room, Plus
def test_worked_bto_plus_500k(s):
    c = card_for(s, W.bto_plus_500k())
    proj, base = c.proj, c.proj.base
    assert c.deal.build_years == 4                       # completion 2030
    assert c.deal.mop_end_year == 14                      # 4 years building plus a 10 year MOP
    assert proj.exit_year == 14                           # a year 10 sale is not allowed
    assert all(not r.sale_allowed for r in base.rows[:13]) and base.rows[13].sale_allowed
    # no rent at all: Plus flats can never be rented out whole; rent saved only while living in
    assert all(r.rent == 0 for r in base.rows)
    assert all(r.rent_saved == 0 for r in base.rows[:4])  # building years
    # subsidy recovery 6% (earlier PLH figure, no project figure typed in) of the value at exit
    v14 = 500_000 * 1.03 ** 14
    assert base.exit_sale["subsidy_recovery"] == pytest.approx(v14 * 0.06, abs=1)
    # staged payments: option fee and 20% (bank loan) at the Agreement for Lease, the rest at keys
    pays = {p.label.split(",")[0]: p for p in c.fin.payments}
    assert pays["Option fee"].amount == 2000
    assert pays["Agreement for Lease"].amount == pytest.approx(98_000)
    assert pays["Balance of downpayment at key collection"].year == 4
    assert sum(p.cpf for p in c.fin.payments) == pytest.approx(100_000)
    assert all(p.cpf <= p.amount + 0.01 for p in c.fin.payments)


def test_standard_bto_earns_no_rent_during_mop(s):
    cand = replace(W.bto_plus_500k(), bto_classification="standard", completion_year=2026)
    c = card_for(s, cand)
    assert c.deal.mop_end_year == 5
    rows = c.proj.base.rows
    assert all(r.rent == 0 for r in rows[:5]) and rows[5].rent > 0
    assert c.proj.mop_exit is None or c.proj.mop_exit.exit_year == 5


# ------------------------------------------------------------ yearly table rules
def test_value_changes_sum_to_total_change(s):
    for make in W.ALL.values():
        c = card_for(s, make())
        rows = c.proj.base.rows
        total = sum(r.change for r in rows)
        assert total == pytest.approx(rows[-1].value_end - rows[0].value_start, abs=0.01)
        for r in rows:
            assert r.change == pytest.approx(r.market_change - r.decay_change, abs=0.01)


def test_lease_decay_bands():
    table = {"above_80_years": 0.0, "70_to_80_years": 0.3, "60_to_70_years": 0.7, "50_to_60_years": 1.2,
             "below_50_years": 2.0}
    assert [decay_pct(x, False, table) for x in (85, 80, 71, 70, 61, 60, 51, 50, 30)] == \
        [0.0, 0.3, 0.3, 0.7, 0.7, 1.2, 1.2, 2.0, 2.0]
    assert decay_pct(40, True, table) == 0.0


def test_mop_exit_years_by_type(s):
    resale = card_for(s, W.hdb_resale_600k())
    assert resale.proj.mop_years == 5 and resale.proj.mop_exit.exit_year == 5
    std = card_for(s, replace(W.bto_plus_500k(), bto_classification="standard", completion_year=2026))
    assert std.proj.mop_years == 5
    plus = card_for(s, replace(W.bto_plus_500k(), completion_year=2026))
    assert plus.proj.mop_years == 10 and plus.proj.exit_year == 10
    prime = card_for(s, replace(W.bto_plus_500k(), bto_classification="prime", completion_year=2026))
    assert prime.proj.mop_years == 10
    ec = card_for(s, replace(W.condo_ocr_1500k(), category_key="ec", completion_year=2026))
    assert ec.proj.mop_years == 5
    condo = card_for(s, W.condo_ocr_1500k())
    assert condo.proj.mop_years is None and condo.proj.mop_exit is None


# ------------------------------------------------------------ solvers
def test_irr_solver_known_values():
    assert irr([-100, 110]) == pytest.approx(0.10, abs=1e-6)
    assert irr([-1000, 0, 0, 1331]) == pytest.approx(0.10, abs=1e-6)
    assert irr([-1000, 50, 50, 1050]) == pytest.approx(0.05, abs=1e-6)
    assert irr([-1000, 0, 500]) == pytest.approx(-0.2929, abs=1e-4)
    assert irr([100, 100]) is None
    assert npv(0.1, [-100, 110]) == pytest.approx(0)


def test_break_even_is_first_allowed_year_with_no_loss(s):
    c = card_for(s, W.condo_ocr_1500k())
    rows = c.proj.base.rows
    be = c.proj.base.breakeven_year
    assert rows[be - 1].net_if_sold >= 0
    assert all(r.net_if_sold < 0 for r in rows[:be - 1])


def test_fair_price_solver_converges(s):
    c = card_for(s, W.condo_ocr_1500k())
    fair = c.proj.fair_price
    assert fair and not c.proj.fair_price_note
    at_fair = card_for(s, replace(W.condo_ocr_1500k(), price=fair))
    assert at_fair.proj.base.irr_pct == pytest.approx(s.profile.benchmark_return_pct, abs=0.05)


def test_scenarios_and_sensitivities(s):
    c = card_for(s, W.condo_ocr_1500k())
    p = c.proj
    assert (p.bear_cagr, p.base_cagr, p.bull_cagr) == (-0.5, 2.5, 4.5)
    assert p.bear.net_gain < p.base.net_gain < p.bull.net_gain
    assert p.irr_rate_up_pct < p.base.irr_pct and p.irr_growth_down_pct < p.base.irr_pct


def test_base_cagr_is_capped_and_bear_floored(s):
    from propbot.models import Growth
    cand = replace(W.condo_ocr_1500k(), growth=Growth(9.0, "loc", 7.0, "nat"))
    c = card_for(s, cand)
    assert c.proj.base_cagr == 4.0 and "capped" in c.proj.cagr_source
    cand = replace(W.condo_ocr_1500k(), growth=Growth(-1.5, "loc", None, ""))
    c = card_for(s, cand)
    assert c.proj.bear_cagr == -2.0


def test_growth_unknown_prints_unknown(s):
    cand = replace(W.condo_ocr_1500k(), growth=None)
    c = card_for(s, cand)
    assert c.proj.base is None and "growth unknown" in c.proj.flags
