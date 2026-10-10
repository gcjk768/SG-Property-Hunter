"""Telegram listener and hourly scheduler (`propbot serve`).

Lives in the owner Channel, a forum group shared by many bots: it only answers in its own topic (or the
owner's private chat), and only to its own command names, which are unique in the group. Inline
buttons may only trigger the cheap commands in BUTTON_COMMANDS, because callback_data is client supplied.
"""
from __future__ import annotations

import json
import logging
import re
import shlex
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from . import bto, condo_report, hdb_report, hunt, pgcheck, propnex, pulse, site, tracker
from .claude import ClaudeUnavailable
from .config import ConfigError, Profile, Settings, apply_profile_overrides, parse_override_value
from .db import DB, clear_user_profile, set_user_profile, user_profile
from .geo import Geo, onemap_state
from .ratelimit import BudgetExceeded, RateLimiter
from .render import DIVIDER, REPORT_TITLES, SECTION_TITLES, header
from .telegram import TelegramClient, esc
from .web import PoliteFetcher

log = logging.getLogger("propbot.bot")

COMMANDS = ("proppulse", "propcondoreport", "prophdbreport", "propgone", "prophunt", "propanalyse", "propask", "propprofile", "propstatus", "prophelp")
# a friend in a private chat gets the free commands only; hunt and ask spend the owner's Claude plan
FRIEND_COMMANDS = {"proppulse", "propanalyse", "propprofile", "propstatus", "prophelp"}
PROFILE_KEYS = ("citizenship", "age", "first_timer", "marital_status", "buying_with", "gross_monthly_income",
                "variable_monthly_income", "monthly_debt_repayments", "cpf_oa_balance", "cash_available",
                "properties_owned", "intent", "hold_years", "benchmark_return_pct", "max_monthly_commitment_pct")
BUTTON_COMMANDS = {"proppulse", "propstatus", "prophelp"}
BUTTONS = [("🔄 Run again", "proppulse"), ("📊 Status", "propstatus")]
ASK_TOOLS = ["WebSearch", "WebFetch"]


NEW_YORK = ZoneInfo("America/New_York")


def us_session_open(now: datetime) -> bool:
    """US regular session, Mon to Fri 09:30 to 16:00 New York (21:30 to 04:00 SGT in US summer time).
    ponytail: ignores US market holidays; the desk is idle then anyway."""
    ny = (now if now.tzinfo else now.replace(tzinfo=ZoneInfo("Asia/Singapore"))).astimezone(NEW_YORK)
    return ny.weekday() < 5 and (9, 30) <= (ny.hour, ny.minute) < (16, 0)


def plain(text: str) -> str:
    import html
    return html.unescape(re.sub(r"<[^>]+>", "", text))


class Bot:
    def __init__(self, settings: Settings, db: DB, tg: TelegramClient, vault, limiter: RateLimiter,
                 claude=None, http: httpx.Client | None = None, config_path: str | None = None):
        self.s, self.db, self.tg, self.vault, self.limiter, self.claude = settings, db, tg, vault, limiter, claude
        self.http = http or httpx.Client(timeout=120, headers={"User-Agent": settings.limits.web.user_agent})
        self.config_path = config_path
        self.chat = str(settings.telegram.chat_id)
        self.thread = settings.telegram.thread_id or None
        self.owner = settings.telegram.owner_user_id
        self.friends = set(settings.telegram.allowed_user_ids)
        self.geo = Geo(db, settings, limiter, self.http)
        self._uid = 0                   # sender of the update being handled
        self._private_friend = False    # a friend in a private chat: free commands only
        self.tz = ZoneInfo(settings.run.timezone)
        self.username = ""
        self._pulse_lock = threading.Lock()

    # ------------------------------------------------------------ routing
    def where(self, msg: dict) -> tuple[str | int, int | None] | None:
        """Where to reply, or None when the message is not for this bot."""
        chat = msg.get("chat") or {}
        if str(chat.get("id")) == self.chat:
            if self.thread and msg.get("message_thread_id") != self.thread:
                return None                          # another bot's topic
            return self.chat, self.thread
        uid = (msg.get("from") or {}).get("id")
        if chat.get("type") == "private" and uid and (uid == self.owner or uid in self.friends):
            return chat["id"], None
        return None

    def parse(self, text: str) -> tuple[str, str] | None:
        if not text.startswith("/"):
            return None
        parts = text[1:].split(None, 1)
        if not parts:
            return None
        name, _, at = parts[0].partition("@")
        if at and self.username and at.lower() != self.username.lower():
            return None
        name = name.lower()
        return (name, parts[1].strip() if len(parts) > 1 else "") if name in COMMANDS else None

    def handle(self, update: dict) -> None:
        cq = update.get("callback_query")
        if cq:
            self.tg.answer_callback(cq["id"])        # always stop the spinner
            msg = dict(cq.get("message") or {}, **{"from": cq.get("from")})
            where = self.where(msg)
            if where and cq.get("data") in BUTTON_COMMANDS:
                self._who(msg, where)
                self.run_command(cq["data"], "", where)
            return
        msg = update.get("message") or {}
        where = self.where(msg)
        parsed = self.parse(msg.get("text") or "") if where else None
        if parsed:
            self._who(msg, where)
            if self._private_friend and parsed[0] not in FRIEND_COMMANDS:
                self.send(where, f"{SECTION_TITLES['error']} <b>NOT AVAILABLE</b> · /{esc(parsed[0])}\n\n"
                          "<i>That one uses the owner's Claude plan. Try /propanalyse or /proppulse.</i>")
                return
            self.run_command(*parsed, where)

    def _who(self, msg: dict, where) -> None:
        self._uid = (msg.get("from") or {}).get("id") or 0
        self._private_friend = where[1] is None and self._uid != self.owner

    def is_owner(self) -> bool:
        return not self.owner or self._uid in (0, self.owner)

    def profile_for_user(self) -> list[str]:
        """--set arguments for the sender's own profile; empty for the owner (their Profile.md applies)."""
        if self.is_owner():
            return []
        return [f"{k}={json.dumps(v)}" for k, v in user_profile(self.db, self._uid).items()]

    def run_command(self, cmd: str, arg: str, where) -> None:
        self.vault.activity("command", f"/{cmd} {arg[:80]}".strip())
        try:
            getattr(self, "cmd_" + cmd[4:])(arg, where)
        except Exception as exc:  # one bad command must not stop the listener
            log.exception("/%s failed", cmd)
            self.vault.activity("error", f"/{cmd} failed: {type(exc).__name__}: {str(exc)[:200]}")
            self.send(where, f"{SECTION_TITLES['error']} <b>ERROR</b> · /{esc(cmd)}\n\n<i>{esc(str(exc)[:500])}</i>")

    def send(self, where, msgs: str | list[str], buttons=None, silent: bool = False) -> list[int]:
        msgs = [msgs] if isinstance(msgs, str) else msgs
        return [self.tg.send_message(where[0], m, thread_id=where[1], silent=silent,
                                     buttons=buttons if i == len(msgs) - 1 else None)
                for i, m in enumerate(msgs)]

    # ------------------------------------------------------------ pulse
    def pulse(self, where, *, manual: bool, now: datetime | None = None) -> list[str] | None:
        """Refresh and post. Scheduled runs post only new notable deals; manual runs always answer."""
        with self._pulse_lock:
            now = now or datetime.now(self.tz)
            today = now.date()
            full = self.db.meta_get("pulse_full_day") != today.isoformat()
            added = pulse.refresh(self.db, self.s, self.limiter, self.http, today, full=full)
            if full:
                self.db.meta_set("pulse_full_day", today.isoformat())
            deals, ctx = pulse.picks(self.db, self.s, today)
            self.db.meta_set("pulse_last", now.strftime("%Y-%m-%d %H:%M"))
            if not manual:
                deals = [d for d in deals if d.new]
                if not deals:
                    self.vault.activity("pulse", f"checked, {added} new sales, nothing notable")
                    return None
            shown = deals[:self.s.pulse.max_items] if self.s.pulse.max_items > 0 else deals
            pulse.data_outlook(shown, self.s, self.db, today)
            for d in shown:
                d.mrt = self.geo.describe(f"Blk {d.block} {d.street}", d.town.title(), today)
            msgs = pulse.render(deals, ctx, self.s)
            if where is None:              # dry run: print only, keep the deals new
                return msgs
            self.send(where, msgs, buttons=BUTTONS)
            pulse.mark_alerted(self.db, deals)
            link = self.vault.report("HDB pulse", "\n\n".join(plain(m) for m in msgs) + "\n")
            self.vault.activity("pulse", f"{'manual' if manual else 'hourly'}: {added} new sales, "
                                f"{ctx['new']} new notable, posted {len(shown)}: "
                                + "; ".join(f"{d.flat_type.title()} {d.town.title()} S${d.price:,.0f}" for d in shown)[:800],
                                link or "")
            return msgs

    def cmd_pulse(self, arg, where) -> None:
        self.pulse(where, manual=True)

    # ------------------------------------------------------------ listing hunt
    def hunt(self, where, *, manual: bool, now: datetime | None = None, limit: int | None = None) -> int:
        """One Claude web search; posts a header plus one card per new listing. Returns cards posted."""
        if self.claude is None:
            if manual:
                self.send(where, header(REPORT_TITLES["hunt"], "Claude is not set up"))
            return 0
        now = now or datetime.now(self.tz)
        self.limiter.set_run(f"hunt-{now:%Y%m%d%H%M}")
        if manual:
            self.tg.typing(where[0], where[1])
        try:
            listings, dropped, note = hunt.run(self.claude, self.s, self.db, now.date(), limit or self.s.hunt.per_run, self.geo)
        except ClaudeUnavailable as exc:
            self.vault.activity("error", f"listing hunt: Claude unavailable: {exc.reason}")
            if manual:
                self.send(where, header(REPORT_TITLES["hunt"], "Claude unavailable") + f"\n\n<i>{esc(exc.reason)}</i>")
            return 0
        self.vault.activity("hunt", f"{len(listings)} new listings, {len(dropped)} dropped"
                            + (f" ({'; '.join(dropped)[:300]})" if dropped else ""))
        if not listings:
            if manual:
                self.send(where, hunt.messages([], note, len(dropped), self.s), buttons=[("📊 Status", "propstatus")])
            return 0
        msgs = hunt.messages(listings, note, len(dropped), self.s)
        self.send(where, msgs, buttons=[("🏠 HDB pulse", "proppulse"), ("📊 Status", "propstatus")])
        hunt.mark_posted(self.db, listings)
        for cat in {x.category for x in listings}:     # every category feeds the reports and the website
            tracker.record(self.db, cat, [x for x in listings if x.category == cat], now.date())
        for x in listings:
            self.vault.activity("hunt", f"posted {x.name}, S${x.price:,.0f}, {x.url}")
        self.vault.report("Listing hunt", "\n\n".join(plain(m) for m in msgs) + "\n")
        return len(listings)

    def cmd_hunt(self, arg, where) -> None:
        self.hunt(where, manual=True)

    # ------------------------------------------------------------ weekly reports (condo and HDB PDFs)
    def _post_pdf(self, where, key: str, name: str, pdf, text: str, caption: str, now: datetime, found: int) -> None:
        self.send(where, text)
        self.tg.send_document(where[0], pdf, caption, thread_id=where[1])
        link = self.vault.report(name, plain(text) + "\n")
        base = getattr(self.vault, "base", None)
        try:     # keep the PDF next to the note in the vault; best effort
            if base:
                dest = base / f"Reports/{now:%Y/%m}" / pdf.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(pdf.read_bytes())
        except OSError as exc:
            log.warning("pdf not copied to the vault: %s", exc)
        self.vault.activity(key, f"posted report: {found} listings", link or "")

    def _note(self, key: str):
        def notify(what, detail):
            kind = "error" if isinstance(detail, Exception) else key
            self.vault.activity(kind, f"{what}: {detail if not isinstance(detail, Exception) else type(detail).__name__ + ': ' + str(detail)[:200]}")
        return notify

    def condo_report(self, where, *, manual: bool, now: datetime | None = None) -> bool:
        """Two Claude searches (resale, new launch), then a PDF and a short summary. True when posted."""
        if self.claude is None:
            if manual:
                self.send(where, header(REPORT_TITLES["condo_report"], "Claude is not set up"))
            return False
        now = now or datetime.now(self.tz)
        self.limiter.set_run(f"condo-{now:%Y%m%d%H%M}")
        if manual:
            self.tg.typing(where[0], where[1])
        pdf = self.s.base_dir / "data" / f"condo-report-{now:%Y-%m-%d}.pdf"
        text, found = condo_report.weekly(self.claude, self.s, self.db, now.date(), pdf, self._note("condo_report"))
        if not found:
            if manual:
                self.send(where, header(REPORT_TITLES["condo_report"], "nothing found or Claude unavailable"))
            return False
        self._post_pdf(where, "condo_report", "Condo report", pdf, text,
                       "📄 <b>Condo weekly report</b> · resale and new launch, ranked", now, found)
        return True

    def hdb_report(self, where, *, manual: bool, now: datetime | None = None) -> bool:
        """The HDB listings the hunt has seen, ranked against their block's sales, as a PDF with a short summary."""
        now = now or datetime.now(self.tz)
        self.limiter.set_run(f"hdbreport-{now:%Y%m%d%H%M}")
        if manual:
            self.tg.typing(where[0], where[1])
        pdf = self.s.base_dir / "data" / f"hdb-report-{now:%Y-%m-%d}.pdf"
        text, found = hdb_report.weekly(self.claude, self.s, self.db, now.date(), pdf, self._note("hdb_report"))
        if not found:
            if manual:
                self.send(where, header(REPORT_TITLES["hdb_report"], "no listings to rank yet"))
            return False
        self._post_pdf(where, "hdb_report", "HDB report", pdf, text,
                       "📄 <b>HDB weekly report</b> · listings ranked against their block's sales", now, found)
        return True

    def cmd_condoreport(self, arg, where) -> None:
        self.condo_report(where, manual=True)

    def cmd_hdbreport(self, arg, where) -> None:
        self.hdb_report(where, manual=True)

    def cmd_gone(self, arg, where) -> None:
        """/propgone <name or part of the link>: you found it sold or withdrawn, so the reports drop it."""
        t = SECTION_TITLES["listing"]
        if not arg.strip():
            self.send(where, f"{t} <b>GONE</b> · tell me what you found sold\n\n<code>/propgone narra residences</code>\n"
                      "<i>a name or part of the listing link; it stays out of every report.</i>")
            return
        hits = tracker.find(self.db, arg.strip())
        if len(hits) != 1:
            lines = [f"{t} <b>GONE</b> · " + ("nothing matches" if not hits else f"{len(hits)} match, be more specific")]
            lines += [f"• {esc(h['name'])} · S${h['price']:,.0f} · <code>{esc(h['url'].split('/')[-1][:50])}</code>" for h in hits[:6]]
            self.send(where, "\n".join(lines))
            return
        tracker.mark_gone(self.db, hits[0]["key"], datetime.now(self.tz).date(), "user")
        self.vault.activity("gone", f"you marked {hits[0]['name']} as gone", "")
        self.send(where, f"{t} <b>GONE</b> · {esc(hits[0]['name'])} removed\n\n<i>It stays out of the next reports.</i>")

    # ------------------------------------------------------------ analyse
    def cmd_analyse(self, arg, where) -> None:
        from .cli import AnalyseInputError, _settings, build_parser, card_message
        t = SECTION_TITLES["listing"]
        if not arg:
            self.send(where, f"{t} <b>ANALYSE</b> · type the figures\n\n"
                      "<code>/propanalyse hdb_resale, Tampines, 600000, 1001, 99 year, 68, 3200</code>\n"
                      "<i>category, area, price, size sqft, tenure, years left, monthly rent</i>")
            return
        try:
            self.limiter.acquire("analyses")
        except BudgetExceeded:
            self.send(where, f"{t} <b>ANALYSE</b> · daily limit reached\n\n<i>Try again tomorrow.</i>")
            return
        argv = ["--config", self.config_path] if self.config_path else []
        argv += ["analyse", *(shlex.split(arg) if arg.startswith("--") else [arg])]
        if not self.is_owner():      # a friend: a blank profile plus their own saved fields, never the owner's
            argv += ["--friend"]
            for item in self.profile_for_user():
                argv += ["--set", item]
        try:
            args = build_parser().parse_args(argv)
        except SystemExit:
            self.send(where, f"{t} <b>ANALYSE</b> · could not read that\n\n<i>See /prophelp for the format.</i>")
            return
        try:
            settings, db = _settings(args, require_income=True)
            _, msg, _, link = card_message(args, settings, db, args._vault)
        except ConfigError:
            how = ("<code>/propprofile set gross_monthly_income=6500 cash_available=80000 cpf_oa_balance=40000</code>"
                   if not self.is_owner() else "<b>Profile.md</b> in the Obsidian vault")
            self.send(where, f"{t} <b>ANALYSE</b> · profile needed\n\n"
                      "👤 I need your income, cash and CPF to work out what you can afford.\n"
                      f"✏️ Set them with {how}")
            return
        except (AnalyseInputError, ValueError) as exc:
            self.send(where, f"{t} <b>ANALYSE</b> · could not read that\n\n<i>{esc(exc)}</i>")
            return
        self.send(where, msg)

    # ------------------------------------------------------------ profile
    def cmd_profile(self, arg, where) -> None:
        t = SECTION_TITLES["listing"]
        words = arg.split()
        if self.is_owner():
            self.send(where, f"{t} <b>PROFILE</b> · yours is in the vault\n\n"
                      "<i>Edit Profile.md in the Obsidian vault. Friends set their own with /propprofile set.</i>")
            return
        if words[:1] == ["clear"]:
            clear_user_profile(self.db, self._uid)
            self.send(where, f"{t} <b>PROFILE</b> · cleared")
            return
        if words[:1] == ["set"]:
            new, bad = {}, []
            for w in words[1:]:
                k, _, v = w.partition("=")
                if k in PROFILE_KEYS and v:
                    new[k] = parse_override_value(v)
                else:
                    bad.append(w)
            try:
                apply_profile_overrides(self.s.model_copy(update={"profile": Profile()}),
                                        {**user_profile(self.db, self._uid), **new})
            except ConfigError as exc:
                self.send(where, f"{t} <b>PROFILE</b> · not saved\n\n<i>{esc(str(exc)[:300])}</i>")
                return
            set_user_profile(self.db, self._uid, new)
            msg = f"{t} <b>PROFILE</b> · saved {len(new)} field(s)"
            if bad:
                msg += f"\n\n⚠️ <i>Ignored: {esc(' '.join(bad))}</i>"
            self.send(where, msg)
            return
        mine = user_profile(self.db, self._uid)
        shown = "\n".join(f"👤 <code>{esc(k)}</code> = {esc(mine.get(k, 'not set'))}" for k in PROFILE_KEYS[:10])
        self.send(where, f"{t} <b>PROFILE</b> · yours\n\n{shown}\n\n"
                  "<code>/propprofile set age=31 gross_monthly_income=6500 cash_available=80000 cpf_oa_balance=40000 "
                  "citizenship=SC first_timer=true marital_status=single</code>\n"
                  "<i>/propprofile clear removes it. Your figures stay on this bot's server and are used only for your /propanalyse.</i>")

    # ------------------------------------------------------------ ask
    def cmd_ask(self, arg, where) -> None:
        t = SECTION_TITLES["ask"]
        if not arg:
            self.send(where, f"{t} <b>ASK</b> · type a question\n\n<code>/propask is now a good time to buy a 4 room in Punggol?</code>")
            return
        if self.claude is None:
            self.send(where, f"{t} <b>ASK</b> · Claude is not set up\n\n<i>CLAUDE_CODE_OAUTH_TOKEN is missing.</i>")
            return
        self.tg.typing(where[0], where[1])
        self.limiter.set_run(f"ask-{datetime.now():%Y%m%d%H%M%S}")
        cfg, base = self.s.claude, self.s.prompts_dir
        memory = self.vault.recent(4000) or "nothing yet"
        stdin = f"Today is {datetime.now(self.tz):%Y-%m-%d %H:%M} SGT.\n\nWhat you already did and found (newest first):\n{memory}\n"
        try:
            res = self.claude.call(
                label="ask", brief=arg, stdin_text=stdin, system_file=base / "ask_system.md",
                schema=json.loads((base / "ask_schema.json").read_text(encoding="utf-8")),
                allowed_tools=ASK_TOOLS, disallowed_tools=[x for x in cfg.no_tools if x not in ASK_TOOLS],
                max_turns=cfg.ask.max_turns, timeout=cfg.ask.timeout_seconds)
        except ClaudeUnavailable as exc:
            self.send(where, f"{t} <b>ASK</b> · Claude unavailable\n\n<i>{esc(exc.reason)}</i>")
            return
        answer = ((res.structured or {}).get("answer") or res.text or "No answer.").strip()
        self.send(where, f"{t} <b>ASK</b> · {esc(arg[:60])}\n\n{esc(answer[:3600])}")
        self.vault.activity("claude_call", f"answered: {arg[:80]}")

    # ------------------------------------------------------------ status, help
    def cmd_status(self, arg, where) -> None:
        q = self.db.scalar
        claude = next((u for u in self.limiter.usage() if u["bucket"] == "claude"), {})
        nxt = datetime.now(self.tz).replace(minute=self.s.pulse.check_minute, second=0)
        if nxt <= datetime.now(self.tz):
            nxt = nxt.replace(hour=(nxt.hour + 1) % 24)
        self.send(where, "\n".join([
            f"{SECTION_TITLES['status']} <b>STATUS</b> · SG Property Hunter", "",
            f"🏠 <b>HDB pulse</b> · {'hourly' if self.s.pulse.enabled else 'off'}",
            f"⏰ Last check <code>{esc(self.db.meta_get('pulse_last') or 'never')}</code> · next ~{nxt:%H:%M}",
            f"📦 {q('SELECT COUNT(*) FROM hdb_resale', default=0):,} sales stored · newest "
            f"{esc(q('SELECT MAX(month) FROM hdb_resale', default='none'))} · rents {esc(q('SELECT MAX(quarter) FROM hdb_rent', default='none'))}",
            f"🔔 {q('SELECT COUNT(*) FROM pulse_alerted', default=0):,} deals already posted",
            f"🚇 {q('SELECT COUNT(DISTINCT station) FROM mrt_exits', default=0)} MRT stations"
            + f" · <i>OneMap {esc(onemap_state(self.s))}</i>", "",
            f"🤖 <b>Claude</b> · {claude.get('today', 0)} / {claude.get('day_limit')} calls today · {esc(self.s.claude.model)}",
            f"🧮 <b>Bar</b> · {self.s.pulse.value_discount_pct:g}% under median or {self.s.pulse.min_yield_pct:g}% gross",
        ]), buttons=[("🔄 Run pulse", "proppulse")])

    def cmd_help(self, arg, where) -> None:
        self.send(where, "\n".join([
            f"{SECTION_TITLES['help']} <b>HELP</b> · SG Property Hunter", "",
            "🏠 <b>/proppulse</b> · notable HDB resale deals now",
            "📄 <b>/propcondoreport</b> · condo PDF now (resale and new launch, ranked; owner only)",
            "📄 <b>/prophdbreport</b> · HDB PDF now (listings ranked against their block; owner only)",
            "❌ <b>/propgone</b> · <code>/propgone narra</code> removes a listing you found sold\n",
            "🏘 <b>/prophunt</b> · real listings for sale now (Claude web search, owner only)",
            "👤 <b>/propprofile</b> · set your own income, cash and CPF for /propanalyse",
            "🧮 <b>/propanalyse</b> · full card for one property",
            "<code>/propanalyse hdb_resale, Tampines, 600000, 1001, 99 year, 68, 3200</code>",
            "💬 <b>/propask</b> · ask anything about SG property",
            "📊 <b>/propstatus</b> · data, schedule and budgets", "",
            f"<i>Condo and HDB PDFs every Sunday 09:00. Posts by itself every hour at :{self.s.pulse.check_minute:02d} only when new notable deals appear; hunts new listings at :{self.s.hunt.check_minute:02d}.</i>",
        ]))

    # ------------------------------------------------------------ loops
    def tick(self, now: datetime) -> None:
        """Hourly pulse and hunt; the slot is stored so a restart doesn't post twice."""
        slot = now.strftime("%Y-%m-%d %H")
        if self.s.run.avoid_us_session and us_session_open(now):
            self.site_tick(now, slot)    # listings keep coming in; only the Claude calls wait
            return      # trading desk hours: leave the NAS and the Claude plan to it, catch up afterwards
        c = self.s.condo_report
        if c.enabled and now.weekday() == c.weekday and now.hour >= c.hour and self.db.meta_get("condo_day") != str(now.date()):
            self.db.meta_set("condo_day", str(now.date()))     # once a day even if it fails: Claude calls are capped
            self.condo_report((self.chat, self.thread), manual=False, now=now)
            if c.hdb:
                self.hdb_report((self.chat, self.thread), manual=False, now=now)
            if self.claude is not None and bto.due(self.db, now.date()):
                self.refresh_bto(now)        # the BTO list for the website, with the other weekly Claude calls
        if self.s.pulse.enabled and now.minute >= self.s.pulse.check_minute and self.db.meta_get("pulse_slot") != slot:
            self.db.meta_set("pulse_slot", slot)
            self.pulse((self.chat, self.thread), manual=False, now=now)
        if self.s.hunt.enabled and now.minute >= self.s.hunt.check_minute and self.db.meta_get("hunt_slot") != slot:
            self.db.meta_set("hunt_slot", slot)
            self.hunt((self.chat, self.thread), manual=False, now=now)
        self.site_tick(now, slot)

    def site_tick(self, now: datetime, slot: str) -> None:
        """Hourly after the hunt minute: PropNex, photos and data.json for the website (no Claude call)."""
        if now.minute >= self.s.hunt.check_minute and self.db.meta_get("site_slot") != slot:
            self.db.meta_set("site_slot", slot)
            self.refresh_site(now)

    def refresh_bto(self, now: datetime) -> None:
        """Weekly: open and upcoming BTO projects for the website (one Claude call)."""
        try:
            n = bto.refresh(self.claude, self.s, self.db, now.date())
            self.vault.activity("bto", f"BTO list updated · {n} projects")
        except Exception as exc:          # last week's list stays up
            log.exception("BTO refresh failed")
            self.vault.activity("error", f"BTO refresh failed: {type(exc).__name__}: {str(exc)[:200]}")

    def refresh_site(self, now: datetime) -> None:
        """Hourly, after the hunt: a few listing photos, then data.json for the family website."""
        try:
            if propnex.due(self.db, time.time()):
                try:
                    self.vault.activity("site", f"PropNex · {propnex.run(self.s, self.db, self.http, self.limiter, now.date(), self.vault.journal)} listings")
                except Exception as exc:      # one source down must not stop the website update
                    self.vault.activity("error", f"PropNex read failed: {type(exc).__name__}: {str(exc)[:200]}")
            try:
                self.vault.activity("site", f"PropertyGuru pages opened via NAS Chrome · {pgcheck.run(self.db, now.date())}")
            except Exception as exc:          # verifier down: PropertyGuru cards stay hidden, the rest is unaffected
                self.vault.activity("error", f"PropertyGuru check failed: {type(exc).__name__}: {str(exc)[:200]}")
            site.fetch_photos(self.db, PoliteFetcher(self.s, self.db, self.limiter, journal=self.vault.journal), now.date())
            n = site.export(self.db, self.s.data_dir / "site", now.date(), now, self.geo)
            self.vault.activity("site", f"website updated · {n} listings")
        except Exception as exc:          # the website must never break the bot
            log.exception("website refresh failed")
            self.vault.activity("error", f"website refresh failed: {type(exc).__name__}: {str(exc)[:200]}")

    def _scheduler(self) -> None:
        while True:
            try:
                self.tick(datetime.now(self.tz))
            except Exception as exc:
                log.exception("scheduled pulse failed")
                self.vault.activity("error", f"hourly pulse failed: {type(exc).__name__}: {str(exc)[:200]}")
            time.sleep(30)

    def serve(self, heartbeat: Path) -> None:
        self.username = self.tg.get_me().get("username", "")
        log.info("listening as @%s in %s topic %s", self.username, self.chat, self.thread)
        self.vault.activity("start", f"listening as @{self.username}")
        threading.Thread(target=self._scheduler, daemon=True, name="scheduler").start()
        offset = int(self.db.meta_get("tg_offset") or 0) or None
        while True:
            heartbeat.write_text(str(time.time()))
            try:
                updates = self.tg.get_updates(offset, timeout=30)
            except Exception as exc:
                log.warning("getUpdates failed: %s", exc)
                time.sleep(5)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                self.db.meta_set("tg_offset", str(offset))
                self.handle(u)
