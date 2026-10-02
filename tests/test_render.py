import re
from dataclasses import replace
from datetime import date

import pytest

from propbot.config import apply_profile_overrides
from propbot.engine.card import build_card
from propbot.models import Rates
from propbot.render import (allowed_numbers, check_generated_text, clean, has_dash, kfmt, money, pct, render_listing,
                            table_change, table_sold)
from propbot.rules.loader import Rules

from . import worked_examples as W
from .conftest import ROOT

RULES = Rules.load(ROOT / "rules" / "sg_property_rules.yaml")
RATES = Rates(date="2026-10-02", bank_rate_today=3.0, hdb_rate=2.6, cpf_oa=2.5, source="assumed",
              note="test")


def cards(settings, **over):
    s = apply_profile_overrides(settings, {**W.PLACEHOLDERS, **over})
    return s, [build_card(make(), s, RULES, RATES, date(2026, 10, 2)) for make in W.ALL.values()]


def test_formatters():
    assert money(1234.4) == "S$1,234" and money(-1234) == "loss of S$1,234" and money(None) == "unknown"
    assert pct(2.345) == "2.3%" and pct(-1.25) == "minus 1.2%" and pct(None) == "unknown"
    assert kfmt(1_182_400) == "1,182k"
    assert table_change(2.44) == "+2.4%" and table_change(-1.1) == "dep 1.1%"
    assert table_sold(-52_000, True) == "loss 52k" and table_sold(9_000, True) == "+9k"
    assert table_sold(1, False) == "locked"
    assert clean("Blk 123 #05-12 Tampines St 11 - near MRT") == "Blk 123 #05 12 Tampines St 11, near MRT"
    assert clean("2–room — Flexi") == "2 room, Flexi"


def test_every_card_under_limits_no_none_no_dashes(settings):
    s, cs = cards(settings)
    for c in cs:
        msg = render_listing(c, s, run_time="2026-10-02 08:30")
        assert len(msg) < 4096
        assert "None" not in msg and "nan" not in msg.lower().split()
        assert not has_dash(msg), [line for line in msg.splitlines() if has_dash(line)]
        assert msg.rstrip().endswith("SGT")
        assert "Model estimate, not financial advice. Rules as of 2026-10-02." in msg


def test_one_message_per_item(settings):
    s, cs = cards(settings)
    msgs = [render_listing(c, s) for c in cs]
    assert len(msgs) == 4 and all(m.count("Model estimate, not financial advice") == 1 for m in msgs)


def test_not_eligible_cards_carry_no_verdict_words(settings):
    s, cs = cards(settings)
    hdb = cs[0]
    hdb.curated = {"verdict_why": "Strong case.", "risks": ["x"], "what_would_make_it_work": "y"}
    msg = render_listing(hdb, s)
    assert "Not eligible yet" in msg
    assert "Strong case" not in msg and "Risks:" not in msg and "What would make it work" not in msg
    assert "/5)" not in msg


def test_curated_words_appear_for_eligible_cards(settings):
    s, cs = cards(settings, cash_available=900_000)
    condo = cs[1]
    msg = render_listing(condo, s)
    allowed = allowed_numbers(msg)
    words = "The base case IRR of 4.8% sits just above your target, and the rent does not cover the instalment."
    assert check_generated_text(words, allowed) == []
    condo.curated = {"verdict_why": words, "risks": ["thin evidence"], "what_would_make_it_work": "a lower price"}
    msg2 = render_listing(condo, s)
    assert words in msg2 and "Risks: thin evidence" in msg2


def test_generated_text_checks(settings):
    s, cs = cards(settings, cash_available=900_000)
    allowed = allowed_numbers(render_listing(cs[1], s))
    assert check_generated_text("Prices rose 7.7% last year.", allowed) == ["number 7.7 is not on the card"]
    assert "contains a dash" in check_generated_text("a well-located flat", allowed)
    assert any("promise" in p for p in check_generated_text("This is guaranteed to rise.", allowed))
    assert any("will" in p for p in check_generated_text("Prices will go up.", allowed))
    assert "contains a link" in check_generated_text("see https://x.sg", allowed)


def test_year_table_aligned_and_locked(settings):
    s, cs = cards(settings)
    msg = render_listing(cs[0], s)
    pre = re.search(r"<pre>(.*?)</pre>", msg, re.S).group(1).splitlines()
    assert len({len(line) for line in pre}) == 1          # aligned columns
    assert pre[1].strip().endswith("locked") and not pre[5].strip().endswith("locked")
