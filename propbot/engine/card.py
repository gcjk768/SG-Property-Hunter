"""Run one candidate through eligibility, finance, projection and the verdict rubric."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from ..config import Settings
from ..db import DB
from ..models import Candidate, EligibilityResult, Rates
from ..rules.loader import RuleTrace, Rules
from . import eligibility
from .projection import Deal, Projection, project
from .verdict import Verdict, score


@dataclass
class Card:
    card_id: str
    cand: Candidate
    elig: EligibilityResult
    deal: Deal
    proj: Projection
    verdict: Verdict
    trace: RuleTrace
    rules_date: str
    rates: Rates
    today: str
    company: bool = False
    curated: dict = field(default_factory=dict)

    @property
    def fin(self):
        return self.deal.fin

    @property
    def duties(self):
        return self.deal.duties


def rates_from_db(db: DB | None, settings: Settings, rules: Rules, trace: RuleTrace | None) -> Rates:
    """Latest stored market rates, or a labelled fallback when none are stored."""
    cpf = rules.v(trace, "cpf_oa_rate", "rate_pct")
    spread = rules.v(trace, "hdb_loan", "rate_spread_over_cpf_oa_pct")
    row = db.one("SELECT * FROM rates ORDER BY date DESC LIMIT 1") if db is not None else None
    if row and row["bank_rate"]:
        return Rates(date=row["date"], bank_rate_today=row["bank_rate"],
                     hdb_rate=row["hdb_rate"] or cpf + spread, cpf_oa=row["cpf_oa"] or cpf,
                     sora_3m=row["sora_3m"], tbill=row["tbill"], source="MAS SORA plus your bank spread",
                     note=f"rates from {row['date']}")
    lr = settings.assumptions.long_run_bank_rate_pct
    return Rates(date="", bank_rate_today=lr, hdb_rate=cpf + spread, cpf_oa=cpf, source="assumed",
                 note=f"live SORA not loaded yet, today's bank rate assumed at your long run rate of {lr:g}%")


def build_card(cand: Candidate, settings: Settings, rules: Rules, rates: Rates, today: date, *,
               card_id: str = "", company: bool = False, with_fair_price: bool = True,
               trace: RuleTrace | None = None) -> Card:
    trace = trace or RuleTrace()
    profile = settings.profile
    elig = eligibility.check(profile, cand, rules, trace, today, company=company)
    deal, proj = project(cand, cand.price, profile, settings.assumptions, rules, trace, rates, elig, today,
                         company=company, with_fair_price=with_fair_price)
    verdict = score(profile, cand, elig, deal.fin, proj)
    return Card(card_id or f"c{abs(hash((cand.name, cand.price))) % 10**8}", cand, elig, deal, proj, verdict, trace,
                rules.rules_date, rates, today.isoformat(), company)


def card_row(card: Card, run_id: str, candidate_id: int | None) -> dict:
    """The calculation row stored in the cards table, with every rule value used."""
    def j(obj) -> str:
        return json.dumps(obj, default=_default)
    proj = asdict(card.proj)
    for key in ("base", "bear", "bull", "mop_exit"):
        if proj.get(key):
            for r in proj[key]["rows"]:
                r.pop("sale", None)
    return {"run_id": run_id, "candidate_id": candidate_id, "eligibility_json": j(asdict(card.elig)),
            "costs_json": j({"duties": card.duties.as_dict(), "fees": asdict(card.deal.fees)}),
            "financing_json": j(card.fin.as_dict()), "projection_json": j(proj),
            "verdict_json": j(card.verdict.as_dict()), "rules_used_json": j(card.trace.as_dict()),
            "comparables_json": j(asdict(card.cand.comparables) if card.cand.comparables else None),
            "created_at": datetime.now().isoformat(timespec="seconds")}


def _default(o):
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)
