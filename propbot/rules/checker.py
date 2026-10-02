"""Weekly rules check: claude -p reads the official pages, the app diffs and applies.

Claude may fetch only iras.gov.sg, mas.gov.sg, hdb.gov.sg, cpf.gov.sg and ura.gov.sg (enforced
with domain scoped WebFetch permissions, stated in the brief, and checked again here: any
returned URL outside those domains is rejected). When a value changed: the new value, quote
and checked_at are written, the old entry goes to history, a rules_update item is queued for
the channel and the admin chat is alerted. When a page could not be read, the old value stays
and the admin is alerted if the rule is older than 2 x rules_max_age_days.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from ..claude import ClaudeRunner, ClaudeUnavailable
from ..config import Settings
from ..db import DB
from ..web import domain_matches, host_of
from .loader import Rule, Rules, same_shape

OFFICIAL_DOMAINS = ["iras.gov.sg", "mas.gov.sg", "hdb.gov.sg", "cpf.gov.sg", "ura.gov.sg"]


def is_official(url: str) -> bool:
    host = host_of(url or "")
    return bool(host) and url.startswith("https://") and any(domain_matches(host, d) for d in OFFICIAL_DOMAINS)


def allowed_tool_rules() -> list[str]:
    rules = []
    for d in OFFICIAL_DOMAINS:
        rules += [f"WebFetch(domain:{d})", f"WebFetch(domain:www.{d})"]
    rules += ["WebFetch(domain:eservices.mas.gov.sg)", "WebFetch(domain:services-homes.hdb.gov.sg)",
              "WebFetch(domain:eservice.ura.gov.sg)"]
    return rules


@dataclass
class CheckOutcome:
    changed: list[dict] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    needs_manual: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    run_note: str = ""
    error: str = ""


def checkable(rules: Rules) -> list[Rule]:
    return [r for r in rules.rules.values() if r.check_method != "planning_figure" and is_official(r.url)]


def build_stdin(rules: Rules) -> str:
    items = [{"rule_id": r.id, "title": r.title, "value_on_file": r.value, "unit": r.unit,
              "effective_from_on_file": r.effective_from, "url": r.url} for r in checkable(rules)]
    return json.dumps({"official_domains": OFFICIAL_DOMAINS, "rules": items}, ensure_ascii=False, indent=1)


def build_brief(today: date, n: int) -> str:
    return (f"Run date: {today.isoformat()} (Asia/Singapore). Stdin holds {n} rules. Check each one on its "
            f"official URL; open pages only on {', '.join(OFFICIAL_DOMAINS)}. Return value in exactly the "
            "same JSON structure as value_on_file, with status changed only when the official page states a "
            "different figure. Return the rules object.")


def norm(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def apply_results(rules: Rules, results: list[dict], today: date, *, db: DB | None = None,
                  max_age_days: int = 10) -> CheckOutcome:
    """Diff Claude's results against the file and apply them in memory. Caller saves."""
    out = CheckOutcome()
    by_id = {r.id: r for r in checkable(rules)}
    seen = set()
    for item in results:
        rid = item.get("rule_id", "")
        rule = by_id.get(rid)
        if rule is None:
            out.rejected.append({"rule_id": rid, "reason": "unknown or uncheckable rule id"})
            continue
        seen.add(rid)
        url = item.get("url") or rule.url
        if not is_official(url):
            out.rejected.append({"rule_id": rid, "reason": f"URL outside the official domains: {url}"})
            continue
        status = item.get("status")
        quote = (item.get("quote") or "").strip()
        if status == "unreadable" or item.get("value") is None:
            out.unreadable.append(rid)
            continue
        if not quote:
            out.rejected.append({"rule_id": rid, "reason": "no quote returned"})
            continue
        new_value = item["value"]
        if norm(new_value) == norm(rule.value):
            rule.checked_at = today.isoformat()
            rule.check_method = "official_page"
            rule.verified = True
            rule.quote = quote
            if url != rule.url:
                rule.url = url
            out.confirmed.append(rid)
            continue
        if not same_shape(rule.value, new_value):
            out.needs_manual.append({"rule_id": rid, "value": new_value, "quote": quote, "url": url,
                                     "reason": "value returned in a different structure"})
            continue
        old = {"value": rule.value, "effective_from": rule.effective_from, "checked_at": rule.checked_at,
               "url": rule.url, "quote": rule.quote, "replaced_at": today.isoformat()}
        if db is not None:
            db.insert("rules_history", {"rule_id": rid, "value_json": json.dumps(rule.value, default=str),
                                        "effective_from": rule.effective_from, "checked_at": rule.checked_at,
                                        "url": rule.url, "quote": rule.quote, "replaced_at": today.isoformat()})
        rule.history.insert(0, old)
        rule.value = new_value
        rule.effective_from = item.get("effective_from") or rule.effective_from
        rule.quote = quote
        rule.url = url
        rule.checked_at = today.isoformat()
        rule.check_method = "official_page"
        rule.verified = True
        out.changed.append({"rule_id": rid, "title": rule.title, "old_value": old["value"],
                            "old_effective": old["effective_from"], "new_value": new_value,
                            "new_effective": rule.effective_from, "quote": quote, "url": url})
    for rid in by_id:
        if rid not in seen:
            out.unreadable.append(rid)
    return out


def stale_unreadable(rules: Rules, rule_ids: list[str], today: date, max_age_days: int) -> list[str]:
    stale = []
    for rid in rule_ids:
        rule = rules.get(rid)
        if (today - date.fromisoformat(rule.checked_at)).days > 2 * max_age_days:
            stale.append(rid)
    return stale


def run_rules_check(settings: Settings, db: DB, runner: ClaudeRunner, today: date,
                    alert: Callable[[str, str, str], object] | None = None,
                    rules_path: Path | None = None) -> CheckOutcome:
    alert = alert or (lambda kind, key, text: None)
    path = rules_path or settings.rules_dir / "sg_property_rules.yaml"
    rules = Rules.load(path)
    stdin = build_stdin(rules)
    schema = json.loads((settings.prompts_dir / "rules_schema.json").read_text())
    cfg = settings.claude.rules_check
    disallowed = [t for t in settings.claude.no_tools if t != "WebFetch"]
    try:
        result = runner.call(label="rules_check", brief=build_brief(today, len(checkable(rules))),
                             stdin_text=stdin, system_file=settings.prompts_dir / "rules_system.md",
                             schema=schema, allowed_tools=allowed_tool_rules(), disallowed_tools=disallowed,
                             max_turns=cfg.max_turns, timeout=cfg.timeout_seconds,
                             max_budget_usd=cfg.max_budget_usd)
    except ClaudeUnavailable as exc:
        out = CheckOutcome(error=f"{exc.reason} {exc.reset}".strip())
        alert("rules_page_unreadable", "claude", f"Rules check could not run: {out.error}")
        return out
    data = result.structured or {}
    out = apply_results(rules, data.get("rules") or [], today, db=db,
                        max_age_days=settings.run.rules_max_age_days)
    out.run_note = str(data.get("run_note", ""))[:500]
    if out.changed or out.confirmed:
        rules.meta["last_full_check"] = today.isoformat()
        rules.save(path)
    for ch in out.changed:
        db.upsert("pending_rule_changes", {"rule_id": ch["rule_id"], "detected_at": today.isoformat(),
                                           "json": json.dumps(ch, default=str), "posted": 0}, "rule_id")
        alert("rules_changed", ch["rule_id"],
              f"{ch['title']} changed. Was {norm(ch['old_value'])}, now {norm(ch['new_value'])}. {ch['url']}")
    for item in out.needs_manual:
        alert("rules_changed", item["rule_id"],
              f"{item['rule_id']} may have changed but came back in a different structure; update it by hand. "
              f"Quote: {item['quote'][:300]} {item['url']}")
    for item in out.rejected:
        alert("rules_page_unreadable", item["rule_id"], f"Rules check result rejected for {item['rule_id']}: {item['reason']}")
    stale = stale_unreadable(rules, out.unreadable, today, settings.run.rules_max_age_days)
    if stale:
        alert("rules_page_unreadable", "stale",
              f"These rules could not be read and are older than {2 * settings.run.rules_max_age_days} days: "
              + ", ".join(stale))
    return out
