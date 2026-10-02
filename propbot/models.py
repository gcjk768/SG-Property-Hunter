"""Data models shared by the data layer, the engine and the renderer."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .config import Profile  # noqa: F401  (re exported: Profile lives with the settings)

PriceLabel = Literal["asking", "launch price", "transacted", "reported", "unknown", "typed in"]
EvidenceLevel = Literal["page", "snippet", "typed", "official"]


@dataclass
class Comparable:
    label: str
    price: float | None
    psf: float | None
    date: str
    source: str
    url: str = ""


@dataclass
class ComparablesSummary:
    median_psf: float | None = None
    median_price: float | None = None
    n: int = 0
    source: str = ""
    url: str = ""
    period: str = "12 months"
    area_label: str = ""
    median_rent: float | None = None
    rent_n: int = 0
    rent_source: str = ""
    items: list[Comparable] = field(default_factory=list)


@dataclass
class Growth:
    """Historical growth inputs for the projection, each with its source."""
    location_cagr: float | None = None
    location_source: str = ""
    national_cagr: float | None = None
    national_source: str = ""


@dataclass
class Rates:
    date: str
    bank_rate_today: float
    hdb_rate: float
    cpf_oa: float
    sora_3m: float | None = None
    tbill: float | None = None
    source: str = ""
    note: str = ""


@dataclass
class Candidate:
    category_key: str
    name: str
    price: float | None
    area: str = ""
    address: str = ""
    district: str = ""
    segment: str = ""                 # CCR, RCR or OCR for private homes
    price_label: str = "asking"
    price_source: str = ""
    price_date: str = ""
    size_sqft: float | None = None
    tenure: str = "unknown"           # freehold, 999 year, 99 year, 60 year, 30 year, unknown
    lease_start: int | None = None
    remaining_lease: float | None = None
    floor: str = ""
    built_year: int | None = None
    completion_year: int | None = None
    rent_asking: float | None = None
    url: str = ""
    evidence_level: str = "page"
    flat_type: str = ""               # HDB flat type, for example "4 ROOM"
    bto_classification: str = ""      # standard, plus or prime
    subsidy_recovery_pct: float | None = None   # project figure from the HDB launch page
    zoning: str = ""                  # shophouses: commercial, residential or mixed
    residential_share_pct: float | None = None
    gst_seller_registered: bool | None = None
    market_value: float | None = None
    market_value_source: str = ""
    comparables: ComparablesSummary | None = None
    growth: Growth | None = None
    rent_estimate: float | None = None
    rent_source: str = ""
    signals: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    repeat_note: str = ""
    more_sources: list[tuple[str, str]] = field(default_factory=list)
    id: int | None = None

    @property
    def psf(self) -> float | None:
        if self.price and self.size_sqft:
            return self.price / self.size_sqft
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EligibilityResult:
    eligible: bool | str                    # True, False or "conditional"
    reasons: list[str]
    rule_ids: list[str]
    what_would_change_it: list[str]
    hdb_loan_allowed: bool = False
    grants: list[dict] = field(default_factory=list)   # {name, amount or None, counted, note}
    absd_buyer: str = ""
    absd_count: int = 1
    notes: list[str] = field(default_factory=list)

    @property
    def is_eligible(self) -> bool:
        return self.eligible is True or self.eligible == "conditional"
