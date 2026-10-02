import json
from datetime import date, datetime
from urllib.parse import parse_qs, urlsplit

import httpx

from propbot import pulse
from propbot.bot import Bot
from propbot.telegram import TelegramClient
from propbot.vault import NullVault

from .conftest import ROOT

CHAT, TOPIC = "-100", 7
TODAY = date(2026, 10, 3)


def resale(month, town, ftype, price, sqm=100, lease="70 years 02 months", block="1"):
    return {"month": month, "town": town, "flat_type": ftype, "block": block, "street_name": "TEST ST",
            "storey_range": "04 TO 06", "floor_area_sqm": str(sqm), "flat_model": "Model A",
            "lease_commence_date": "1996", "remaining_lease": lease, "resale_price": str(price)}


def datagov(records, rents):
    def handler(req):
        q = parse_qs(urlsplit(str(req.url)).query)
        if q["resource_id"][0] == "d_23000a00c52996c55106084ed0339566":
            return httpx.Response(200, json={"success": True, "result": {"total": len(rents), "records": rents}})
        months = json.loads(q["filters"][0])["month"]
        recs = [r for r in records if r["month"] in months]
        return httpx.Response(200, json={"success": True, "result": {"total": len(recs), "records": recs}})
    return httpx.Client(transport=httpx.MockTransport(handler))


class FakeTG:
    def __init__(self):
        self.sent, self.answered = [], []

    def send_message(self, chat, text, **kw):
        self.sent.append((chat, kw.get("thread_id"), text, kw.get("buttons")))
        return len(self.sent)

    def answer_callback(self, cid, text=""):
        self.answered.append(cid)

    def typing(self, *a):
        pass


def make_bot(settings, db, limiter, records, rents=()):
    s = settings.model_copy(update={"telegram": settings.telegram.model_copy(
        update={"chat_id": CHAT, "thread_id": TOPIC, "owner_user_id": 42})})
    bot = Bot(s, db, FakeTG(), NullVault(), limiter, http=datagov(records, list(rents)))
    bot.username = "prop_bot"
    return bot


# 6 normal 4 room sales at S$600k/100 sqm in Tampines, plus one cheap one in the latest month
HISTORY = [resale("2026-0%d" % m, "TAMPINES", "4 ROOM", 600_000, block=str(m)) for m in range(3, 9)]
CHEAP = resale("2026-10", "TAMPINES", "4 ROOM", 450_000, block="99")
OLD_LEASE = resale("2026-10", "TAMPINES", "4 ROOM", 450_000, lease="52 years", block="98")  # its own band, no peers
RENTS = [{"quarter": "2026-Q2", "town": "TAMPINES", "flat_type": "4-RM", "median_rent": "3000"},
         {"quarter": "2026-Q2", "town": "TAMPINES", "flat_type": "EXEC", "median_rent": "na"}]


def test_parse_helpers():
    assert pulse.parse_lease("61 years 04 months") == 61.33 and pulse.parse_lease("61") == 61
    assert pulse.parse_lease(None) is None
    assert pulse.months_back(date(2026, 1, 5), 3) == ["2026-01", "2025-12", "2025-11"]


def test_picks_compare_within_lease_band(settings, db, limiter):
    bot = make_bot(settings, db, limiter, HISTORY + [CHEAP, OLD_LEASE], RENTS)
    pulse.refresh(db, bot.s, limiter, bot.http, TODAY, full=True)
    deals, ctx = pulse.picks(db, bot.s, TODAY)
    assert [d.block for d in deals] == ["99"]              # 52 year lease has no comparable peers
    d = deals[0]
    assert round(d.discount_pct) == 25 and d.rent == 3000 and round(d.yield_pct, 1) == 8.0
    assert ctx["rent_quarter"] == "2026-Q2"


def test_hourly_posts_only_new_then_stays_quiet(settings, db, limiter):
    bot = make_bot(settings, db, limiter, HISTORY + [CHEAP], RENTS)
    bot.tick(datetime(2026, 10, 3, 9, 7))
    assert len(bot.tg.sent) == 1
    chat, thread, text, buttons = bot.tg.sent[0]
    assert (chat, thread) == (CHAT, TOPIC) and text.startswith("🏠 <b>HDB RESALE PULSE</b>")
    assert "🆕" in text and buttons and len(text) < 4096
    bot.tick(datetime(2026, 10, 3, 9, 30))                  # same hour: no second check
    bot.tick(datetime(2026, 10, 3, 10, 7))                  # next hour: nothing new, silent
    assert len(bot.tg.sent) == 1


def test_topic_filter_and_command_names(settings, db, limiter):
    bot = make_bot(settings, db, limiter, HISTORY)
    msg = {"chat": {"id": -100}, "message_thread_id": TOPIC, "text": "/prophelp"}
    bot.handle({"message": dict(msg, message_thread_id=<THREAD_ID>)})           # another bot's topic
    bot.handle({"message": dict(msg, text="/prophelp@other_bot")})     # addressed to another bot
    bot.handle({"message": dict(msg, text="/help")})                   # not our command name
    bot.handle({"message": {"chat": {"id": 5, "type": "private"}, "from": {"id": 6}, "text": "/prophelp"}})
    assert bot.tg.sent == []
    bot.handle({"message": dict(msg, text="/prophelp@prop_bot")})
    bot.handle({"message": {"chat": {"id": 42, "type": "private"}, "from": {"id": 42}, "text": "/prophelp"}})
    assert [s[0] for s in bot.tg.sent] == [CHAT, 42]


def test_buttons_whitelisted_and_always_answered(settings, db, limiter):
    bot = make_bot(settings, db, limiter, HISTORY)
    where = {"chat": {"id": -100}, "message_thread_id": TOPIC}
    bot.handle({"callback_query": {"id": "a", "data": "propask spend money", "message": where}})
    bot.handle({"callback_query": {"id": "b", "data": "propstatus", "message": dict(where, message_thread_id=<THREAD_ID>)}})
    assert bot.tg.sent == [] and bot.tg.answered == ["a", "b"]
    bot.handle({"callback_query": {"id": "c", "data": "propstatus", "message": where}})
    assert bot.tg.sent[0][2].startswith("📊 <b>STATUS</b>")


def test_analyse_without_income_asks_for_profile(settings, db, limiter, tmp_path, monkeypatch):
    monkeypatch.setenv("PROPBOT_HOME", str(tmp_path))       # no real .env, db or vault
    bot = make_bot(settings, db, limiter, HISTORY)
    bot.config_path = str(ROOT / "config.yaml")             # the shipped profile has no income
    bot.cmd_analyse("hdb_resale, Tampines, 600000, 1001, 99 year, 68, 3200", (CHAT, TOPIC))
    assert "Profile.md" in bot.tg.sent[0][2]


def test_html_rejected_resends_plain(settings, limiter):
    sent = []

    def handler(req):
        body = json.loads(req.content)
        sent.append(body)
        if "parse_mode" in body:
            return httpx.Response(400, json={"ok": False, "error_code": 400,
                                             "description": "Bad Request: can't parse entities"})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    tg = TelegramClient("1:A", settings, limiter, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert tg.send_message(CHAT, "<b>a &amp; b", thread_id=TOPIC) == 5
    assert sent[-1]["text"] == "a & b" and sent[-1]["message_thread_id"] == TOPIC
