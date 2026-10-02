from dataclasses import replace
from datetime import date

import pytest

from propbot.config import apply_profile_overrides
from propbot.engine import eligibility
from propbot.engine.card import build_card
from propbot.engine.verdict import label_for, score
from propbot.models import Candidate, ComparablesSummary, Rates
from propbot.rules.loader import RuleTrace, Rules

from . import worked_examples as W
from .conftest import ROOT

RULES = Rules.load(ROOT / "rules" / "sg_property_rules.yaml")
RATES = Rates(date="2026-10-02", bank_rate_today=3.0, hdb_rate=2.6, cpf_oa=2.5, source="test")
TODAY = date(2026, 10, 2)
FIANCE = {"buying_with": "fiance", "co_buyer.citizenship": "SC", "co_buyer.age": 29,
          "co_buyer.gross_monthly_income": 3000}


def check(settings, cand, **over):
    s = apply_profile_overrides(settings, {**W.PLACEHOLDERS, "gross_monthly_income": 5000, **over})
    return eligibility.check(s.profile, cand, RULES, RuleTrace(), TODAY)


def bto(rooms="4 ROOM", cls="standard"):
    return Candidate("bto", "x", 500_000, flat_type=rooms, bto_classification=cls, tenure="99 year",
                     remaining_lease=99)


def resale(cls=""):
    return Candidate("hdb_resale", "x", 600_000, flat_type="4 ROOM", bto_classification=cls, tenure="99 year",
                     remaining_lease=70)


# ------------------------------------------------------------ singles and nucleus
def test_single_29_not_eligible_for_bto_or_resale(settings):
    for cand in (bto(), resale()):
        r = check(settings, cand)
        assert r.eligible is False
        assert any("35" in x for x in r.what_would_change_it)
        assert any("fiance" in x for x in r.what_would_change_it)
        assert any("2032" in x for x in r.what_would_change_it)


def test_single_35_resale_ok_bto_only_2_room(settings):
    assert check(settings, resale(), age=35).eligible is True
    assert check(settings, bto("4 ROOM"), age=35).eligible is False
    assert check(settings, bto("2 ROOM FLEXI"), age=35).eligible is True


def test_with_fiance_eligible(settings):
    assert check(settings, bto(), **FIANCE).eligible is True
    assert check(settings, resale(), **FIANCE).eligible is True


def test_income_ceiling_and_pending_change(settings):
    r = check(settings, bto(), **{**FIANCE, "gross_monthly_income": 12000, "co_buyer.gross_monthly_income": 3000})
    assert r.eligible == "conditional"          # 15,000 is above 14,000 but within the announced 16,000
    r = check(settings, bto(), **{**FIANCE, "gross_monthly_income": 15000, "co_buyer.gross_monthly_income": 3000})
    assert r.eligible is False


def test_pr_single_cannot_buy_hdb(settings):
    assert check(settings, resale(), age=40, citizenship="PR").eligible is False


# ------------------------------------------------------------ private property owners
def test_private_owner_rules(settings):
    r = check(settings, bto(), owns_private_now=True, **FIANCE)
    assert r.eligible is False and any("30 months" in x for x in r.what_would_change_it)
    r = check(settings, resale(), owns_private_now=True, **FIANCE)
    assert r.eligible == "conditional"           # no wait without an HDB loan since the 2026 change
    r = check(settings, bto(), disposed_private_date="2026-01-01", **FIANCE)
    assert r.eligible is False and any("2028-07" in x for x in r.what_would_change_it)
    r = check(settings, resale(), disposed_private_date="2026-01-01", **FIANCE)
    assert r.eligible == "conditional" and r.hdb_loan_allowed is False
    r = check(settings, resale(), disposed_private_date="2024-01-01", **FIANCE)
    assert r.eligible is True and r.hdb_loan_allowed is True


def test_hdb_mop_blocks_private_purchase(settings):
    condo = Candidate("condo_resale", "x", 1_200_000, tenure="99 year", remaining_lease=90)
    r = check(settings, condo, hdb_flat_mop_end="2028-05-01", properties_owned=1)
    assert r.eligible is False and any("2028-05-01" in x for x in r.what_would_change_it)
    r = check(settings, condo, hdb_flat_mop_end="2025-05-01", properties_owned=1)
    assert r.eligible is True and "second property, ABSD 20%" in r.notes


def test_absd_count_in_eligibility_notes(settings):
    condo = Candidate("condo_resale", "x", 1_200_000, tenure="99 year", remaining_lease=90)
    assert "first property, ABSD 0%" in check(settings, condo).notes
    assert "first property, ABSD 5%" in check(settings, condo, citizenship="PR").notes


def test_plus_prime_resale_conditions(settings):
    assert check(settings, resale("plus"), age=40).eligible is False      # singles cannot
    assert check(settings, resale("prime"), **FIANCE).eligible is True
    assert check(settings, resale("prime"), owns_private_now=True, **FIANCE).eligible is False


def test_commercial_individual_vs_company(settings):
    shop = Candidate("strata_commercial", "x", 1_000_000)
    s = apply_profile_overrides(settings, W.PLACEHOLDERS)
    ind = eligibility.check(s.profile, shop, RULES, RuleTrace(), TODAY)
    co = eligibility.check(s.profile, shop, RULES, RuleTrace(), TODAY, company=True)
    assert ind.eligible is True and any("no CPF" in n for n in ind.notes)
    assert any("65%" in n for n in co.notes)
    sh = Candidate("shophouse", "x", 3_000_000, zoning="mixed", tenure="freehold")
    assert eligibility.check(s.profile, sh, RULES, RuleTrace(), TODAY).eligible is True
    pr = apply_profile_overrides(s, {"citizenship": "PR"})
    assert eligibility.check(pr.profile, sh, RULES, RuleTrace(), TODAY).eligible == "conditional"
    unknown = Candidate("shophouse", "x", 3_000_000, tenure="freehold")
    assert eligibility.check(s.profile, unknown, RULES, RuleTrace(), TODAY).eligible == "conditional"


def test_landed_citizenship(settings):
    landed = Candidate("landed", "x", 3_000_000, tenure="freehold")
    assert check(settings, landed).eligible is True
    assert check(settings, landed, citizenship="PR").eligible == "conditional"


def test_lease_vs_cpf_note(settings):
    old = Candidate("hdb_resale", "x", 300_000, flat_type="3 ROOM", tenure="99 year", remaining_lease=45)
    r = check(settings, old, age=35)
    assert any("pro rated" in n for n in r.notes)


# ------------------------------------------------------------ verdict rubric
def card(settings, cand, **over):
    s = apply_profile_overrides(settings, {**W.PLACEHOLDERS, **over})
    return build_card(cand, s, RULES, RATES, TODAY, with_fair_price=False)


def clause(c, name):
    return next(x for x in c.verdict.clauses if x["clause"] == name)["points"]


@pytest.mark.parametrize("score_,label", [(5, "Worth a serious look"), (4.0, "Worth a serious look"),
                                          (3.5, "Reasonable, with conditions"), (3.0, "Reasonable, with conditions"),
                                          (2.5, "Marginal"), (2.0, "Marginal"), (1.5, "Avoid at this price"),
                                          (0, "Avoid at this price")])
def test_label_boundaries(score_, label):
    assert label_for(score_) == label


def test_return_clause(settings):
    c = card(settings, W.condo_ocr_1500k(), cash_available=900_000)
    assert clause(c, "return") == 1                          # base IRR 4.8% vs benchmark 4%
    c = card(settings, W.condo_ocr_1500k(), cash_available=900_000, benchmark_return_pct=2.0)
    assert clause(c, "return") == 2
    c = card(settings, W.condo_ocr_1500k(), cash_available=900_000, benchmark_return_pct=6.0)
    assert clause(c, "return") == 0


def test_bear_clause(settings):
    c = card(settings, W.condo_ocr_1500k(), cash_available=900_000)
    assert clause(c, "bear case") == 0                       # loses 22% of equity
    c = card(settings, W.shophouse_4m())
    assert clause(c, "bear case") == 1                       # no loss in the bear case
    c = card(settings, replace(W.condo_ocr_1500k(), rent_estimate=1500), cash_available=900_000)
    assert clause(c, "bear case") == -1


def test_affordability_lock_lease_evidence(settings):
    c = card(settings, W.condo_ocr_1500k(), cash_available=900_000, gross_monthly_income=30_000)
    assert clause(c, "affordability") == 1
    c = card(settings, W.condo_ocr_1500k(), cash_available=900_000)
    assert clause(c, "affordability") == 0                  # instalment above the 35% own limit
    assert clause(c, "lock in") == 0.5 and clause(c, "lease") == 0.5
    assert clause(c, "evidence") == -0.5 and "thin evidence" in c.verdict.flags
    plus = card(settings, W.bto_plus_500k(), **FIANCE)
    assert clause(plus, "lock in") == 0
    old = card(settings, replace(W.hdb_resale_600k()), **FIANCE)
    assert clause(old, "lease") == -0.5                      # 58 years left at exit
    good = replace(W.condo_ocr_1500k(), comparables=ComparablesSummary(median_psf=1394, n=12, source="URA"),
                   market_value=1_500_000)
    c = card(settings, good, cash_available=900_000)
    assert not any(x["clause"] == "evidence" for x in c.verdict.clauses)


def test_overrides(settings):
    c = card(settings, W.hdb_resale_600k())
    assert c.verdict.label == "Not eligible yet"
    c = card(settings, W.condo_ocr_1500k())                  # short of cash
    assert c.verdict.label == "Not affordable" and "out of reach" in c.verdict.flags
    c = card(settings, W.condo_ocr_1500k(), cash_available=900_000, monthly_debt_repayments=6000)
    assert c.fin.option.tdsr_pct > 55 and c.verdict.label == "Not affordable"


def test_best_intent_names_flip_with_ssd_warning(settings):
    c = card(settings, W.condo_ocr_1500k(), cash_available=900_000, intent=["flip"])
    assert c.verdict.best_intent == "flip" and "SSD" in c.verdict.best_intent_words
