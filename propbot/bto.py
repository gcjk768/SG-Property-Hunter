"""Open and upcoming BTO projects for the family website: one Claude web-search call a week (Sunday, with the reports).

HDB's flat portal is a JavaScript app with no feed, so Claude reads HDB pages and the news. Code keeps
only links on listing_domains_allowed and replaces the table only when the call returned something,
so a failed week leaves last week's list up.
"""
from __future__ import annotations

import json
from datetime import date

from .config import Settings
from .db import DB
from .web import is_allowed_domain, is_never_fetch

TOOLS = ["WebSearch", "WebFetch"]
EVERY_DAYS = 7


def due(db: DB, today: date) -> bool:
    last = db.meta_get("bto_day")
    return not last or (today - date.fromisoformat(last)).days >= EVERY_DAYS


def clean(projects: list[dict], settings: Settings) -> list[dict]:
    src, out, seen = settings.sources, [], set()
    for p in projects:
        name, url = (p.get("project") or "").strip(), (p.get("url") or "").strip()
        if not name or name.lower() in seen:
            continue
        if url and (not url.startswith("https://") or is_never_fetch(url, src.never_fetch_domains)
                    or not is_allowed_domain(url, src.listing_domains_allowed)):
            url = ""
        seen.add(name.lower())
        out.append(dict(p, project=name, url=url))
    return out


def refresh(claude, settings: Settings, db: DB, today: date) -> int:
    db.meta_set("bto_day", today.isoformat())       # once a week even if it fails: Claude calls are capped
    base, cfg = settings.prompts_dir, settings.claude.discovery
    res = claude.call(
        label="bto", brief="List the open and upcoming HDB BTO projects. Return the object the schema describes.",
        stdin_text=json.dumps({"today": today.isoformat(), "allowed_domains": settings.sources.listing_domains_allowed,
                               "never_fetch_domains": settings.sources.never_fetch_domains}, indent=1),
        system_file=base / "bto_system.md", schema=json.loads((base / "bto_schema.json").read_text(encoding="utf-8")),
        allowed_tools=TOOLS, disallowed_tools=[t for t in settings.claude.no_tools if t not in TOOLS],
        max_turns=min(cfg.max_turns, 20), timeout=cfg.timeout_seconds)
    rows = clean((res.structured or {}).get("projects") or [], settings)
    if rows:
        db.execute("DELETE FROM bto_upcoming")
        for r in rows:
            db.upsert("bto_upcoming", {k: r.get(k) for k in ("project", "town", "launch", "status", "classification",
                                                              "flat_types", "price_from", "price_to", "completion", "url", "note")}
                      | {"fetched_on": today.isoformat()}, "project")
    return len(rows)


def listed(db: DB) -> list[dict]:
    order = "CASE status WHEN 'open' THEN 0 WHEN 'upcoming' THEN 1 ELSE 2 END, launch IS NULL, launch, town"
    return [dict(r) for r in db.all(f"SELECT * FROM bto_upcoming ORDER BY {order}")]
