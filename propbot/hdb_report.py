"""Weekly HDB resale report: the listings the hunt has posted, ranked against recent sales of the same block.

The listings come from the tracker (the hunt records every HDB listing it posts; a weekly Claude pass looks
at them again). Each is matched to data.gov.sg resale sales of its own block over the last 12 months. The
flat type was never stored, so it is inferred as the one whose block median is closest to the ask.
"""
from __future__ import annotations

import json
import logging
import re
import statistics as st
from datetime import date
from types import SimpleNamespace

from . import tracker
from .config import Settings
from .db import DB
from .pulse import SQFT_PER_SQM, months_back
from .render import dot
from .reportlib import Checker, Row, build_pdf, by_prem, lease_text
from .telegram import esc, esc_attr

log = logging.getLogger("propbot.hdb")

KIND = "hdb_resale"
MIN_SALES = 3
TOOLS = ["WebSearch", "WebFetch"]
ABBR = {"STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR", "CENTRAL": "CTRL", "CRESCENT": "CRES",
        "LORONG": "LOR", "NORTH": "NTH", "SOUTH": "STH", "JALAN": "JLN", "PLACE": "PL", "TERRACE": "TER",
        "BUKIT": "BT", "CLOSE": "CL", "GARDENS": "GDNS", "HEIGHTS": "HTS", "LANE": "LN", "COMMONWEALTH": "C'WEALTH",
        "UPPER": "UPP", "KAMPONG": "KG", "TANJONG": "TG", "MARKET": "MKT", "PARK": "PK", "BOULEVARD": "BLVD"}
POSTED = re.compile(r"hunt\*\* · posted (.+?), S\$([\d,]+), (https?://\S+)")


def norm(s: str) -> str:
    return " ".join(ABBR.get(w, w) for w in s.upper().split())


def match(db: DB, today: date, listings) -> list[dict]:
    """Attach the block's recent sales to each listing; listings with no sale of their block in 12 months are left out."""
    rows = db.all("SELECT month, town, flat_type, block, street, storey, sqm, lease_start, price FROM hdb_resale WHERE month IN ("
                  + ",".join("?" * 13) + ")", months_back(today, 13))
    by_block: dict[tuple, list] = {}
    for r in rows:
        by_block.setdefault((r["block"], r["street"]), []).append(r)
    year = today.year + (today.timetuple().tm_yday - 1) / 365
    out = []
    for x in listings:
        m = re.match(r"(\d+[A-Z]?) (.+)", x["name"].replace("Pinnacle @ Duxton", "1 Cantonment Rd"))
        sales = by_block.get((m.group(1), norm(m.group(2)))) if m else None
        if not sales:
            continue
        types: dict[str, list] = {}
        for r in sales:
            types.setdefault(r["flat_type"], []).append(r)
        best = min(types, key=lambda t: abs(st.median(r["price"] for r in types[t]) - x["price"]))
        tr = types[best]
        med = st.median(r["price"] for r in tr)
        lo, hi = min(r["price"] for r in tr), max(r["price"] for r in tr)
        out.append(dict(x, town=sales[0]["town"], ftype=best, n=len(tr), med=med, sales=tr,
                        sqft=x["sqft"] or st.median(r["sqm"] for r in tr) * SQFT_PER_SQM,
                        rem=99 - (year - tr[0]["lease_start"]), inrange=lo * 0.9 <= x["price"] <= hi * 1.1))
    return out


def rows_for(db: DB, today: date, chk: Checker) -> tuple[list[Row], int]:
    live = [dict(r) for r in tracker.active(db, KIND, today)]
    matched = match(db, today, live)
    out = []
    for m in matched:
        t = db.one("SELECT * FROM report_listing WHERE key=?", [m["key"]])
        drag = chk.drag("99 year", m["rem"])
        out.append(Row(m["name"], m["town"], m["ftype"].title(), m["price"], m["sqft"],
                       m["med"] / m["sqft"] if m["sqft"] else None, m["n"], m["inrange"] and m["n"] >= MIN_SALES,
                       lease_text("99 year", m["rem"], drag) + (" !" if m["rem"] < 65 else ""), drag,
                       tracker.change_label(t, today) if t else "",
                       chk.afford(KIND, m["name"], m["price"], m["town"], m["sqft"], "99 year", m["rem"], m["ftype"]),
                       m["last_seen"][5:], m["url"]))
    return out, len(live) - len(matched)


def recheck(claude, settings: Settings, db: DB, today: date) -> int:
    """One Claude pass over HDB listings from earlier weeks: sold or removed ones are marked gone, blocked ones left alone."""
    prev = tracker.to_recheck(db, KIND, today, limit=40)
    if not prev:
        return 0
    base, cfg = settings.prompts_dir, settings.claude.discovery
    res = claude.call(
        label="hdb-recheck", brief="Report the status of every listing in recheck_previous_listings. Return the object the schema describes.",
        stdin_text=json.dumps({"recheck_previous_listings": prev, "allowed_domains": settings.sources.listing_domains_allowed,
                               "never_fetch_domains": settings.sources.never_fetch_domains}, indent=1),
        system_file=base / "condo_report_system.md",
        schema={"type": "object", "properties": {"previous_status": json.loads(
            (base / "condo_report_schema.json").read_text(encoding="utf-8"))["properties"]["previous_status"]},
            "required": ["previous_status"]},
        allowed_tools=TOOLS, disallowed_tools=[t for t in settings.claude.no_tools if t not in TOOLS],
        max_turns=min(cfg.max_turns, 30), timeout=cfg.timeout_seconds)
    return tracker.apply_checks(db, (res.structured or {}).get("previous_status") or [], today)


def summary(rows: list[Row], changes, today: date, skipped: int, top: int = 5) -> str:
    good = by_prem([r for r in rows if r.reliable])
    lines = [f"🏠 <b>HDB WEEKLY REPORT</b> · {today:%a %d %b %Y}", "",
             f"🏘 <b>HDB resale</b> · {len(rows)} listings, {len(good)} ranked" + (f" · {skipped} had no sales of their block to compare" if skipped else ""),
             "<i>" + " · ".join([f"🆕 {sum(r.change == '🆕' for r in rows)}", f"🟢 {sum(m == '🟢' for m, *_ in changes)} drops",
                                 f"🔴 {sum(m == '🔴' for m, *_ in changes)} rises", f"❌ {sum(m == '❌' for m, *_ in changes)} gone"]) + "</i>"]
    for i, r in enumerate((good or by_prem(rows))[:top], 1):
        mark = "" if r.prem is None else (" 🟢" if r.prem <= 0 else " 🔴") + f" <i>{r.prem:+.0%} vs block</i>"
        lines.append(f'{i}. <a href="{esc_attr(r.url)}">{esc(r.name)}</a> · ' + dot(r.type_label, f"S${r.price:,.0f}", r.area.title(), r.afford.split(" ")[0] if r.afford[:1] in "✅⚠❌◐" else None) + mark)
    lines += ["", "<blockquote expandable>" + esc(
        "Listings the hunt found, ranked against the last 12 months of sales in the same block (data.gov.sg). The flat type is inferred. "
        "Availability is only as good as the last page check; tell me with /propgone <name> what you find sold. "
        "✅ you can afford it, ⚠ cash short, ❌ not eligible. Not financial advice.") + "</blockquote>"]
    return "\n".join(lines)


def build(path, rows: list[Row], changes, today: date, skipped: int) -> None:
    good, rest = by_prem([r for r in rows if r.reliable]), by_prem([r for r in rows if not r.reliable])
    intro = [f"Week of {today:%d %b %Y}. {len(rows)} HDB resale listings seen by the hunt could be matched to data.gov.sg sales of their block"
             + (f" ({skipped} more had none)." if skipped else "."),
             "<b>Availability is only as good as the last page check.</b> PropertyGuru blocks automated checks (Cloudflare 403), so most listings "
             "could not be re-opened. 'Seen' is the last day the hunt or a page check confirmed the listing; listings not seen for 21 days drop out, "
             "and /propgone removes one you found sold. Open the link before relying on any row."]
    note = ("<b>How ranked:</b> ask price vs the median of the last 12 months of resale sales in the <i>same block</i> (data.gov.sg), lowest premium first "
            "(green = below median). The bot did not store flat type or size, so the type is the one whose block median is closest to the ask. "
            "<b>Lease, 5y drag</b> = lease left (99 years minus block age; ! = under 65) and the value lost to lease decay over 5 years. "
            "<b>Change</b> = new or price move since the last report. <b>Can I?</b> = your Profile.md run through the eligibility and financing engine.")
    secs = [(f"Ranked shortlist ({len(good)})", "", good),
            (f"Not reliably comparable ({len(rest)})", "Ask is more than 10% outside every sale seen for that type in the block, or fewer than 3 sales. "
             "Sorted by premium, for reference only.", rest)]
    for ft in ("5 Room", "4 Room", "3 Room"):
        g = by_prem([r for r in rows if r.type_label == ft])
        secs.append((f"Overall {ft} ({len(g)})", "Shortlist and not-reliably-comparable combined, ranked by ask vs block median (lowest first).", g))
    build_pdf(path, "HDB resale listings seen by the bot, ranked", intro, note,
              [(m, n, d) for m, n, d in changes], [(None, secs)])


def weekly(claude, settings: Settings, db: DB, today: date, pdf_path, notify=lambda *a: None) -> tuple[str, int]:
    if claude is not None:
        try:
            notify("recheck", f"{recheck(claude, settings, db, today)} marked gone")
        except Exception as exc:          # the report is still worth sending without the recheck
            notify("recheck", exc)
    chk = Checker(settings, db, today)
    rows, skipped = rows_for(db, today, chk)
    if not rows:
        return "", 0
    changes = tracker.changes(db, KIND, today)
    build(pdf_path, rows, changes, today, skipped)
    return summary(rows, changes, today, skipped), len(rows)


def backfill_from_vault(db: DB, activity_dir) -> int:
    """One-off: record the HDB listings found in the vault's Activity notes (hunt · posted name, price, url)."""
    n = 0
    for f in sorted(activity_dir.rglob("????-??-??.md")):
        day = date.fromisoformat(f.stem)
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = POSTED.search(line)
            if not m or not ("hdb-for-sale" in m.group(3) or "edgeprop.sg/listing/hdb" in m.group(3)):
                continue
            from .hunt import listing_key
            name, price, url = m.groups()
            tracker.record(db, KIND, [SimpleNamespace(key=listing_key(url), name=name, area="", url=url, bedrooms=None,
                                                      tenure="99 year", lease_left=None, sqft=None,
                                                      price=float(price.replace(",", "")))], day)
            n += 1
    return n
