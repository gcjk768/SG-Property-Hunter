"""propbot command line: run, serve, status, purge, test-telegram, discover, analyse, rules-check, backfill."""
from __future__ import annotations

import argparse
import html
import json
import logging
import re
import sys
from dataclasses import asdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .config import (ConfigError, Settings, apply_profile_overrides, check_safety_floors, load_settings,
                     parse_override_value)
from .db import DB, profile_overrides

log = logging.getLogger("propbot")

CARD_COMMANDS = {"run", "serve", "analyse", "discover"}


def _vault(settings: Settings):
    from .vault import NullVault, Vault
    if not settings.obsidian.enabled:
        return NullVault()
    v = Vault.from_settings(settings)
    v.ensure_templates()
    return v


def _settings(args, *, require_income: bool) -> tuple[Settings, DB]:
    settings = load_settings(args.config)
    db = DB(settings.db_path)
    overrides: dict = {}
    vault = _vault(settings)
    if settings.obsidian.read_profile:
        vault_over = vault.profile_overrides()
        try:
            apply_profile_overrides(settings, vault_over)
        except ConfigError as exc:
            raise ConfigError(f"Profile.md in the Obsidian vault: {exc}") from exc
        overrides.update(vault_over)
    overrides.update(profile_overrides(db))
    for item in getattr(args, "set", None) or []:
        if "=" not in item:
            raise ConfigError(f"--set expects key=value, got {item!r}")
        k, v = item.split("=", 1)
        overrides[k.strip()] = parse_override_value(v.strip())
    settings = apply_profile_overrides(settings, overrides)
    check_safety_floors(settings, require_income=require_income)
    args._vault = vault
    return settings, db


def _today(args, settings: Settings) -> date:
    if getattr(args, "date", None):
        return date.fromisoformat(args.date)
    return datetime.now(ZoneInfo(settings.run.timezone)).date()


def plain(html_text: str) -> str:
    t = re.sub(r'<a href="([^"]*)">([^<]*)</a>', r"\2 (\1)", html_text)
    t = re.sub(r"</?(b|pre|i)>", "", t)
    return html.unescape(t)


# ------------------------------------------------------------ analyse
def _parse_positional(text: str) -> dict:
    """'category, area, price, size, tenure, remaining lease, rent' as in /analyse."""
    parts = [p.strip() for p in text.split(",")]
    keys = ["category", "area", "price", "size", "tenure", "remaining_lease", "rent"]
    out = {}
    for k, v in zip(keys, parts):
        if v:
            out[k] = v
    return out


def cmd_analyse(args) -> int:
    settings, db = _settings(args, require_income=True)
    vault = args._vault
    if args.watchlist:
        items = vault.watchlist()
        if not items:
            print("Watchlist.md in the vault has no entries (or the vault is off).")
            return 2
        for item in items:
            args.listing = item
            args.category = args.price = args.size = args.rent = args.remaining_lease = args.tenure = None
            args.area = args.name = None
            print(f"=== {item} ===")
            _analyse_one(args, settings, db, vault)
            print()
        vault.activity("watchlist", f"analysed {len(items)} watchlist items", "Watchlist")
        return 0
    return _analyse_one(args, settings, db, vault)


def _analyse_one(args, settings, db, vault) -> int:
    from .engine.card import build_card, card_row, rates_from_db
    from .models import Candidate, ComparablesSummary, Growth
    from .render import render_listing
    from .rules.loader import RuleTrace, Rules

    if args.listing and args.listing.startswith("http"):
        print("Analysing a listing URL needs the evidence fetcher, which arrives in build step 5. "
              "Type the figures instead (see propbot analyse --help).")
        return 2
    pos = _parse_positional(args.listing) if args.listing else {}
    category = args.category or pos.get("category")
    price = args.price or (float(pos["price"]) if "price" in pos else None)
    if not category or not price:
        print("analyse needs at least --category and --price")
        return 2
    size = args.size or (float(pos["size"]) if "size" in pos else None)
    if args.size_sqm:
        size = args.size_sqm * 10.7639
    rules = Rules.load(settings.rules_dir / "sg_property_rules.yaml")
    trace = RuleTrace()
    today = _today(args, settings)
    rates = rates_from_db(db, settings, rules, trace)
    if args.bank_rate is not None:
        rates.bank_rate_today, rates.source, rates.note = args.bank_rate, "typed in", "bank rate typed in"
    elif args.sora is not None:
        rates.sora_3m = args.sora
        rates.bank_rate_today = args.sora + settings.assumptions.bank_spread_over_sora_pct
        rates.source, rates.note = "typed in SORA plus your spread", "SORA typed in"
    growth = None
    if args.cagr is not None or args.national_cagr is not None:
        growth = Growth(location_cagr=args.cagr, location_source=args.cagr_source or "typed in",
                        national_cagr=args.national_cagr, national_source=args.national_source or "typed in")
    comps = None
    if args.comparable_psf:
        comps = ComparablesSummary(median_psf=args.comparable_psf, n=args.comparables_n or 0,
                                   source=args.comparable_source or "typed in", area_label=args.area or pos.get("area", ""))
    market_value = args.market_value
    if market_value is None and comps and size:
        market_value = comps.median_psf * size
    rent = args.rent if args.rent is not None else (float(pos["rent"]) if "rent" in pos else None)
    cand = Candidate(
        category_key=category, name=args.name or f"{category} at {args.area or pos.get('area', 'unknown area')}",
        price=price, area=args.area or pos.get("area", ""), address=args.address or "",
        price_label="typed in", price_source="your input", price_date=today.isoformat(), size_sqft=size,
        tenure=args.tenure or pos.get("tenure", "unknown"),
        remaining_lease=args.remaining_lease if args.remaining_lease is not None else
        (float(pos["remaining_lease"]) if "remaining_lease" in pos else None),
        built_year=args.built, completion_year=args.completion, rent_estimate=rent,
        rent_source=args.rent_source or ("typed in" if rent is not None else ""), evidence_level="typed",
        flat_type=args.flat_type or "", bto_classification=args.classification or "",
        subsidy_recovery_pct=args.subsidy_recovery, zoning=args.zoning or "",
        residential_share_pct=args.residential_share,
        gst_seller_registered={"yes": True, "no": False}.get(args.gst_seller or "", None),
        market_value=market_value, comparables=comps, growth=growth, segment=args.segment or "")
    card = build_card(cand, settings, rules, rates, today, company=args.company, trace=trace)
    run_time = datetime.now(ZoneInfo(settings.run.timezone)).strftime("%Y-%m-%d %H:%M")
    msg = render_listing(card, settings, run_time=run_time)
    if args.store:
        db.insert("cards", card_row(card, "analyse", None))
    link = vault.write_card(card, plain(msg), run_id="analyse") if vault.available() else None
    if link:
        print(f"[saved to the vault as {link}.md]")
    if args.html:
        print(msg)
    else:
        print(plain(msg))
    print(f"\n[{len(msg)} characters; Telegram limit 4096]")
    if rates.note:
        print(f"[rates: {rates.note}]")
    if args.detail:
        print(detail(card))
    return 0


def detail(card) -> str:
    """Working figures for checking the maths by hand."""
    fin, d, proj = card.fin, card.duties, card.proj
    lines = ["", "CALCULATION DETAIL"]
    lines.append(f"BSD {d.bsd:,.2f} (residential part {d.bsd_residential:,.2f}, non residential {d.bsd_non_residential:,.2f}); "
                 f"ABSD {d.absd:,.2f} at {d.absd_pct}% as {d.absd_buyer} property {d.absd_count}; GST {d.gst:,.2f}")
    o = fin.option
    lines.append(f"Loan option used: {o.lender}, cap {o.ltv_pct}% (reduced {o.reduced_ltv}), binding {o.binding}, "
                 f"loan {o.loan:,.2f}, tenure {o.tenure_years}y, rate {o.rate_pct}%, stress {o.stress_rate_pct}%, "
                 f"instalment {o.instalment:,.2f}, stress instalment {o.instalment_stress:,.2f}, TDSR {o.tdsr_pct:.2f}%"
                 + (f", MSR {o.msr_pct:.2f}%" if o.msr_pct is not None else ""))
    if fin.alternative:
        a = fin.alternative
        lines.append(f"Alternative: {a.lender}, loan {a.loan:,.2f}, tenure {a.tenure_years}y, rate {a.rate_pct}%, "
                     f"instalment {a.instalment:,.2f}, binding {a.binding}")
    lines.append(f"Valuation basis {fin.valuation_basis:,.0f}; downpayment {fin.downpayment:,.2f}; min cash {fin.min_cash:,.2f}; "
                 f"CPF available {fin.cpf_available:,.0f}, limit {fin.cpf_limit:,.0f} ({fin.cpf_factor_note}), used "
                 f"{fin.cpf_used:,.2f} (downpayment {fin.cpf_used_downpayment:,.2f}, duties {fin.cpf_used_duties:,.2f})")
    lines.append(f"Upfront total {fin.upfront_total:,.2f}; cash needed {fin.cash_needed:,.2f}; cash available "
                 f"{fin.cash_available:,.0f}; short {fin.cash_short:,.2f}")
    for p in fin.payments:
        lines.append(f"  payment year {p.year}: {p.label}: {p.amount:,.2f} (CPF {p.cpf:,.2f})")
    if proj.base:
        lines.append(f"Growth: base {proj.base_cagr}%, bear {proj.bear_cagr}%, bull {proj.bull_cagr}% ({proj.cagr_source}); "
                     f"value starts at {proj.v0:,.0f} ({proj.v0_label}); rent {proj.rent0} ({proj.rent_label})")
        lines.append("Yr mode   value_end   market   decay  rent  saved  instal  interest  maint  ptax  ins  itax  "
                     "net_flow  owed  if_sold")
        for r in proj.base.rows:
            lines.append(f"{r.year:>2} {r.mode:<5} {r.value_end:>10,.0f} {r.market_change:>8,.0f} {r.decay_change:>6,.0f} "
                         f"{r.rent:>6,.0f} {r.rent_saved:>6,.0f} {r.instalments:>7,.0f} {r.interest:>8,.0f} "
                         f"{r.maintenance:>6,.0f} {r.property_tax:>5,.0f} {r.insurance:>4,.0f} {r.income_tax:>5,.0f} "
                         f"{r.net_flow:>9,.0f} {r.loan_balance:>9,.0f} "
                         f"{'locked' if not r.sale_allowed else format(r.net_if_sold, ',.0f')}")
        s = proj.base.exit_sale
        lines.append(f"Exit year {s['year']}: value {s['value']:,.0f}, agent with GST {s['agent_fee_with_gst']:,.0f}, legal "
                     f"{s['legal']:,.0f}, SSD {s['ssd']:,.0f}, subsidy recovery {s['subsidy_recovery']:,.0f}, loan "
                     f"{s['loan_redemption']:,.0f}, proceeds {s['proceeds']:,.0f}, CPF refund {s['cpf_refund']:,.0f} "
                     f"(of which accrued interest {s['cpf_accrued_interest']:,.0f}), cash {s['cash_out']:,.0f}")
        lines.append("Base flows: " + ", ".join(f"{x:,.0f}" for x in proj.base.flows))
        lines.append(f"Base: cash in {proj.base.total_cash_in:,.0f}, CPF in {proj.base.total_cpf_in:,.0f}, out cash "
                     f"{proj.base.equity_out_cash:,.0f}, out CPF {proj.base.equity_out_cpf:,.0f}, net {proj.base.net_gain:,.0f}, "
                     f"IRR {proj.base.irr_pct}")
        lines.append(f"Intents: {json.dumps(proj.intents, default=str)}")
    lines.append("Verdict clauses: " + "; ".join(f"{c['clause']} {c['points']:+g} ({c['why']})" for c in card.verdict.clauses))
    lines.append("Rules used: " + ", ".join(f"{rid} (checked {v['checked_at']})" for rid, v in sorted(card.trace.as_dict().items())))
    return "\n".join(lines)


# ------------------------------------------------------------ other commands
def cmd_test_telegram(args) -> int:
    from .ratelimit import RateLimiter
    from .telegram import TelegramClient
    settings, db = _settings(args, require_income=False)
    if not settings.secrets.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN is not set in .env")
        return 2
    limiter = RateLimiter(db, settings, run_id="test-telegram")
    tg = TelegramClient(settings.secrets.telegram_bot_token, settings, limiter, journal=args._vault.journal)
    me = tg.get_me()
    print(f"Bot: @{me.get('username')}")
    target = args.chat or settings.telegram.admin_chat_id or settings.telegram.chat_id
    mid = tg.send_message(target, "propbot test message. It will be deleted in a moment.", silent=True)
    print(f"Sent message {mid} to {target}")
    if not args.keep:
        tg.delete_messages(target, [mid])
        print("Deleted it again")
    return 0


def cmd_rules_check(args) -> int:
    from .alerts import Alerts
    from .claude import ClaudeRunner
    from .ratelimit import RateLimiter
    from .rules.checker import run_rules_check
    settings, db = _settings(args, require_income=False)
    limiter = RateLimiter(db, settings, run_id=f"rules-{datetime.now():%Y%m%d%H%M}")
    vault = args._vault
    alerts = Alerts(db, settings.telegram.admin_chat_id, None, vault=vault)
    runner = ClaudeRunner(settings, limiter, journal=vault.journal)
    out = run_rules_check(settings, db, runner, _today(args, settings), alert=alerts, vault=vault)
    print(json.dumps({"confirmed": out.confirmed, "changed": out.changed, "unreadable": out.unreadable,
                      "rejected": out.rejected, "needs_manual": out.needs_manual, "error": out.error,
                      "run_note": out.run_note}, indent=1, default=str))
    return 0 if not out.error else 1


def cmd_status(args) -> int:
    from .ratelimit import RateLimiter
    from .rules.loader import Rules
    settings, db = _settings(args, require_income=False)
    limiter = RateLimiter(db, settings)
    today = _today(args, settings)
    print("Budgets today:")
    for u in limiter.usage():
        print(f"  {u['bucket']:<9} {u['today']:>4} / {u['day_limit'] or 'no cap'} today, "
              f"{u['this_run']} / {u['run_limit'] or 'no cap'} per run")
    cds = limiter.cooldowns()
    print("Cooldowns: " + (", ".join(f"{c['bucket']}:{c['key']} ({c['cooldown_reason']})" for c in cds) or "none"))
    rules = Rules.load(settings.rules_dir / "sg_property_rules.yaml")
    print(f"Rules as of {rules.rules_date}, {rules.age_days(today)} days old; check needed: "
          f"{rules.needs_check(today, settings.run.rules_max_age_days)}")
    unverified = [r.id for r in rules.rules.values() if not r.verified]
    print("Unverified rules: " + (", ".join(unverified) or "none"))
    pend = rules.pending_changes()
    print("Pending rule changes: " + (", ".join(pend) or "none"))
    v = args._vault
    if settings.obsidian.enabled:
        print(f"Obsidian vault: {v.base} ({'writable' if v.available() else 'NOT available'})")
    else:
        print("Obsidian vault: off (obsidian.enabled is false)")
    rows = db.all("SELECT name, downloaded_at, rows FROM datasets")
    print("Datasets: " + (", ".join(f"{r['name']} ({r['downloaded_at']}, {r['rows']} rows)" for r in rows) or "none yet"))
    return 0


def cmd_later(step: int):
    def run(args) -> int:
        print(f"'{args.cmd}' arrives in build step {step}; the build is paused after step 3 for the maths check.")
        return 2
    return run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="propbot", description=__doc__)
    p.add_argument("--config", default=None, help="path to config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyse", help="print a card from typed in figures (no network)")
    a.add_argument("listing", nargs="?", help="'category, area, price, size, tenure, remaining lease, rent'")
    a.add_argument("--category")
    a.add_argument("--name")
    a.add_argument("--area")
    a.add_argument("--address")
    a.add_argument("--price", type=float)
    a.add_argument("--size", type=float, help="floor area in sqft")
    a.add_argument("--size-sqm", type=float)
    a.add_argument("--tenure", choices=["freehold", "999 year", "99 year", "60 year", "30 year", "unknown"])
    a.add_argument("--remaining-lease", type=float)
    a.add_argument("--built", type=int)
    a.add_argument("--completion", type=int)
    a.add_argument("--rent", type=float, help="monthly market rent")
    a.add_argument("--rent-source")
    a.add_argument("--flat-type")
    a.add_argument("--classification", choices=["standard", "plus", "prime"])
    a.add_argument("--subsidy-recovery", type=float)
    a.add_argument("--zoning", choices=["commercial", "residential", "mixed"])
    a.add_argument("--residential-share", type=float)
    a.add_argument("--gst-seller", choices=["yes", "no", "unknown"])
    a.add_argument("--segment")
    a.add_argument("--cagr", type=float, help="location 10 year CAGR in percent")
    a.add_argument("--cagr-source")
    a.add_argument("--national-cagr", type=float)
    a.add_argument("--national-source")
    a.add_argument("--comparable-psf", type=float)
    a.add_argument("--comparables-n", type=int)
    a.add_argument("--comparable-source")
    a.add_argument("--market-value", type=float)
    a.add_argument("--company", action="store_true", help="buy through a company (commercial)")
    a.add_argument("--sora", type=float, help="3 month compounded SORA in percent")
    a.add_argument("--bank-rate", type=float, help="today's bank rate in percent")
    a.add_argument("--set", action="append", help="profile override key=value (repeatable)")
    a.add_argument("--date", help="run date yyyy-mm-dd")
    a.add_argument("--html", action="store_true", help="print the Telegram HTML")
    a.add_argument("--detail", action="store_true", help="print the working figures")
    a.add_argument("--store", action="store_true", help="store the calculation row in the database")
    a.add_argument("--watchlist", action="store_true", help="analyse every entry in Watchlist.md in the vault")
    a.set_defaults(func=cmd_analyse)

    t = sub.add_parser("test-telegram", help="send and delete a test message")
    t.add_argument("--chat")
    t.add_argument("--keep", action="store_true")
    t.set_defaults(func=cmd_test_telegram)

    r = sub.add_parser("rules-check", help="check the rules against the official pages now")
    r.add_argument("--date")
    r.set_defaults(func=cmd_rules_check)

    s = sub.add_parser("status", help="budgets, cooldowns, rules and datasets")
    s.add_argument("--date")
    s.set_defaults(func=cmd_status)

    for name, step in (("run", 5), ("serve", 6), ("discover", 5), ("backfill", 4), ("purge", 6)):
        x = sub.add_parser(name)
        x.add_argument("rest", nargs="*")
        x.set_defaults(func=cmd_later(step))
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        return args.func(args)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
