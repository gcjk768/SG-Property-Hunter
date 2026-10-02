"""The four worked examples from the brief, as typed in inputs (not market data)."""
from datetime import date

from propbot.models import Candidate, Growth

TODAY = date(2026, 10, 2)
# The profile from config.yaml with the three empty money fields filled by placeholders.
PLACEHOLDERS = {"gross_monthly_income": 10000, "cash_available": 300000, "cpf_oa_balance": 100000}


def g(cagr):
    return Growth(location_cagr=cagr, location_source="typed in")


def hdb_resale_600k() -> Candidate:
    return Candidate("hdb_resale", "Example 4 room resale flat", 600_000, area="Tampines",
                     address="Tampines Street 81", price_label="typed in", price_source="your input",
                     price_date=TODAY.isoformat(), size_sqft=93 * 10.7639, tenure="99 year", remaining_lease=68,
                     built_year=1995, flat_type="4 ROOM", rent_estimate=3200, rent_source="typed in",
                     evidence_level="typed", growth=g(3.0))


def condo_ocr_1500k() -> Candidate:
    return Candidate("condo_resale", "Example OCR condo, 3 bedroom", 1_500_000, area="Punggol",
                     address="Punggol Walk", segment="OCR", price_label="typed in", price_source="your input",
                     price_date=TODAY.isoformat(), size_sqft=1076, tenure="99 year", remaining_lease=88,
                     built_year=2018, rent_estimate=4500, rent_source="typed in", evidence_level="typed",
                     growth=g(2.5))


def shophouse_4m() -> Candidate:
    return Candidate("shophouse", "Example conservation shophouse", 4_000_000, area="Tanjong Pagar",
                     address="Duxton Road", price_label="typed in", price_source="your input",
                     price_date=TODAY.isoformat(), size_sqft=1800, tenure="freehold", zoning="commercial",
                     gst_seller_registered=False, rent_estimate=12000, rent_source="typed in",
                     evidence_level="typed", growth=g(3.0))


def bto_plus_500k() -> Candidate:
    return Candidate("bto", "Example BTO 4 room, Plus project", 500_000, area="Toa Payoh",
                     price_label="typed in", price_source="your input", price_date=TODAY.isoformat(),
                     size_sqft=93 * 10.7639, tenure="99 year", remaining_lease=99, completion_year=2030,
                     flat_type="4 ROOM", bto_classification="plus", rent_estimate=3600, rent_source="typed in",
                     evidence_level="typed", growth=g(3.0))


ALL = {"hdb_resale_600k": hdb_resale_600k, "condo_ocr_1500k": condo_ocr_1500k,
       "shophouse_4m": shophouse_4m, "bto_plus_500k": bto_plus_500k}
