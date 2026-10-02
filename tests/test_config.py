import pytest

from propbot.config import (ConfigError, INCOME_MESSAGE, apply_profile_overrides, check_safety_floors,
                            floor_violations, load_settings, parse_override_value)

from .conftest import ROOT


def base():
    return load_settings(ROOT / "config.yaml", base_dir=ROOT)


def test_defaults_match_spec():
    s = base()
    assert s.run.daily_target_messages == 100
    assert s.run.outlooks_per_day == 10
    assert s.limits.web.per_domain_min_gap_seconds == 6
    assert s.limits.web.max_requests_per_day == 220
    assert s.limits.telegram.min_gap_seconds == 3.5
    assert s.limits.telegram.max_per_minute == 17
    assert s.limits.claude.max_calls_per_day == 12
    assert s.assumptions.base_cagr_cap_pct == 4.0
    assert s.profile.citizenship == "SC" and s.profile.age == 29
    assert s.enabled_categories() == ["bto", "hdb_resale", "ec", "condo_resale", "condo_new_launch",
                                      "shophouse", "hdb_shop", "coffeeshop", "strata_commercial"]
    assert s.profile.intent == ["rent_out", "flip"]
    assert s.sources.datagov.datasets["hdb_resale_prices"].startswith("d_")


def test_refuses_without_income_with_clear_message():
    s = base()
    with pytest.raises(ConfigError) as exc:
        check_safety_floors(s)
    assert "profile.gross_monthly_income" in str(exc.value)
    assert INCOME_MESSAGE in str(exc.value)
    check_safety_floors(s, require_income=False)


@pytest.mark.parametrize("key,value", [
    ("limits.web.per_domain_min_gap_seconds", 2),
    ("limits.web.max_requests_per_day", 301),
    ("limits.web.respect_robots_txt", False),
    ("limits.telegram.min_gap_seconds", 0.5),
    ("limits.telegram.max_per_minute", 21),
    ("limits.claude.max_calls_per_day", 13),
    ("limits.claude.max_analyses_per_day", 31),
    ("assumptions.base_cagr_cap_pct", 6.5),
])
def test_each_floor_names_its_key(key, value):
    s = apply_profile_overrides(base(), {"gross_monthly_income": 5000})
    section, *rest = key.split(".")
    obj = getattr(s, section)
    for part in rest[:-1]:
        obj = getattr(obj, part)
    setattr(obj, rest[-1], value)
    problems = floor_violations(s)
    assert len(problems) == 1 and problems[0].startswith(key)


def test_floor_values_at_the_limit_are_allowed():
    s = apply_profile_overrides(base(), {"gross_monthly_income": 5000})
    s.limits.web.per_domain_min_gap_seconds = 3
    s.limits.web.per_domain_max_gap_seconds = 4
    s.limits.web.max_requests_per_day = 300
    s.limits.telegram.max_per_minute = 20
    s.assumptions.base_cagr_cap_pct = 6
    assert floor_violations(s) == []


def test_profile_override_and_unknown_field():
    s = apply_profile_overrides(base(), {"gross_monthly_income": parse_override_value("6500"),
                                         "co_buyer.age": 30})
    assert s.profile.gross_monthly_income == 6500
    assert s.profile.co_buyer.age == 30
    with pytest.raises(ConfigError):
        apply_profile_overrides(s, {"salary": 1})


def test_unknown_config_key_is_rejected(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text((ROOT / "config.yaml").read_text() + "\nunexpected_section: 1\n")
    with pytest.raises(ConfigError):
        load_settings(bad, base_dir=tmp_path)


def test_secrets_never_print(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:SECRET")
    s = base()
    assert "SECRET" not in repr(s.secrets)
