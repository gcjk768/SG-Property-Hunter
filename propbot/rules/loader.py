"""Load and validate rules/sg_property_rules.yaml and expose typed accessors.

Nothing legal is hardcoded in Python. Every accessor reads the rules file and records the
rule id, the path inside the value, the value used and its checked_at date in a RuleTrace,
so a card can be reproduced later from its stored calculation row.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

REQUIRED_FIELDS = ("title", "value", "unit", "applies_to", "effective_from", "url",
                   "checked_at", "quote", "check_method", "verified")
CHECK_METHODS = {"official_page", "official_search_index", "planning_figure"}


class RulesError(Exception):
    pass


class UnverifiedRule(RulesError):
    """The rule's figure is not confirmed on an official page; it must not be used."""


@dataclass
class Rule:
    id: str
    title: str
    value: Any
    unit: str
    applies_to: list[str]
    effective_from: str | None
    url: str
    checked_at: str
    quote: str
    check_method: str
    verified: bool
    note: str = ""
    pending_change: dict | None = None
    history: list = field(default_factory=list)

    def to_yaml_dict(self) -> dict:
        out = {"title": self.title, "value": self.value, "unit": self.unit, "applies_to": self.applies_to,
               "effective_from": self.effective_from, "url": self.url, "checked_at": self.checked_at,
               "check_method": self.check_method, "verified": self.verified, "quote": self.quote}
        if self.note:
            out["note"] = self.note
        if self.pending_change:
            out["pending_change"] = self.pending_change
        out["history"] = self.history
        return out


class RuleTrace:
    """Every rule value an engine function used, for the calculation row and the card."""

    def __init__(self) -> None:
        self.used: dict[str, dict] = {}

    def record(self, rule: Rule, path: tuple, value: Any) -> None:
        entry = self.used.setdefault(rule.id, {"checked_at": rule.checked_at, "url": rule.url,
                                               "effective_from": rule.effective_from,
                                               "check_method": rule.check_method, "values": {}})
        entry["values"][".".join(str(p) for p in path) or "value"] = copy.deepcopy(value)

    def as_dict(self) -> dict:
        return json.loads(json.dumps(self.used, default=str))

    def ids(self) -> list[str]:
        return sorted(self.used)


def _validate(rule_id: str, entry: Any) -> list[str]:
    problems = []
    if not isinstance(entry, dict):
        return [f"{rule_id}: entry is not a mapping"]
    for f in REQUIRED_FIELDS:
        if f not in entry:
            problems.append(f"{rule_id}: missing field '{f}'")
    if problems:
        return problems
    for f in ("url", "checked_at", "quote", "title", "unit"):
        if not entry.get(f):
            problems.append(f"{rule_id}: field '{f}' is empty")
    if entry["check_method"] not in CHECK_METHODS:
        problems.append(f"{rule_id}: check_method must be one of {sorted(CHECK_METHODS)}")
    if not str(entry.get("url", "")).startswith("https://"):
        problems.append(f"{rule_id}: url must be https")
    try:
        date.fromisoformat(str(entry["checked_at"]))
    except ValueError:
        problems.append(f"{rule_id}: checked_at must be yyyy-mm-dd")
    if entry["effective_from"] is not None:
        try:
            date.fromisoformat(str(entry["effective_from"]))
        except ValueError:
            problems.append(f"{rule_id}: effective_from must be yyyy-mm-dd or null")
    if not isinstance(entry["verified"], bool):
        problems.append(f"{rule_id}: verified must be true or false")
    if not isinstance(entry["applies_to"], list):
        problems.append(f"{rule_id}: applies_to must be a list")
    return problems


class Rules:
    def __init__(self, rules: dict[str, Rule], meta: dict, path: Path | None = None, header: str = ""):
        self.rules = rules
        self.meta = meta
        self.path = path
        self.header = header

    # ------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: str | Path) -> "Rules":
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        return cls.from_text(text, path)

    @classmethod
    def from_text(cls, text: str, path: Path | None = None) -> "Rules":
        data = yaml.safe_load(text) or {}
        if "rules" not in data or not isinstance(data["rules"], dict):
            raise RulesError("rules file has no 'rules' mapping")
        problems: list[str] = []
        rules: dict[str, Rule] = {}
        for rule_id, entry in data["rules"].items():
            problems += _validate(rule_id, entry)
            if not problems:
                rules[rule_id] = Rule(
                    id=rule_id, title=entry["title"], value=entry["value"], unit=entry["unit"],
                    applies_to=list(entry["applies_to"]), effective_from=_s(entry["effective_from"]),
                    url=entry["url"], checked_at=str(entry["checked_at"]), quote=entry["quote"],
                    check_method=entry["check_method"], verified=entry["verified"],
                    note=entry.get("note", ""), pending_change=entry.get("pending_change"),
                    history=list(entry.get("history") or []))
        if problems:
            raise RulesError("rules file is invalid:\n  " + "\n  ".join(problems))
        header = "".join(line + "\n" for line in text.splitlines() if line.startswith("#")) if text else ""
        header = text[: text.find("meta:")] if "meta:" in text else header
        return cls(rules, data.get("meta") or {}, path, header)

    def save(self, path: str | Path | None = None) -> None:
        path = Path(path or self.path)
        body = {"meta": self.meta, "rules": {rid: r.to_yaml_dict() for rid, r in self.rules.items()}}
        text = self.header + yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=110)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        Rules.from_text(text)           # never write a file that does not load
        tmp.replace(path)

    # ------------------------------------------------------------ access
    def get(self, rule_id: str) -> Rule:
        try:
            return self.rules[rule_id]
        except KeyError as exc:
            raise RulesError(f"rule '{rule_id}' is not in the rules file") from exc

    def v(self, trace: RuleTrace | None, rule_id: str, *path: Any, allow_unverified: bool = False) -> Any:
        """Read a value (or a part of it) and record it in the trace."""
        rule = self.get(rule_id)
        if not rule.verified and not allow_unverified:
            raise UnverifiedRule(f"{rule_id} is not confirmed on an official page")
        value: Any = rule.value
        for p in path:
            try:
                value = value[p]
            except (KeyError, IndexError, TypeError) as exc:
                raise RulesError(f"{rule_id} has no value at {'.'.join(map(str, path))}") from exc
        if trace is not None:
            trace.record(rule, path, value)
        return copy.deepcopy(value)

    @property
    def rules_date(self) -> str:
        """The oldest checked_at among the rules the engine relies on ('Rules as of')."""
        dates = [r.checked_at for r in self.rules.values() if r.check_method != "planning_figure"]
        return min(dates) if dates else "unknown"

    def age_days(self, today: date) -> int:
        if self.rules_date == "unknown":
            return 10**6
        return (today - date.fromisoformat(self.rules_date)).days

    def needs_check(self, today: date, max_age_days: int) -> bool:
        """Older than the limit, or some rule was only confirmed through the search index."""
        if self.age_days(today) > max_age_days:
            return True
        return any(r.check_method == "official_search_index" for r in self.rules.values())

    def pending_changes(self) -> dict[str, dict]:
        return {rid: r.pending_change for rid, r in self.rules.items() if r.pending_change}

    # ------------------------------------------------------------ typed accessors
    def bands(self, trace: RuleTrace | None, rule_id: str, *path: Any) -> list[tuple[float | None, float]]:
        raw = self.v(trace, rule_id, *path)
        if isinstance(raw, dict) and "bands" in raw:
            raw = raw["bands"]
        return [(None if b["width"] is None else float(b["width"]), float(b["rate_pct"])) for b in raw]

    def bsd_bands(self, trace: RuleTrace | None, residential: bool) -> list[tuple[float | None, float]]:
        return self.bands(trace, "bsd_residential" if residential else "bsd_non_residential", "bands")

    def absd_rate_pct(self, trace: RuleTrace | None, buyer_type: str, count_after_purchase: int) -> float:
        rates = self.v(trace, "absd_rates", buyer_type)
        idx = max(0, min(count_after_purchase, len(rates)) - 1)
        return float(rates[idx])

    def ssd_rates_pct(self, trace: RuleTrace | None, kind: str, acquired: date) -> list[float]:
        """Rates by year held for a property acquired on this date; [] when no SSD applies."""
        scope = self.v(trace, "ssd_scope", "applies_to_types")
        if kind not in scope:
            return []
        rule_id = "ssd_residential" if kind == "residential" else "ssd_industrial"
        for i, regime in enumerate(self.get(rule_id).value["regimes"]):
            start = date.fromisoformat(regime["acquired_from"])
            end = date.fromisoformat(regime["acquired_to"]) if regime.get("acquired_to") else None
            if acquired >= start and (end is None or acquired <= end):
                return [float(x) for x in self.v(trace, rule_id, "regimes", i, "rates_by_year_pct")]
        return []

    def ltv(self, trace: RuleTrace | None, loans_outstanding: int, reduced: bool) -> tuple[float, float]:
        key = str(min(max(loans_outstanding, 0), 2))
        row = self.v(trace, "mas_ltv", "individual", key)
        if reduced:
            return float(row["reduced_ltv_pct"]), float(row["reduced_min_cash_pct"])
        return float(row["ltv_pct"]), float(row["min_cash_pct"])

    def tax_on_bands(self, trace: RuleTrace | None, amount: float, rule_id: str, *path: Any) -> float:
        return tiered(amount, self.bands(trace, rule_id, *path))


def tiered(amount: float, bands: list[tuple[float | None, float]]) -> float:
    """Progressive charge: each band's rate applies to the part of the amount inside the band."""
    remaining = max(0.0, float(amount))
    total = 0.0
    for width, rate in bands:
        portion = remaining if width is None else min(remaining, width)
        total += portion * rate / 100.0
        remaining -= portion
        if remaining <= 0:
            break
    return total


def marginal_rate(amount: float, bands: list[tuple[float | None, float]]) -> float:
    """The rate of the band that the next dollar above this amount falls into."""
    edge = 0.0
    for width, rate in bands:
        if width is None or amount < edge + width:
            return rate
        edge += width
    return bands[-1][1]


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


def same_shape(old: Any, new: Any) -> bool:
    """True when new has the same structure as old (types and dict keys), so it can replace it."""
    if isinstance(old, bool) or isinstance(new, bool):
        return isinstance(old, bool) and isinstance(new, bool)
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        return True
    if old is None or new is None:
        return True
    if isinstance(old, str):
        return isinstance(new, str)
    if isinstance(old, dict):
        return isinstance(new, dict) and set(old) == set(new) and all(same_shape(old[k], new[k]) for k in old)
    if isinstance(old, list):
        if not isinstance(new, list) or not new:
            return False
        if not old:
            return True
        return all(same_shape(old[0], item) for item in new)
    return type(old) is type(new)
