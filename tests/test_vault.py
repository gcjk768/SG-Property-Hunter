from datetime import date

import pytest

from propbot.alerts import Alerts
from propbot.config import apply_profile_overrides
from propbot.engine.card import build_card
from propbot.models import Rates
from propbot.rules.loader import Rules
from propbot.vault import MY_NOTES, NullVault, Vault, split_frontmatter

from . import worked_examples as W
from .conftest import ROOT

RULES = Rules.load(ROOT / "rules" / "sg_property_rules.yaml")


@pytest.fixture
def vault(tmp_path):
    v = Vault(tmp_path, "propbot")
    v.ensure_templates()
    return v


def test_templates_and_profile_read(vault):
    assert vault.read("Profile.md") and vault.read("Watchlist.md")
    assert vault.profile_overrides() == {}            # template lines are comments
    vault.write("Profile.md", "---\ngross_monthly_income: 6500\nco_buyer:\n  age: 30\n---\nbody\n")
    assert vault.profile_overrides() == {"gross_monthly_income": 6500, "co_buyer.age": 30}


def test_watchlist_skips_examples_in_code(vault):
    assert vault.watchlist() == []                    # the template's example is in backticks
    vault.write("Watchlist.md", "# w\n- hdb_resale, Tampines, 600000, 1001, 99 year, 68, 3200\n- just a note\n")
    assert vault.watchlist() == ["hdb_resale, Tampines, 600000, 1001, 99 year, 68, 3200"]


def test_never_writes_outside_folder(vault):
    with pytest.raises(ValueError):
        vault.write("../escape.md", "x")
    with pytest.raises(ValueError):
        vault.path("/etc/passwd")


def test_missing_vault_root_is_not_created(tmp_path):
    v = Vault(tmp_path / "not_mounted", "propbot")
    assert v.available() is False and not (tmp_path / "not_mounted").exists()
    v.activity("x", "y")                               # carries on quietly
    assert NullVault().available() is False


def test_card_note_keeps_my_notes_and_logs(vault, settings):
    s = apply_profile_overrides(settings, W.PLACEHOLDERS)
    card = build_card(W.condo_ocr_1500k(), s, RULES,
                      Rates("2026-10-02", 3.0, 2.6, 2.5, source="test"), date(2026, 10, 2), with_fair_price=False)
    link = vault.write_card(card, "card text")
    rel = link + ".md"
    meta, body = split_frontmatter(vault.read(rel))
    assert meta["verdict"] == "Not affordable" and meta["price"] == 1_500_000 and "propbot" in meta["tags"]
    vault.write(rel, vault.read(rel).replace(MY_NOTES + "\n", MY_NOTES + "\nViewing on Saturday.\n"))
    vault.write_card(card, "card text, updated")
    text = vault.read(rel)
    assert "Viewing on Saturday." in text and "card text, updated" in text
    month = vault.now().strftime("%Y-%m")
    assert vault.read(f"Activity/{month}.md").count("`card`") == 2


def test_alerts_rules_daily_and_favourites(vault, db):
    Alerts(db, vault=vault).send("domain_cooldown", "edgeprop.sg cooling down", key="edgeprop.sg")
    vault.write_rules(RULES)
    vault.rule_change({"rule_id": "mas_tdsr", "title": "TDSR", "old_value": 55, "new_value": 50, "url": "https://x"})
    vault.write_daily("2026-10-02", [{"link": "Cards/2026-10-02/a", "name": "A", "verdict": "Marginal", "score": 2.5,
                                      "posted": True}])
    vault.favourite("Cards/2026-10-02/a", 42)
    month = vault.now().strftime("%Y-%m")
    log = vault.read(f"Activity/{month}.md")
    for kind in ("`alert`", "`rules_changed`", "`favourite`"):
        assert kind in log
    assert "TDSR" in vault.read("Rules/Changes.md")
    assert "Total Debt Servicing Ratio" in vault.read("Rules/Rules.md")
    assert "[[Cards/2026-10-02/a" in vault.read("Daily/2026-10-02.md")
    vault.journal("fetch", "https://edgeprop.sg/a status 200", {"stream": "web"})
    assert "edgeprop" in vault.read(f"Activity/{month} web.md")
