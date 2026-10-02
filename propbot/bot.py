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

from . import hunt, pulse
from .claude import ClaudeUnavailable
from .config import ConfigError, Settings
from .db import DB
from .ratelimit import BudgetExceeded, RateLimiter
from .render import DIVIDER, SECTION_TITLES
from .telegram import TelegramClient, esc

log = logging.getLogger("propbot.bot")

COMMANDS = ("proppulse", "prophunt", "propanalyse", "propask", "propstatus", "prophelp")
BUTTON_COMMANDS = {"proppulse", "propstatus", "prophelp"}
BUTTONS = [("🔄 Run again", "proppulse"), ("📊 Status", "propstatus")]
ASK_TOOLS = ["WebSearch", "WebFetch"]


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
        if chat.get("type") == "private" and self.owner and (msg.get("from") or {}).get("id") == self.owner:
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
                self.run_command(cq["data"], "", where)
            return
        msg = update.get("message") or {}
        where = self.where(msg)
        parsed = self.parse(msg.get("text") or "") if where else None
        if parsed:
            self.run_command(*parsed, where)

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
            msgs = pulse.render(deals, ctx, self.s)
            if where is None:              # dry run: print only, keep the deals new
                return msgs
            self.send(where, msgs, buttons=BUTTONS)
            pulse.mark_alerted(self.db, deals)
            shown = deals[:self.s.pulse.max_items]
            link = self.vault.report("HDB pulse", "\n\n".join(plain(m) for m in msgs) + "\n")
            self.vault.activity("pulse", f"{'manual' if manual else 'hourly'}: {added} new sales, "
                                f"{ctx['new']} new notable, posted {len(shown)}: "
                                + "; ".join(f"{d.flat_type.title()} {d.town.title()} S${d.price:,.0f}" for d in shown),
                                link or "")
            return msgs

    def cmd_pulse(self, arg, where) -> None:
        self.pulse(where, manual=True)

    # ------------------------------------------------------------ listing hunt
    def hunt(self, where, *, manual: bool, now: datetime | None = None) -> int:
        """One Claude web search; posts a header plus one card per new listing. Returns cards posted."""
        if self.claude is None:
            if manual:
                self.send(where, f"{hunt.TITLE} <b>LISTING HUNT</b> · Claude is not set up")
            return 0
        now = now or datetime.now(self.tz)
        self.limiter.set_run(f"hunt-{now:%Y%m%d%H%M}")
        if manual:
            self.tg.typing(where[0], where[1])
        try:
            listings, dropped, note = hunt.run(self.claude, self.s, self.db, now.date(), self.s.hunt.per_run)
        except ClaudeUnavailable as exc:
            self.vault.activity("error", f"listing hunt: Claude unavailable: {exc.reason}")
            if manual:
                self.send(where, f"{hunt.TITLE} <b>LISTING HUNT</b> · Claude unavailable\n\n<i>{esc(exc.reason)}</i>")
            return 0
        self.vault.activity("hunt", f"{len(listings)} new listings, {len(dropped)} dropped"
                            + (f" ({'; '.join(dropped)[:300]})" if dropped else ""))
        if not listings:
            if manual:
                self.send(where, hunt.header([], note, len(dropped)), buttons=[("📊 Status", "propstatus")])
            return 0
        msgs = [hunt.header(listings, note, len(dropped))] + [hunt.card(x, self.s) for x in listings]
        self.send(where, msgs, buttons=[("🏠 HDB pulse", "proppulse"), ("📊 Status", "propstatus")])
        hunt.mark_posted(self.db, listings)
        for x in listings:
            self.vault.activity("hunt", f"posted {x.name}, S${x.price:,.0f}, {x.url}")
        self.vault.report("Listing hunt", "\n\n".join(plain(m) for m in msgs) + "\n")
        return len(listings)

    def cmd_hunt(self, arg, where) -> None:
        self.hunt(where, manual=True)

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
        try:
            args = build_parser().parse_args(argv)
        except SystemExit:
            self.send(where, f"{t} <b>ANALYSE</b> · could not read that\n\n<i>See /prophelp for the format.</i>")
            return
        try:
            settings, db = _settings(args, require_income=True)
            _, msg, _, link = card_message(args, settings, db, args._vault)
        except ConfigError:
            self.send(where, f"{t} <b>ANALYSE</b> · profile needed\n\n"
                      "👤 Fill in <code>gross_monthly_income</code>, <code>cash_available</code> and "
                      "<code>cpf_oa_balance</code> in <b>Profile.md</b>\n"
                      "📂 <i>Obsidian / SG Property Hunter / Profile.md</i>")
            return
        except (AnalyseInputError, ValueError) as exc:
            self.send(where, f"{t} <b>ANALYSE</b> · could not read that\n\n<i>{esc(exc)}</i>")
            return
        self.send(where, msg)

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
            f"🔔 {q('SELECT COUNT(*) FROM pulse_alerted', default=0):,} deals already posted", "",
            f"🤖 <b>Claude</b> · {claude.get('today', 0)} / {claude.get('day_limit')} calls today · {esc(self.s.claude.model)}",
            f"🧮 <b>Bar</b> · {self.s.pulse.value_discount_pct:g}% under median or {self.s.pulse.min_yield_pct:g}% gross",
        ]), buttons=[("🔄 Run pulse", "proppulse")])

    def cmd_help(self, arg, where) -> None:
        self.send(where, "\n".join([
            f"{SECTION_TITLES['help']} <b>HELP</b> · SG Property Hunter", "",
            "🏠 <b>/proppulse</b> · notable HDB resale deals now",
            "🏘 <b>/prophunt</b> · real listings for sale now (Claude web search)",
            "🧮 <b>/propanalyse</b> · full card for one property",
            "<code>/propanalyse hdb_resale, Tampines, 600000, 1001, 99 year, 68, 3200</code>",
            "💬 <b>/propask</b> · ask anything about SG property",
            "📊 <b>/propstatus</b> · data, schedule and budgets", "",
            f"<i>Posts by itself every hour at :{self.s.pulse.check_minute:02d} only when new notable deals appear; hunts new listings at :{self.s.hunt.check_minute:02d}.</i>",
        ]))

    # ------------------------------------------------------------ loops
    def tick(self, now: datetime) -> None:
        """Hourly pulse at check_minute; the slot is stored so a restart doesn't post twice."""
        slot = now.strftime("%Y-%m-%d %H")
        if self.s.pulse.enabled and now.minute >= self.s.pulse.check_minute and self.db.meta_get("pulse_slot") != slot:
            self.db.meta_set("pulse_slot", slot)
            self.pulse((self.chat, self.thread), manual=False, now=now)
        if self.s.hunt.enabled and now.minute >= self.s.hunt.check_minute and self.db.meta_get("hunt_slot") != slot:
            self.db.meta_set("hunt_slot", slot)
            self.hunt((self.chat, self.thread), manual=False, now=now)

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
