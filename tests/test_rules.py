import json
from datetime import date

import pytest
import yaml

from propbot.rules.checker import allowed_tool_rules, apply_results, build_stdin, checkable, is_official
from propbot.rules.loader import (REQUIRED_FIELDS, RuleTrace, Rules, RulesError, UnverifiedRule, marginal_rate,
                                  same_shape, tiered)

from .conftest import ROOT

RULES = ROOT / "rules" / "sg_property_rules.yaml"


def load():
    return Rules.load(RULES)


def test_every_rule_has_url_checked_at_and_quote():
    raw = yaml.safe_load(RULES.read_text())["rules"]
    assert len(raw) >= 30
    for rid, entry in raw.items():
        for f in REQUIRED_FIELDS:
            assert f in entry, f"{rid} lacks {f}"
        assert entry["url"].startswith("https://"), rid
        assert entry["quote"].strip(), rid
        date.fromisoformat(entry["checked_at"])


def test_missing_field_fails():
    raw = yaml.safe_load(RULES.read_text())
    del raw["rules"]["bsd_residential"]["quote"]
    with pytest.raises(RulesError) as exc:
        Rules.from_text(yaml.safe_dump(raw))
    assert "bsd_residential: missing field 'quote'" in str(exc.value)


def test_empty_url_fails():
    raw = yaml.safe_load(RULES.read_text())
    raw["rules"]["mas_tdsr"]["url"] = ""
    with pytest.raises(RulesError):
        Rules.from_text(yaml.safe_dump(raw))


def test_typed_accessors_and_trace():
    r = load()
    t = RuleTrace()
    bands = r.bsd_bands(t, residential=True)
    assert bands[0] == (180000.0, 1.0) and bands[-1] == (None, 6.0)
    assert r.absd_rate_pct(t, "SC", 1) == 0.0
    assert r.absd_rate_pct(t, "SC", 2) == 20.0
    assert r.absd_rate_pct(t, "SC", 5) == 30.0
    assert r.absd_rate_pct(t, "foreigner", 1) == 60.0
    assert r.ltv(t, 0, False) == (75.0, 5.0)
    assert r.ltv(t, 0, True) == (55.0, 10.0)
    assert r.ltv(t, 3, False) == (35.0, 25.0)
    assert r.v(t, "mas_tdsr", "limit_pct") == 55
    assert r.ssd_rates_pct(t, "residential", date(2026, 1, 1)) == [16, 12, 8, 4]
    assert r.ssd_rates_pct(t, "residential", date(2024, 1, 1)) == [12, 8, 4]
    assert r.ssd_rates_pct(t, "commercial", date(2026, 1, 1)) == []
    used = t.as_dict()
    assert used["mas_tdsr"]["values"]["limit_pct"] == 55
    assert used["bsd_residential"]["checked_at"] == "2026-10-02"


def test_unverified_rule_is_refused():
    raw = yaml.safe_load(RULES.read_text())
    raw["rules"]["ehg"]["verified"] = False
    r = Rules.from_text(yaml.safe_dump(raw))
    with pytest.raises(UnverifiedRule):
        r.v(None, "ehg", "family_max")


def test_tiered_and_marginal():
    bands = [(20000, 0), (10000, 2), (10000, 3.5), (None, 7)]
    assert tiered(35000, bands) == pytest.approx(200 + 175)
    assert marginal_rate(35000, bands) == 3.5
    assert marginal_rate(45000, bands) == 7


def test_save_roundtrip_keeps_header(tmp_path):
    r = load()
    p = tmp_path / "rules.yaml"
    r.save(p)
    text = p.read_text()
    assert text.startswith("# Singapore property rules")
    again = Rules.load(p)
    assert again.get("bsd_residential").value == r.get("bsd_residential").value


def test_diff_detects_change_and_keeps_history(db):
    r = load()
    today = date(2026, 10, 9)
    new_tdsr = dict(r.get("mas_tdsr").value, limit_pct=50)
    results = [
        {"rule_id": "mas_tdsr", "status": "changed", "value": new_tdsr, "effective_from": "2026-10-08",
         "quote": "The TDSR threshold is 50%.", "url": r.get("mas_tdsr").url},
        {"rule_id": "gst_rate", "status": "unchanged", "value": {"rate_pct": 9}, "effective_from": "2024-01-01",
         "quote": "The GST rate is 9%.", "url": r.get("gst_rate").url},
        {"rule_id": "cpf_oa_rate", "status": "unreadable", "value": None, "effective_from": None, "quote": None,
         "url": r.get("cpf_oa_rate").url},
    ]
    out = apply_results(r, results, today, db=db)
    assert [c["rule_id"] for c in out.changed] == ["mas_tdsr"]
    assert r.get("mas_tdsr").value["limit_pct"] == 50
    assert r.get("mas_tdsr").history[0]["value"]["limit_pct"] == 55
    assert r.get("mas_tdsr").checked_at == "2026-10-09"
    assert "gst_rate" in out.confirmed and r.get("gst_rate").check_method == "official_page"
    assert "cpf_oa_rate" in out.unreadable
    assert db.scalar("SELECT COUNT(*) FROM rules_history WHERE rule_id='mas_tdsr'") == 1


def test_checker_rejects_url_outside_official_domains():
    r = load()
    results = [{"rule_id": "mas_tdsr", "status": "changed", "value": dict(r.get("mas_tdsr").value, limit_pct=40),
                "effective_from": None, "quote": "x", "url": "https://www.propertyguru.com.sg/tdsr"}]
    out = apply_results(r, results, date(2026, 10, 9))
    assert out.rejected and "outside the official domains" in out.rejected[0]["reason"]
    assert r.get("mas_tdsr").value["limit_pct"] == 55
    assert not is_official("http://www.iras.gov.sg/x")
    assert not is_official("https://iras.gov.sg.evil.com/x")
    assert is_official("https://www.iras.gov.sg/x")


def test_shape_change_needs_manual_update():
    r = load()
    results = [{"rule_id": "mas_tdsr", "status": "changed", "value": 50, "effective_from": None,
                "quote": "x", "url": r.get("mas_tdsr").url}]
    out = apply_results(r, results, date(2026, 10, 9))
    assert out.needs_manual and r.get("mas_tdsr").value["limit_pct"] == 55
    assert same_shape({"a": [1, 2]}, {"a": [3]}) and not same_shape({"a": 1}, {"b": 1})


def test_stdin_lists_only_official_checkable_rules():
    r = load()
    stdin = json.loads(build_stdin(r))
    ids = {x["rule_id"] for x in stdin["rules"]}
    assert "commercial_loan_planning" not in ids        # planning figure
    assert "landed_eligibility" not in ids              # sla.gov.sg is outside the checker's domains
    assert "bsd_residential" in ids
    assert all(is_official(x["url"]) for x in stdin["rules"])
    assert len(checkable(r)) == len(ids)
    assert all(x.startswith("WebFetch(domain:") for x in allowed_tool_rules())


def test_needs_check_while_search_index_only():
    r = load()
    assert r.needs_check(date(2026, 10, 3), 10) is True
    assert r.rules_date == "2026-10-02"
