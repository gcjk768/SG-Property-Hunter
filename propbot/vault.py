"""Obsidian vault on the NAS: propbot reads its inputs from it and writes every action to it.

Everything lives under <vault_path>/<folder>. On the NAS the app vault
/volume1/<USER>/Obsidian/SG Property Hunter is mounted at /vault and folder is empty:

  Home.md                    write once: MOC linking the latest notes
  Profile.md                 read: frontmatter overrides profile fields from config.yaml
  Watchlist.md               read: one listing per bullet, analysed by `propbot analyse --watchlist`
  Activity/2026/10/2026-10-02.md  write: one line per action (runs, cards, posts, deletes, rules, alerts)
  Reports/2026/10/2026-10-02 HDB pulse.md  write: each pulse report
  Daily/2026-10-02.md        write: the day's index, linking every card
  Cards/2026-10-02/<slug>.md write: one note per item, frontmatter for Dataview, the card text
  Rules/Rules.md             write: current rule values with checked dates and links
  Rules/Changes.md           write: every rule change, appended
  Favourites.md              write: favourites saved from the private chat

Rules: never write outside the propbot folder, never delete a note, write atomically, and keep
everything under "## My notes" in a card note when it is rewritten. If the vault is missing or not
writable, propbot carries on and says so once in the log; the vault is a mirror, not the database.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import yaml

log = logging.getLogger("propbot.vault")

MY_NOTES = "## My notes"
_SLUG = re.compile(r"[^a-z0-9]+")
_LOCK = threading.Lock()
KIND_EMOJI = {"card": "🧮", "telegram_send": "📤", "telegram_delete": "🗑", "alert": "⚠️", "rules_changed": "📜",
              "favourite": "⭐", "claude_call": "🤖", "claude_limit": "⛔", "pulse": "🏠", "command": "💬",
              "watchlist": "👀", "fetch": "🌐", "error": "❌", "start": "🚀"}

HOME_TEMPLATE = """---
tags: [active]
---
# SG Property Hunter

Singapore property research bot (propbot) in the owner Channel. It posts an hourly HDB resale pulse
from data.gov.sg when new notable deals appear, and answers /propanalyse and /propask.

- [[Profile]]: your income, cash and CPF; /propanalyse needs gross_monthly_income
- [[Watchlist]]: properties to analyse
- Activity/YYYY/MM: the movement log, one line per action
- Reports/YYYY/MM: every pulse report
- Cards: one note per analysed property
"""

PROFILE_TEMPLATE = """---
# propbot reads these fields and uses them instead of the profile in config.yaml.
# Delete a line to fall back to config.yaml. Field names are the same as in config.yaml.
# gross_monthly_income: 6500
# cash_available: 150000
# cpf_oa_balance: 80000
---
# propbot profile

Fill in the frontmatter above. propbot reads it at the start of every run.
"""

WATCHLIST_TEMPLATE = """# propbot watchlist

One property per bullet, in the same order as /analyse:
category, area, price, size in sqft, tenure, remaining lease, monthly rent.
Run `propbot analyse --watchlist` and each one gets a card note under Cards.

<!-- example, remove the backticks to use it -->
`- hdb_resale, Tampines, 600000, 1001, 99 year, 68, 3200`
"""


def slug(text: str, limit: int = 60) -> str:
    s = _SLUG.sub(" ", (text or "").lower()).strip().replace(" ", "_")
    return (s[:limit].rstrip("_") or "item")


def split_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            try:
                data = yaml.safe_load(text[3:end]) or {}
            except yaml.YAMLError:
                data = {}
            body = text[end + 4:].lstrip("\n")
            return (data if isinstance(data, dict) else {}), body
    return {}, text


def frontmatter(data: dict) -> str:
    return "---\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000) + "---\n"


class Vault:
    def __init__(self, root: str | Path | None, folder: str = "propbot", tz: str = "Asia/Singapore",
                 enabled: bool = True):
        self.enabled = bool(enabled and root)
        self.root = Path(root).resolve() if root else None
        self.base = (self.root / folder).resolve() if root else None
        self.tz = ZoneInfo(tz)
        self._warned = False

    # ------------------------------------------------------------ plumbing
    @classmethod
    def from_settings(cls, settings) -> "Vault":
        cfg = settings.obsidian
        return cls(cfg.vault_path, cfg.folder, settings.run.timezone, cfg.enabled)

    def available(self) -> bool:
        if not self.enabled or self.base is None:
            return False
        try:
            if not self.root.is_dir():      # the vault root must already exist (the NAS mount)
                raise OSError(f"vault root {self.root} does not exist; is the NAS folder mounted?")
            self.base.mkdir(exist_ok=True)
            return os.access(self.base, os.W_OK)
        except OSError as exc:
            if not self._warned:
                log.warning("Obsidian vault not available at %s: %s", self.base, exc)
                self._warned = True
            return False

    def path(self, rel: str) -> Path:
        """Resolve a path inside the propbot folder; anything that escapes it is refused."""
        p = (self.base / rel).resolve()
        if p != self.base and self.base not in p.parents:
            raise ValueError(f"refusing to touch a path outside the vault folder: {rel}")
        return p

    def read(self, rel: str) -> str | None:
        if not self.available():
            return None
        p = self.path(rel)
        try:
            return p.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def write(self, rel: str, text: str) -> Path | None:
        if not self.available():
            return None
        p = self.path(rel)
        with _LOCK:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(f".{p.name}.propbot.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, p)
        return p

    def append(self, rel: str, text: str, header: str = "") -> Path | None:
        if not self.available():
            return None
        p = self.path(rel)
        with _LOCK:
            p.parent.mkdir(parents=True, exist_ok=True)
            new = not p.exists()
            with p.open("a", encoding="utf-8") as fh:
                if new and header:
                    fh.write(header)
                fh.write(text if text.endswith("\n") else text + "\n")
        return p

    def now(self) -> datetime:
        return datetime.now(self.tz)

    # ------------------------------------------------------------ read: inputs
    def ensure_templates(self) -> None:
        if not self.available():
            return
        if self.read("Home.md") is None:
            self.write("Home.md", HOME_TEMPLATE)
        if self.read("Profile.md") is None:
            self.write("Profile.md", PROFILE_TEMPLATE)
        if self.read("Watchlist.md") is None:
            self.write("Watchlist.md", WATCHLIST_TEMPLATE)

    def profile_overrides(self) -> dict[str, Any]:
        text = self.read("Profile.md")
        if not text:
            return {}
        data, _ = split_frontmatter(text)
        out: dict[str, Any] = {}
        for k, v in data.items():
            if k == "co_buyer" and isinstance(v, dict):
                for ck, cv in v.items():
                    out[f"co_buyer.{ck}"] = cv
            elif v is not None:
                out[str(k)] = v
        return out

    def watchlist(self) -> list[str]:
        """Bullets outside code spans: '- category, area, price, size, tenure, lease, rent'."""
        text = self.read("Watchlist.md") or ""
        items = []
        for line in text.splitlines():
            s = line.strip()
            if s.startswith(("- ", "* ")) and "`" not in s and "," in s:
                items.append(s[2:].strip())
        return items

    def my_notes(self, rel: str) -> str:
        text = self.read(rel) or ""
        i = text.find(MY_NOTES)
        return text[i:] if i != -1 else f"{MY_NOTES}\n\n"

    # ------------------------------------------------------------ write: every action
    def activity_rel(self, stream: str = "", when: datetime | None = None) -> str:
        now = when or self.now()
        return f"Activity/{now:%Y/%m}/{now:%Y-%m-%d}" + (f" {stream}" if stream else "") + ".md"

    def activity(self, kind: str, text: str, link: str = "", stream: str = "", **data: Any) -> None:
        """One line per action in Activity/YYYY/MM/YYYY-MM-DD.md (busy streams such as web get their own file)."""
        now = self.now()
        extra = " ".join(f"{k}={v}" for k, v in data.items() if v not in (None, ""))
        line = f"- {now:%H:%M} {KIND_EMOJI.get(kind, '•')} **{kind}** · {text}"
        if extra:
            line += f" ({extra})"
        if link:
            line += f" · [[{link}]]"
        try:
            self.append(self.activity_rel(stream, now), line,
                        header=f"---\ntags: [log]\nupdated: {now:%Y-%m-%d}\n---\n# Activity {now:%Y-%m-%d}\n\n")
        except OSError as exc:
            log.warning("vault activity write failed: %s", exc)

    def recent(self, max_chars: int = 4000, days: int = 7) -> str:
        """Memory for the LLM: the last days of activity, newest first, capped."""
        out: list[str] = []
        size = 0
        now = self.now()
        for d in range(days):
            text = self.read(self.activity_rel(when=now - timedelta(days=d))) or ""
            for line in reversed([x for x in text.splitlines() if x.startswith("- ")]):
                if size + len(line) + 1 > max_chars:
                    return "\n".join(out)
                out.append(line)
                size += len(line) + 1
        return "\n".join(out)

    def report(self, name: str, body: str) -> str | None:
        """Write Reports/YYYY/MM/YYYY-MM-DD <name>.md (rewritten on the same day); returns the link."""
        now = self.now()
        rel = f"Reports/{now:%Y/%m}/{now:%Y-%m-%d} {name}.md"
        try:
            self.write(rel, frontmatter({"tags": ["report"], "updated": now.strftime("%Y-%m-%d %H:%M")}) + body)
        except OSError as exc:
            log.warning("vault report write failed: %s", exc)
            return None
        return rel[:-3]

    def write_card(self, card, message_plain: str, *, day: str | None = None, run_id: str = "",
                   rank: int | None = None, telegram_message_id: int | None = None) -> str | None:
        """Write one card note; returns its vault link (without .md)."""
        day = day or card.today
        c, v, proj, fin = card.cand, card.verdict, card.proj, card.fin
        rel = f"Cards/{day}/{slug(c.name)}.md"
        base = proj.base
        meta = {
            "type": "propbot_card", "date": day, "run_id": run_id, "category": c.category_key,
            "name": c.name, "area": c.area, "address": c.address, "price": c.price,
            "price_label": c.price_label, "size_sqft": round(c.size_sqft) if c.size_sqft else None,
            "tenure": c.tenure, "remaining_lease": c.remaining_lease,
            "eligible": card.elig.eligible, "verdict": v.label, "score": v.score,
            "best_intent": v.best_intent, "flags": list(v.flags),
            "upfront": round(fin.upfront_total), "cash_needed": round(fin.cash_needed),
            "loan": round(fin.option.loan), "instalment": round(fin.option.instalment),
            "tdsr_pct": round(fin.option.tdsr_pct, 1),
            "exit_year": proj.exit_year,
            "base_cagr": proj.base_cagr, "base_net": round(base.net_gain) if base else None,
            "base_irr": round(base.irr_pct, 1) if base and base.irr_pct is not None else None,
            "bear_net": round(proj.bear.net_gain) if proj.bear else None,
            "bull_net": round(proj.bull.net_gain) if proj.bull else None,
            "fair_price": proj.fair_price, "rules_as_of": card.rules_date,
            "url": c.url or None, "rank": rank, "telegram_message_id": telegram_message_id,
            "tags": ["propbot", c.category_key.replace("_", "-")],
        }
        body = (f"# {c.name}\n\n"
                f"**{v.label}**" + ("" if v.label == "Not eligible yet" else f" ({v.score:.1f}/5)") + "\n\n"
                f"```\n{message_plain}\n```\n\n"
                f"Rules used: {', '.join(card.trace.ids())}\n\n")
        notes = self.my_notes(rel)
        self.write(rel, frontmatter({k: v for k, v in meta.items() if v is not None}) + body + notes)
        link = rel[:-3]
        self.activity("card", f"{v.label} for {c.name}, S${c.price:,.0f}", link, run_id=run_id)
        return link

    def write_daily(self, day: str, items: Iterable[dict], summary: str = "") -> None:
        """Daily index: items are dicts with link, category, name, verdict, score, posted."""
        lines = [f"# propbot {day}", ""]
        if summary:
            lines += [summary, ""]
        lines += ["| # | Category | Item | Verdict | Score | Posted |", "| --- | --- | --- | --- | --- | --- |"]
        for i, it in enumerate(items, 1):
            lines.append(f"| {i} | {it.get('category', '')} | [[{it['link']}\\|{it.get('name', '')}]] | "
                         f"{it.get('verdict', '')} | {it.get('score', '')} | {'yes' if it.get('posted') else 'no'} |")
        notes = self.my_notes(f"Daily/{day}.md")
        self.write(f"Daily/{day}.md", frontmatter({"type": "propbot_daily", "date": day}) + "\n".join(lines) + "\n\n" + notes)

    def write_rules(self, rules) -> None:
        lines = [f"# propbot rules, as of {rules.rules_date}", "",
                 "| Rule | Value | Checked | How | Source |", "| --- | --- | --- | --- | --- |"]
        for r in rules.rules.values():
            value = yaml.safe_dump(r.value, default_flow_style=True, width=10_000).strip().replace("|", "/")
            mark = "" if r.verified else " (unverified)"
            lines.append(f"| {r.title}{mark} | `{value[:300]}` | {r.checked_at} | {r.check_method} | "
                         f"[page]({r.url}) |")
        self.write("Rules/Rules.md", frontmatter({"type": "propbot_rules", "rules_as_of": rules.rules_date})
                   + "\n".join(lines) + "\n")

    def rule_change(self, change: dict) -> None:
        now = self.now()
        text = (f"## {now:%Y-%m-%d} {change.get('title', change.get('rule_id'))}\n\n"
                f"Was: `{change.get('old_value')}` (since {change.get('old_effective')})\n\n"
                f"Now: `{change.get('new_value')}` (from {change.get('new_effective')})\n\n"
                f"> {change.get('quote', '')}\n\n[Official page]({change.get('url', '')})\n")
        self.append("Rules/Changes.md", text, header="# propbot rule changes\n\n")
        self.activity("rules_changed", f"{change.get('title', change.get('rule_id'))} changed", "Rules/Changes")

    def favourite(self, card_link: str, message_id: int, note: str = "") -> None:
        now = self.now()
        self.append("Favourites.md", f"- {now:%Y-%m-%d} [[{card_link}]] message {message_id} {note}".rstrip(),
                    header="# propbot favourites\n\n")
        self.activity("favourite", "saved a favourite", card_link, message_id=message_id)


    def journal(self, kind: str, text: str, data: dict | None = None) -> None:
        """Callback shape used by the Telegram client, the fetcher and the Claude wrapper."""
        data = dict(data or {})
        stream = data.pop("stream", "")
        self.activity(kind, text, stream=stream, **data)


class NullVault(Vault):
    """Used when the vault is switched off."""

    def __init__(self) -> None:
        super().__init__(None, enabled=False)
