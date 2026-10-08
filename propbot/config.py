"""Load config.yaml and .env into typed settings, and enforce the safety floors.

The bot refuses to start when a limit is loosened past its floor, naming the key,
and when the profile has no income (affordability cannot be computed without it).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ConfigError(Exception):
    """Configuration is invalid or breaks a safety floor."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- profile
Intent = Literal["rent_out", "live_then_sell", "flip"]


class CoBuyer(_Strict):
    citizenship: Literal["", "SC", "PR", "foreigner"] = ""
    age: int = 0
    gross_monthly_income: float = 0
    cpf_oa: float = 0
    properties_owned: int = 0

    @property
    def present(self) -> bool:
        return bool(self.citizenship) and self.age > 0


class Profile(_Strict):
    citizenship: Literal["SC", "PR", "foreigner"] = "SC"
    age: int = 29
    first_timer: bool = True
    marital_status: Literal["single", "engaged", "married"] = "single"
    buying_with: Literal["none", "fiance", "spouse", "parents", "sibling", "joint_singles"] = "none"
    co_buyer: CoBuyer = Field(default_factory=CoBuyer)
    gross_monthly_income: float = 0
    variable_monthly_income: float = 0
    self_employed: bool = False
    monthly_debt_repayments: float = 0
    cpf_oa_balance: float = 0
    cash_available: float = 0
    properties_owned: int = 0
    properties_owned_outside_sg: int = 0
    housing_loans_outstanding: int = 0
    owns_private_now: bool = False
    disposed_private_date: str = ""
    hdb_flat_mop_end: str = ""
    marginal_income_tax_rate_pct: float = 0
    intent: list[Intent] = Field(default_factory=lambda: ["rent_out", "live_then_sell", "flip"])
    hold_years: int = 10
    benchmark_return_pct: float = 4.0
    max_monthly_commitment_pct: float = 35

    @field_validator("hold_years")
    @classmethod
    def _hold(cls, v: int) -> int:
        if not 1 <= v <= 30:
            raise ValueError("hold_years must be between 1 and 30")
        return v

    @property
    def has_co_buyer(self) -> bool:
        return self.buying_with != "none" and self.co_buyer.present


# ---------------------------------------------------------------- sections
class TelegramCfg(_Strict):
    chat_id: str = "@your_channel_or_numeric_id"
    thread_id: int = 0            # forum topic in a shared group; 0 = no topic
    owner_user_id: int = 0
    allowed_user_ids: list[int] = Field(default_factory=list)   # friends who may use the bot in their private chat
    admin_chat_id: str = ""
    replace_previous: bool = True
    notify_types: list[str] = Field(default_factory=lambda: ["rules_update"])


class RunCfg(_Strict):
    daily_target_messages: int = 100
    outlooks_per_day: int = 10
    category_weights: dict[str, float] = Field(default_factory=lambda: {
        "bto": 8, "hdb_resale": 14, "ec": 8, "condo_resale": 16, "condo_new_launch": 12,
        "shophouse": 8, "hdb_shop": 7, "coffeeshop": 5, "strata_commercial": 12})
    repeat_days: int = 7
    candidates_per_category: int = 25
    categories_refreshed_per_run: int = 9
    cache_max_age_days: int = 7
    prepare_cron: str = "0 6 * * *"
    post_cron: str = "30 8 * * *"
    timezone: str = "Asia/Singapore"
    avoid_us_session: bool = True   # no scheduled checks while the trading desk trades (US regular session)
    rules_check_cron: str = "0 5 * * 1"
    rules_max_age_days: int = 10
    max_run_minutes: int = 120
    curation_batch_size: int = 25


class CategoryCfg(_Strict):
    label: str
    tag: str
    enabled: bool = True
    budget_max_sgd: float | None = None   # overrides search.budget_max_sgd (commercial units cost more)


class SearchCfg(_Strict):
    budget_min_sgd: float = 300000
    budget_max_sgd: float = 2500000
    areas: list[str] = Field(default_factory=list)
    min_remaining_lease_years: float = 60
    max_candidates_total: int = 250


class AssumptionsCfg(_Strict):
    rent_growth_pct: float = 1.5
    cost_inflation_pct: float = 2.0
    vacancy_months_per_year: dict[str, float] = Field(default_factory=lambda: {"residential": 1, "commercial": 2})
    base_cagr_cap_pct: float = 4.0
    bear_offset_pct: float = 3.0
    bull_offset_pct: float = 2.0
    long_run_bank_rate_pct: float = 3.0
    bank_spread_over_sora_pct: float = 0.8
    sale_agent_fee_pct: float = 2.0
    buy_agent_fee_pct: dict[str, float] = Field(default_factory=lambda: {"hdb_resale": 1.0, "default": 0.0})
    legal_fee_sgd: float = 3000
    valuation_fee_sgd: float = 500
    renovation_sgd: dict[str, float] = Field(default_factory=lambda: {
        "hdb_resale": 40000, "condo_resale": 30000, "bto": 30000, "default": 0})
    furnishing_for_rent_sgd: float = 10000
    maintenance_monthly_sgd: dict[str, float] = Field(default_factory=lambda: {
        "hdb": 80, "ec": 300, "condo": 350, "shophouse": 400, "strata_commercial": 500, "coffeeshop": 800})
    insurance_yearly_sgd: float = 300
    count_rent_saved_when_living_in: bool = True
    lease_decay: dict[str, float] = Field(default_factory=lambda: {
        "above_80_years": 0.0, "70_to_80_years": 0.3, "60_to_70_years": 0.7,
        "50_to_60_years": 1.2, "below_50_years": 2.0})
    benchmarks: dict[str, float] = Field(default_factory=lambda: {
        "cpf_oa_pct": 2.5, "tbill_pct": 2.0, "balanced_portfolio_pct": 5.0})

    def by_key(self, table: dict[str, float], key: str) -> float:
        return float(table.get(key, table.get("default", 0.0)))


class DatagovCfg(_Strict):
    enabled: bool = True
    datasets: dict[str, str] = Field(default_factory=dict)


class ToggleCfg(_Strict):
    enabled: bool = True


class SourcesCfg(_Strict):
    datagov: DatagovCfg = Field(default_factory=DatagovCfg)
    ura: ToggleCfg = Field(default_factory=ToggleCfg)
    onemap: ToggleCfg = Field(default_factory=ToggleCfg)
    listing_domains_allowed: list[str] = Field(default_factory=list)
    never_fetch_domains: list[str] = Field(default_factory=list)


class DiscoveryCfg(_Strict):
    queries_per_category: int = 3
    query_templates: dict[str, list[str]] = Field(default_factory=dict)
    prefer_days: int = 45


class ClaudeCallCfg(_Strict):
    max_turns: int
    timeout_seconds: int
    max_budget_usd: float
    calls_per_run: int = 1


class ClaudeCfg(_Strict):
    binary: str = "claude"
    model: str = "sonnet"
    fallback_model: str = "haiku"
    auth: Literal["oauth", "apikey"] = "oauth"
    max_retries: int = 4
    discovery: ClaudeCallCfg = Field(default_factory=lambda: ClaudeCallCfg(
        max_turns=100, timeout_seconds=1800, max_budget_usd=3.0, calls_per_run=2))
    curation: ClaudeCallCfg = Field(default_factory=lambda: ClaudeCallCfg(
        max_turns=4, timeout_seconds=600, max_budget_usd=1.0))
    rules_check: ClaudeCallCfg = Field(default_factory=lambda: ClaudeCallCfg(
        max_turns=40, timeout_seconds=900, max_budget_usd=1.5))
    ask: ClaudeCallCfg = Field(default_factory=lambda: ClaudeCallCfg(
        max_turns=8, timeout_seconds=300, max_budget_usd=0.5))
    no_tools: list[str] = Field(default_factory=lambda: [
        "Bash", "Edit", "Write", "Read", "Glob", "Grep", "WebFetch", "WebSearch", "Agent", "NotebookEdit"])


class WebLimits(_Strict):
    per_domain_min_gap_seconds: float = 6
    per_domain_max_gap_seconds: float = 12
    max_requests_per_run: int = 180
    max_requests_per_day: int = 220
    retries: int = 2
    backoff_seconds: list[float] = Field(default_factory=lambda: [30, 120])
    domain_cooldown_hours: float = 24
    page_cache_days: int = 7
    respect_robots_txt: bool = True
    user_agent: str = "propbot/1.0 (personal property research, low volume)"


class OfficialApiLimits(_Strict):
    datagov_requests_per_day: int = 200
    ura_requests_per_day: int = 100
    onemap_requests_per_day: int = 300
    min_gap_seconds: float = 1.0


class TelegramLimits(_Strict):
    min_gap_seconds: float = 3.5
    max_per_minute: int = 17
    max_retries: int = 5
    max_retry_after_seconds: float = 900
    delete_batch_size: int = 100


class ClaudeLimits(_Strict):
    max_calls_per_run: int = 10
    max_calls_per_day: int = 12
    max_analyses_per_day: int = 10


class LimitsCfg(_Strict):
    web: WebLimits = Field(default_factory=WebLimits)
    official_apis: OfficialApiLimits = Field(default_factory=OfficialApiLimits)
    telegram: TelegramLimits = Field(default_factory=TelegramLimits)
    claude: ClaudeLimits = Field(default_factory=ClaudeLimits)


class PulseCfg(_Strict):
    """Hourly HDB resale pulse from data.gov.sg: posts only new, notable deals."""
    enabled: bool = True
    check_minute: int = 7           # minute past every hour
    median_months: int = 12         # town and flat type median over this many months
    min_sales_for_median: int = 5
    value_discount_pct: float = 15  # notable when this far below the median price per sqm
    min_yield_pct: float = 8.0      # or when the estimated gross yield reaches this
    max_items: int = 0             # 0 = all


class HuntCfg(_Strict):
    """Hourly listing hunt: claude -p web search for real listings, one card each, new ones only."""
    enabled: bool = True
    check_minute: int = 37
    per_run: int = 10


class CondoReportCfg(_Strict):
    """Weekly condo report (resale and new launch) as a PDF, posted once on this weekday from this hour."""
    enabled: bool = True
    weekday: int = 6                # Monday is 0, so 6 is Sunday
    hour: int = 9                   # Asia/Singapore
    resale_count: int = 20
    new_launch_count: int = 10


class ObsidianCfg(_Strict):
    enabled: bool = False
    vault_path: str = "/vault"
    folder: str = "propbot"
    read_profile: bool = True
    log_web_requests: bool = True


class Secrets(BaseModel):
    telegram_bot_token: str = ""
    claude_code_oauth_token: str = ""
    anthropic_api_key: str = ""
    ura_access_key: str = ""
    onemap_email: str = ""
    onemap_password: str = ""
    onemap_access_token: str = ""      # a token pasted from OneMap; expires after about 3 days
    tz: str = "Asia/Singapore"

    def __repr__(self) -> str:  # never print secrets
        set_keys = [k for k, v in self.model_dump().items() if v and k != "tz"]
        return f"Secrets(set={set_keys})"

    __str__ = __repr__


class Settings(_Strict):
    profile: Profile = Field(default_factory=Profile)
    telegram: TelegramCfg = Field(default_factory=TelegramCfg)
    run: RunCfg = Field(default_factory=RunCfg)
    categories: dict[str, CategoryCfg] = Field(default_factory=dict)
    search: SearchCfg = Field(default_factory=SearchCfg)
    assumptions: AssumptionsCfg = Field(default_factory=AssumptionsCfg)
    sources: SourcesCfg = Field(default_factory=SourcesCfg)
    discovery: DiscoveryCfg = Field(default_factory=DiscoveryCfg)
    claude: ClaudeCfg = Field(default_factory=ClaudeCfg)
    limits: LimitsCfg = Field(default_factory=LimitsCfg)
    obsidian: ObsidianCfg = Field(default_factory=ObsidianCfg)
    pulse: PulseCfg = Field(default_factory=PulseCfg)
    hunt: HuntCfg = Field(default_factory=HuntCfg)
    condo_report: CondoReportCfg = Field(default_factory=CondoReportCfg)

    # filled by load_settings, not part of config.yaml
    base_dir: Path = Field(default=Path("."), exclude=True)
    secrets: Secrets = Field(default_factory=Secrets, exclude=True)

    @property
    def data_dir(self) -> Path:
        return self.base_dir / "data"

    @property
    def rules_dir(self) -> Path:
        return self.base_dir / "rules"

    @property
    def prompts_dir(self) -> Path:
        return self.base_dir / "prompts"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "propbot.sqlite3"

    def enabled_categories(self) -> list[str]:
        return [k for k, c in self.categories.items() if c.enabled]


# ---------------------------------------------------------------- safety floors
# (dotted key, test, message). A floor protects other people's servers, my Telegram
# account, my Claude plan, or the honesty of the projections.
_FLOORS: list[tuple[str, Any, str]] = [
    ("limits.web.per_domain_min_gap_seconds", lambda v: v >= 3, "must be at least 3"),
    ("limits.web.max_requests_per_day", lambda v: v <= 300, "must be at most 300"),
    ("limits.web.respect_robots_txt", lambda v: v is True, "must be true"),
    ("limits.telegram.min_gap_seconds", lambda v: v >= 1.0, "must be at least 1.0"),
    ("limits.telegram.max_per_minute", lambda v: v <= 20, "must be at most 20"),
    ("limits.claude.max_calls_per_day", lambda v: v <= 48, "must be at most 48"),  # hourly haiku hunt plus asks
    ("limits.claude.max_analyses_per_day", lambda v: v <= 30, "must be at most 30"),
    ("assumptions.base_cagr_cap_pct", lambda v: v <= 6, "must be at most 6"),
]

INCOME_MESSAGE = (
    "profile.gross_monthly_income is 0. propbot cannot work out what you can afford "
    "(loan limit, TDSR, MSR, instalment share) without your fixed monthly income. "
    "Fill it in under profile: in config.yaml, or set it from the private chat with "
    "/set gross_monthly_income 6500, then start again."
)


def _get(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def floor_violations(settings: Settings, *, require_income: bool = True) -> list[str]:
    problems = []
    for key, ok, msg in _FLOORS:
        value = _get(settings, key)
        if not ok(value):
            problems.append(f"{key} is {value!r}; it {msg}")
    if settings.limits.web.per_domain_max_gap_seconds < settings.limits.web.per_domain_min_gap_seconds:
        problems.append("limits.web.per_domain_max_gap_seconds must not be below per_domain_min_gap_seconds")
    if require_income and settings.profile.gross_monthly_income <= 0:
        problems.append(INCOME_MESSAGE)
    return problems


def check_safety_floors(settings: Settings, *, require_income: bool = True) -> None:
    problems = floor_violations(settings, require_income=require_income)
    if problems:
        raise ConfigError("propbot refuses to start:\n  " + "\n  ".join(problems))


# ---------------------------------------------------------------- loading
def _read_env(base_dir: Path) -> Secrets:
    values: dict[str, str] = {}
    env_file = base_dir / ".env"
    if env_file.exists():
        values.update({k: v or "" for k, v in dotenv_values(env_file).items()})
    for key in ("TELEGRAM_BOT_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY",
                "URA_ACCESS_KEY", "ONEMAP_EMAIL", "ONEMAP_PASSWORD", "ONEMAP_ACCESS_TOKEN", "TZ"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return Secrets(**{k.lower(): v for k, v in values.items() if k.lower() in Secrets.model_fields})


def apply_profile_overrides(settings: Settings, overrides: dict[str, Any]) -> Settings:
    """Return settings with profile fields replaced (from /set or --set)."""
    if not overrides:
        return settings
    data = settings.profile.model_dump()
    for key, value in overrides.items():
        if key.startswith("co_buyer."):
            data["co_buyer"][key.split(".", 1)[1]] = value
        elif key in data:
            data[key] = value
        else:
            raise ConfigError(f"unknown profile field: {key}")
    try:
        profile = Profile(**data)
    except ValidationError as exc:
        raise ConfigError(f"invalid profile override: {exc}") from exc
    return settings.model_copy(update={"profile": profile})


def parse_override_value(raw: str) -> Any:
    """Parse a /set or --set value with YAML so numbers, booleans and lists work."""
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


def load_settings(config_path: str | Path | None = None, base_dir: str | Path | None = None) -> Settings:
    base = Path(base_dir or os.environ.get("PROPBOT_HOME") or Path.cwd()).resolve()
    path = Path(config_path) if config_path else base / "config.yaml"
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        settings = Settings(**raw)
    except ValidationError as exc:
        raise ConfigError(f"config.yaml is invalid:\n{exc}") from exc
    settings.base_dir = base
    settings.secrets = _read_env(base)
    unknown = set(settings.run.category_weights) - set(settings.categories)
    if unknown:
        raise ConfigError(f"run.category_weights has unknown categories: {sorted(unknown)}")
    return settings
