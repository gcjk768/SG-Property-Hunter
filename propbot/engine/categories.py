"""How each category behaves in the engine. Classification only; every legal figure stays
in the rules file."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CategoryInfo:
    key: str
    kind: str              # residential, commercial, industrial, mixed (decided by zoning for shophouses)
    hdb_flat: bool         # an HDB flat (bank LTV and MSR rules for HDB flats apply)
    private_residential: bool
    loan: str              # hdb_or_bank, bank, commercial
    maintenance_keys: tuple[str, ...]
    vacancy_key: str
    mop_key: str | None    # key inside rules hdb_mop, None when there is no MOP
    under_construction: bool = False
    ssd_kind: str = "residential"   # residential, industrial or none


CATEGORIES: dict[str, CategoryInfo] = {
    "bto": CategoryInfo("bto", "residential", True, False, "hdb_or_bank", ("bto", "hdb"), "residential",
                        "bto", under_construction=True),
    "hdb_resale": CategoryInfo("hdb_resale", "residential", True, False, "hdb_or_bank", ("hdb_resale", "hdb"),
                               "residential", "hdb_resale_years"),
    "ec": CategoryInfo("ec", "residential", False, False, "bank", ("ec",), "residential", "ec_years",
                       under_construction=True),
    "condo_resale": CategoryInfo("condo_resale", "residential", False, True, "bank", ("condo_resale", "condo"),
                                 "residential", None),
    "condo_new_launch": CategoryInfo("condo_new_launch", "residential", False, True, "bank",
                                     ("condo_new_launch", "condo"), "residential", None, under_construction=True),
    "landed": CategoryInfo("landed", "residential", False, True, "bank", ("landed",), "residential", None),
    "shophouse": CategoryInfo("shophouse", "mixed", False, False, "commercial", ("shophouse",), "commercial",
                              None, ssd_kind="residential"),
    "hdb_shop": CategoryInfo("hdb_shop", "commercial", False, False, "commercial", ("hdb_shop",), "commercial",
                             None, ssd_kind="none"),
    "coffeeshop": CategoryInfo("coffeeshop", "commercial", False, False, "commercial", ("coffeeshop",),
                               "commercial", None, ssd_kind="none"),
    "strata_commercial": CategoryInfo("strata_commercial", "commercial", False, False, "commercial",
                                      ("strata_commercial",), "commercial", None, ssd_kind="none"),
    "industrial": CategoryInfo("industrial", "industrial", False, False, "commercial", ("industrial",),
                               "commercial", None, ssd_kind="industrial"),
}


def info(category_key: str) -> CategoryInfo:
    try:
        return CATEGORIES[category_key]
    except KeyError as exc:
        raise ValueError(f"unknown category: {category_key}") from exc


def residential_share(category_key: str, zoning: str, share_pct: float | None, default_share_pct: float) -> float:
    """Fraction of the price treated as residential for duties, CPF and SSD."""
    cat = info(category_key)
    if cat.kind == "residential":
        return 1.0
    if cat.kind in ("commercial", "industrial"):
        return 0.0
    zoning = (zoning or "").lower()
    if zoning == "commercial":
        return 0.0
    if zoning == "residential":
        return 1.0
    if zoning == "mixed":
        return (share_pct if share_pct is not None else default_share_pct) / 100.0
    return 0.0  # unknown zoning: the card says so and the eligibility engine marks it conditional
