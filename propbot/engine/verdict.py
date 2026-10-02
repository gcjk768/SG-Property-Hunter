"""Deterministic verdict rubric from 0 to 5. Claude cannot change it."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..config import Profile
from ..models import Candidate, EligibilityResult
from .financing import Financing
from .projection import Projection

LABELS = [(4.0, "Worth a serious look"), (3.0, "Reasonable, with conditions"), (2.0, "Marginal"),
          (float("-inf"), "Avoid at this price")]
INTENT_WORDS = {"rent_out": "rent out", "live_then_sell": "live then sell", "flip": "flip"}


@dataclass
class Verdict:
    score: float
    label: str
    clauses: list[dict]
    flags: list[str]
    best_intent: str | None
    best_intent_words: str
    override: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def label_for(score: float) -> str:
    for edge, label in LABELS:
        if score >= edge:
            return label
    return LABELS[-1][1]


def score(profile: Profile, cand: Candidate, elig: EligibilityResult, fin: Financing, proj: Projection) -> Verdict:
    clauses: list[dict] = []
    flags: list[str] = list(dict.fromkeys(cand.flags + proj.flags))
    total = 0.0

    def add(name: str, points: float, why: str) -> None:
        nonlocal total
        total += points
        clauses.append({"clause": name, "points": points, "why": why})

    bench = profile.benchmark_return_pct
    base_irr = proj.base.irr_pct if proj.base else None
    if base_irr is None:
        add("return", 0, "base IRR unknown")
    elif base_irr >= bench + 2:
        add("return", 2, f"base IRR {base_irr:.1f}% is at least 2 points above your {bench:g}%")
    elif base_irr >= bench:
        add("return", 1, f"base IRR {base_irr:.1f}% is at least your {bench:g}%")
    else:
        add("return", 0, f"base IRR {base_irr:.1f}% is below your {bench:g}%")

    if proj.bear:
        equity_in = proj.bear.total_cash_in + proj.bear.total_cpf_in
        loss = -proj.bear.net_gain
        share = loss / equity_in * 100 if equity_in > 0 else 0.0
        if loss <= 0 or share <= 10:
            add("bear case", 1, "bear case loss within 10% of your equity" if loss > 0 else "no loss in the bear case")
        elif share > 25:
            add("bear case", -1, f"bear case loses {share:.0f}% of your equity")
        else:
            add("bear case", 0, f"bear case loses {share:.0f}% of your equity")
    else:
        add("bear case", 0, "bear case unknown")

    opt = fin.option
    own_ok = opt.own_share_pct <= profile.max_monthly_commitment_pct
    if opt.tdsr_pct <= 45 and own_ok:
        add("affordability", 1, f"stress TDSR {opt.tdsr_pct:.1f}% and instalment within your own limit")
    else:
        add("affordability", 0, f"stress TDSR {opt.tdsr_pct:.1f}%" + ("" if own_ok else ", instalment above your own limit"))

    lock = max(proj.mop_years or 0, len(proj.ssd_rates))
    if (proj.mop_years or 0) <= 5 and len(proj.ssd_rates) <= 5:
        add("lock in", 0.5, f"lock in of {lock} years or less" if lock else "no MOP or SSD")
    else:
        add("lock in", 0, f"lock in of {lock} years")

    if cand.tenure in ("freehold", "999 year"):
        add("lease", 0.5, "freehold")
    elif cand.remaining_lease is None and cand.category_key != "bto":
        add("lease", 0, "lease unknown")
    else:
        rem = cand.remaining_lease if cand.remaining_lease is not None else 99
        at_exit = rem - max(0, proj.exit_year - (proj.build_years if cand.category_key == "bto" else 0))
        if at_exit >= 70:
            add("lease", 0.5, f"{at_exit:.0f} years left at exit")
        elif at_exit < 60:
            add("lease", -0.5, f"only {at_exit:.0f} years left at exit")
        else:
            add("lease", 0, f"{at_exit:.0f} years left at exit")

    comps_n = cand.comparables.n if cand.comparables else 0
    if comps_n < 3 or cand.evidence_level == "snippet":
        add("evidence", -0.5, "fewer than 3 comparables" if comps_n < 3 else "price only from a search snippet")
        if "thin evidence" not in flags:
            flags.append("thin evidence")

    total = max(0.0, min(5.0, total))
    label = label_for(total)
    override = ""
    if elig.eligible is False:
        override, label = "not eligible", "Not eligible yet"
    elif opt.tdsr_pct > 55:
        override, label = "TDSR above 55%", "Not affordable"
    elif fin.cash_short > 0:
        override, label = "cash short", "Not affordable"
        if "out of reach" not in flags:
            flags.append("out of reach")
    best = proj.best_intent
    words = INTENT_WORDS.get(best or "", "unknown")
    notes = []
    if best == "flip" and proj.intents.get("flip", {}).get("ssd", 0) > 0:
        notes.append("a flip inside the SSD window pays SSD")
        words += ", SSD applies"
    return Verdict(round(total * 2) / 2, label, clauses, flags, best, words, override, notes)
