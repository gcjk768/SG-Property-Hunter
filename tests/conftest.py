import random
from pathlib import Path

import pytest

from propbot.config import apply_profile_overrides, load_settings
from propbot.db import DB
from propbot.ratelimit import FakeClock, RateLimiter

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def settings(tmp_path):
    s = load_settings(ROOT / "config.yaml", base_dir=ROOT)
    s = apply_profile_overrides(s, {"gross_monthly_income": 10000, "cash_available": 300000,
                                    "cpf_oa_balance": 100000})
    # keep runtime files inside the test's temp folder
    s = s.model_copy(update={"base_dir": tmp_path})
    (tmp_path / "rules").symlink_to(ROOT / "rules")
    (tmp_path / "prompts").symlink_to(ROOT / "prompts")
    return s


@pytest.fixture
def db():
    d = DB(":memory:")
    yield d
    d.close()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def limiter(db, settings, clock):
    return RateLimiter(db, settings, clock=clock, rng=random.Random(7), run_id="run-test")
