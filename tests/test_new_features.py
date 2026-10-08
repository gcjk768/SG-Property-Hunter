"""Data-backed outlook, location signals, lease decay backtest, rate stress, friend profiles."""
import json
from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from propbot import backtest, geo, pulse
from propbot.config import apply_profile_overrides
from propbot.engine.card import build_card
from propbot.engine.projection import decay_pct, value_outlook
from propbot.geo import Geo, haversine_m, parse_exits, station_label
from propbot.models import Rates
from propbot.render import render_listing
from propbot.rules.loader import Rules

from . import worked_examples as W
from .conftest import ROOT
from .test_bot import CHAT, HISTORY, TOPIC, make_bot, resale

TODAY = date(2026, 10, 3)
TABLE = {"above_80_years": 0.0, "70_to_80_years": 0.3, "60_to_70_years": 0.7, "50_to_60_years": 1.2,
         "below_50_years": 2.0}


# ------------------------------------------------------------ 1. outlook from data
def test_value_outlook_matches_hand_calculation():
    # 3%/yr, 75 years left: five years at 0.3% decay while above 70, then 0.7% once at or under 70
    v = 1.0
    for t in range(5):
        v *= 1.03 * (1 - decay_pct(75 - t, False, TABLE) / 100)
    assert value_outlook(3.0, 75, False, 5, TABLE) == pytest.approx((v - 1) * 100)
    assert value_outlook(3.0, None, True, 5, TABLE) == pytest.approx((1.03 ** 5 - 1) * 100)   # freehold, no decay
    assert value_outlook(9.0, None, True, 1, TABLE, cap_pct=4.0) == pytest.approx(4.0)         # growth is capped
    assert value_outlook(-1.0, 55, False, 3, TABLE) < -3                                       # decay adds to a falling market


def test_pulse_data_outlook_uses_the_town_trend(settings, db):
    # Bishan 4 room: S$5,000 per sqm five years ago, S$6,000 now: 3.7% a year
    rows = [resale(m, "BISHAN", "4 ROOM", 500_000, sqm=100, block=str(i))
            for i, m in enumerate(pulse.trend_months(TODAY, 5) * 3)]
    rows += [resale(m, "BISHAN", "4 ROOM", 600_000, sqm=100, block=str(50 + i))
             for i, m in enumerate(pulse.months_back(TODAY, 3) * 3)]
    for r in rows:
        db.execute("INSERT OR IGNORE INTO hdb_resale VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("|".join(map(str, r.values())), r["month"], r["town"], r["flat_type"], r["block"], r["street_name"],
                    r["storey_range"], float(r["floor_area_sqm"]), r["flat_model"], 1996, 70.0, float(r["resale_price"]), ""))
    deal = pulse.Deal("k", "2026-09", "BISHAN", "4 ROOM", "1", "ST", "07 TO 09", 90, 70.0, 600_000, 7000, 5, None, None)
    pulse.data_outlook([deal], settings, db, TODAY)
    cagr = (6000 / 5000) ** (1 / 5) * 100 - 100
    assert deal.outlook_pct == pytest.approx(value_outlook(cagr, 70.0, False, 5, settings.assumptions.lease_decay,
                                                           settings.assumptions.base_cagr_cap_pct))
    assert "Bishan 4 room sales" in deal.outlook_reason and "lease decay applied" in deal.outlook_reason
    no_data = pulse.Deal("k2", "2026-09", "ANG MO KIO", "4 ROOM", "1", "ST", "07 TO 09", 90, 70.0, 600_000, 7000, 5, None, None)
    pulse.data_outlook([no_data], settings, db, TODAY)
    assert no_data.outlook_pct is None                     # no history: no estimate rather than a guess


def test_data_outlook_card_says_data(settings):
    d = pulse.Deal("k", "2026-09", "BISHAN", "4 ROOM", "1", "ST", "07 TO 09", 90, 70.0, 600_000, 7000, 5, None, None,
                   outlook_pct=8.0, outlook_reason="Bishan 4 room sales +3.7%/yr")
    assert "🔮 🟢 5y data ▲8%" in pulse.deal_card(1, d, settings.pulse)


# ------------------------------------------------------------ 4. location signals
def test_distance_and_station_names():
    assert haversine_m(1.3521, 103.8198, 1.3521, 103.8198) == 0
    assert haversine_m(1.3000, 103.8000, 1.3090, 103.8000) == pytest.approx(1000, rel=0.01)    # 0.009 degrees of latitude
    assert station_label("SPRINGLEAF MRT STATION") == "Springleaf MRT" and station_label("FARMWAY LRT STATION") == "Farmway LRT"
    rows = parse_exits({"features": [
        {"geometry": {"type": "Point", "coordinates": [103.8, 1.3]}, "properties": {"STATION_NA": "X MRT STATION", "EXIT_CODE": "Exit 1"}},
        {"geometry": {"type": "Polygon", "coordinates": []}, "properties": {"STATION_NA": "SKIP"}}]})
    assert rows == [("X MRT STATION", "Exit 1", 1.3, 103.8)]


def make_geo(settings, db, limiter, tmp_path, pipeline=None):
    path = tmp_path / "pipe.yaml"
    path.write_text(pipeline or "meta: {signal_radius_m: 800}\nstations:\n"
                    "  - {name: Hougang, line: Cross Island Line, opening_year: 2030, status: upcoming}\n"
                    "  - {name: Done, line: Old Line, opening_year: 2010, status: open}\n", encoding="utf-8")
    return Geo(db, settings, limiter, httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))), path)


def test_geo_nearest_and_upcoming(settings, db, limiter, tmp_path):
    g = make_geo(settings, db, limiter, tmp_path)
    db.executemany("INSERT INTO mrt_exits VALUES (?,?,?,?)", [("FAR MRT STATION", "Exit 1", 1.40, 103.90),
                                                              ("NEAR MRT STATION", "Exit 2", 1.3009, 103.8000)])
    name, dist = g.nearest(1.3, 103.8)
    assert name == "Near MRT" and 90 < dist < 110
    assert g.upcoming("Blk 5 Hougang Ave 1", "Hougang") == ["Hougang (Cross Island Line, 2030)"]
    assert g.upcoming("Blk 5 Bishan Rd", "Bishan") == []           # opened stations are not "upcoming"


def test_geo_describe_without_onemap_login_still_gives_upcoming(settings, db, limiter, tmp_path):
    g = make_geo(settings, db, limiter, tmp_path)
    db.meta_set("mrt_exits_day", TODAY.isoformat())
    assert g.describe("Blk 5 Hougang Ave 1", "Hougang", TODAY) == "upcoming Hougang (Cross Island Line, 2030)"
    assert g.describe("Blk 5 Bishan Rd", "Bishan", TODAY) == ""     # nothing known: no line, no crash


def test_geo_geocodes_with_cache_and_marks_near_station(settings, db, limiter, tmp_path):
    hits = []

    def handler(req):
        hits.append(str(req.url))
        if "getToken" in str(req.url):
            return httpx.Response(200, json={"access_token": "T"})
        assert req.headers["Authorization"] == "T"
        return httpx.Response(200, json={"results": [{"LATITUDE": "1.3", "LONGITUDE": "103.8"}]})

    s = settings.model_copy()
    s.secrets.onemap_email, s.secrets.onemap_password = "a@b.c", "pw"
    g = Geo(db, s, limiter, httpx.Client(transport=httpx.MockTransport(handler)), tmp_path / "none.yaml")
    db.executemany("INSERT INTO mrt_exits VALUES (?,?,?,?)", [("NEAR MRT STATION", "Exit 2", 1.3009, 103.8000)])
    db.meta_set("mrt_exits_day", TODAY.isoformat())
    assert g.describe("1 Test Rd", "", TODAY).startswith("🟢 Near MRT 1")
    g.describe("1 Test Rd", "", TODAY)
    assert sum("getToken" in h for h in hits) == 1 and sum("search" in h for h in hits) == 1     # token and answer cached


def test_geo_failure_never_breaks_a_card(settings, db, limiter, tmp_path):
    def boom(req):
        raise httpx.ConnectError("down")
    s = settings.model_copy()
    s.secrets.onemap_email, s.secrets.onemap_password = "a@b.c", "pw"
    g = Geo(db, s, limiter, httpx.Client(transport=httpx.MockTransport(boom)), tmp_path / "none.yaml")
    db.meta_set("mrt_exits_day", TODAY.isoformat())
    db.executemany("INSERT INTO mrt_exits VALUES (?,?,?,?)", [("A MRT STATION", "Exit 1", 1.3, 103.8)])
    assert g.describe("1 Test Rd", "", TODAY) == ""


# ------------------------------------------------------------ 2. backtest
def test_backtest_flat_market_and_decay_table(settings, db):
    """One flat sells at the start and again 4 years later in a market that did not move: the model predicts
    price0 x the decay of four years from 65 years left, and the error is exactly that decay."""
    def add(month, price, lease, block="1"):
        db.execute("INSERT INTO hdb_resale VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (f"{month}|{block}|{price}", month, "BISHAN", "4 ROOM", block, "ST", "07 TO 09", 90.0, "A", 1990, lease, price, ""))
    add("2020-01", 500_000, 65.0)
    add("2024-01", 500_000, 61.0)
    for i in range(6):                                           # peers so each month has a median
        add("2020-01", 500_000, 65.0, block=f"p{i}")
        add("2024-01", 500_000, 61.0, block=f"q{i}")
    r = backtest.run(db, settings, min_gap_years=3)
    assert r["pairs"] == 1
    band = r["bands"]["60_to_70_years"]
    expected = 1.0
    for t in range(4):
        expected *= 1 - decay_pct(65.0 - t, False, settings.assumptions.lease_decay) / 100
    assert band["n"] == 1 and band["mean_error_pct"] == pytest.approx((expected - 1) * 100)   # negative: decay predicted, none seen
    assert "60 to 70 years" in backtest.report(r) and backtest.band_of(75) == "70_to_80_years"


def test_backtest_pairs_need_the_gap(db):
    for month, lease in (("2024-01", 65.0), ("2025-01", 64.0)):
        db.execute("INSERT INTO hdb_resale VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (month, month, "BISHAN", "4 ROOM", "1", "ST", "07 TO 09", 90.0, "A", 1990, lease, 500_000.0, ""))
    assert backtest.pairs(db, min_gap_years=3) == [] and len(backtest.pairs(db, min_gap_years=1)) == 1


# ------------------------------------------------------------ 3. rate stress test
RULES = Rules.load(ROOT / "rules" / "sg_property_rules.yaml")
RATES = Rates(date="2026-10-02", bank_rate_today=3.0, hdb_rate=2.6, cpf_oa=2.5, source="assumed", note="test")


def test_rate_stress_line_on_every_card(settings):
    s = apply_profile_overrides(settings, W.PLACEHOLDERS)
    for make in W.ALL.values():
        c = build_card(make(), s, RULES, RATES, date(2026, 10, 2))
        o = c.fin.option
        assert o.instalment < o.instalment_up1 < o.instalment_up2
        lines = [x for x in render_listing(c, s).splitlines() if "If rates rise 1 point" in x]
        if o.lender == "HDB":
            assert lines == []                     # an HDB loan rate is pegged to CPF OA and does not float
        else:
            assert lines and lines[0].startswith("⚠️") and "None" not in lines[0]


def test_rate_stress_matches_amortisation(settings):
    from propbot.engine.financing import instalment
    s = apply_profile_overrides(settings, W.PLACEHOLDERS)
    o = build_card(W.condo_ocr_1500k(), s, RULES, RATES, date(2026, 10, 2)).fin.option
    assert o.instalment_up1 == pytest.approx(instalment(o.loan, o.rate_pct + 1, o.tenure_years))
    assert o.instalment_up2 == pytest.approx(instalment(o.loan, o.rate_pct + 2, o.tenure_years))


# ------------------------------------------------------------ 6. friend profiles
FRIEND = 777


def friend_bot(settings, db, limiter, tmp_path, monkeypatch):
    monkeypatch.setenv("PROPBOT_HOME", str(tmp_path))
    bot = make_bot(settings, db, limiter, HISTORY)
    bot.friends = {FRIEND}
    bot.config_path = str(ROOT / "config.yaml")
    return bot


def say(bot, uid, text, chat_type="private"):
    chat = {"id": uid, "type": "private"} if chat_type == "private" else {"id": -100}
    msg = {"chat": chat, "from": {"id": uid}, "text": text}
    if chat_type != "private":
        msg["message_thread_id"] = TOPIC
    bot.handle({"message": msg})


def test_friend_can_message_privately_but_strangers_cannot(settings, db, limiter, tmp_path, monkeypatch):
    bot = friend_bot(settings, db, limiter, tmp_path, monkeypatch)
    say(bot, 999, "/prophelp")                                  # not on the list
    assert bot.tg.sent == []
    say(bot, FRIEND, "/prophelp")
    assert bot.tg.sent[0][0] == FRIEND and "PROFILE" in bot.tg.sent[0][2].upper()


def test_friend_cannot_spend_the_owners_claude_plan(settings, db, limiter, tmp_path, monkeypatch):
    bot = friend_bot(settings, db, limiter, tmp_path, monkeypatch)
    for cmd in ("/propask is now a good time", "/prophunt"):
        say(bot, FRIEND, cmd)
    assert len(bot.tg.sent) == 2 and all("NOT AVAILABLE" in m[2] for m in bot.tg.sent)


def test_friend_profile_is_saved_validated_and_private(settings, db, limiter, tmp_path, monkeypatch):
    bot = friend_bot(settings, db, limiter, tmp_path, monkeypatch)
    say(bot, FRIEND, "/propprofile set age=31 gross_monthly_income=6500 cash_available=80000 bogus=1")
    assert "saved 3 field" in bot.tg.sent[-1][2] and "bogus=1" in bot.tg.sent[-1][2]
    from propbot.db import user_profile
    assert user_profile(db, FRIEND) == {"age": 31, "gross_monthly_income": 6500, "cash_available": 80000}
    assert user_profile(db, 42) == {}                            # the owner's id has nothing saved
    say(bot, FRIEND, "/propprofile set citizenship=martian")
    assert "not saved" in bot.tg.sent[-1][2] and user_profile(db, FRIEND)["age"] == 31
    say(bot, FRIEND, "/propprofile clear")
    assert user_profile(db, FRIEND) == {}


def test_friend_analyse_uses_their_figures_not_the_owners(settings, db, limiter, tmp_path, monkeypatch):
    bot = friend_bot(settings, db, limiter, tmp_path, monkeypatch)
    say(bot, FRIEND, "/propanalyse hdb_resale, Tampines, 600000, 1001, 99 year, 68, 3200")
    assert "/propprofile set" in bot.tg.sent[-1][2]            # blank profile: told how to set it, no owner's figures
    say(bot, FRIEND, "/propprofile set gross_monthly_income=9000 cash_available=200000 cpf_oa_balance=80000")
    say(bot, FRIEND, "/propanalyse hdb_resale, Tampines, 600000, 1001, 99 year, 68, 3200")
    card = bot.tg.sent[-1][2]
    assert "Verdict" in card and "Upfront" in card
    say(bot, FRIEND, "/propprofile")
    assert "gross_monthly_income" in bot.tg.sent[-1][2] and "9000" in bot.tg.sent[-1][2]


def test_owner_profile_command_points_at_the_vault(settings, db, limiter, tmp_path, monkeypatch):
    bot = friend_bot(settings, db, limiter, tmp_path, monkeypatch)
    say(bot, 42, "/propprofile set age=40")
    assert "Profile.md" in bot.tg.sent[-1][2]
    from propbot.db import user_profile
    assert user_profile(db, 42) == {}


def test_db_reads_are_safe_across_threads(db):
    """The scheduler and the listener share one connection: concurrent reads and writes must not mix rows."""
    import threading
    db.meta_set("k", "v")
    errors = []

    def reader():
        try:
            for _ in range(2000):
                assert db.meta_get("k") == "v" and len(db.all("SELECT key, value FROM meta")) >= 1
        except Exception as exc:        # noqa: BLE001
            errors.append(repr(exc))

    def writer():
        for i in range(2000):
            db.meta_set(f"w{i % 5}", str(i))

    ts = [threading.Thread(target=f) for f in (reader, reader, writer, writer)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert errors == []


# ------------------------------------------------------------ OneMap access token
def fake_jwt(exp: float) -> str:
    import base64
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"h.{body}.s"


def test_token_expiry_and_status_line(settings):
    import time
    assert geo.token_expiry(fake_jwt(1_800_000_000)) == 1_800_000_000 and geo.token_expiry("garbage") is None
    s = settings.model_copy()
    s.secrets.onemap_email = s.secrets.onemap_password = s.secrets.onemap_access_token = ""
    assert geo.onemap_state(s) == "NOT set"
    s.secrets.onemap_access_token = fake_jwt(time.time() + 3600)
    assert geo.onemap_state(s).startswith("token valid until")
    s.secrets.onemap_access_token = fake_jwt(time.time() - 3600)
    assert "EXPIRED" in geo.onemap_state(s)
    s.secrets.onemap_email, s.secrets.onemap_password = "a@b.c", "pw"
    assert geo.onemap_state(s) == "set, renews itself"


def test_geo_uses_a_pasted_token_until_it_expires(settings, db, limiter, tmp_path):
    import time
    seen = []

    def handler(req):
        seen.append((req.url.path, req.headers.get("Authorization")))
        return httpx.Response(200, json={"results": [{"LATITUDE": "1.3", "LONGITUDE": "103.8"}]})

    s = settings.model_copy()
    s.secrets.onemap_email = s.secrets.onemap_password = ""
    live = fake_jwt(time.time() + 3600)
    s.secrets.onemap_access_token = live
    g = Geo(db, s, limiter, httpx.Client(transport=httpx.MockTransport(handler)), tmp_path / "none.yaml")
    assert g.geocode("1 Test Rd") == (1.3, 103.8)
    assert seen == [("/api/common/elastic/search", live)]           # the pasted token is sent, nothing is fetched to renew
    s.secrets.onemap_access_token = fake_jwt(time.time() - 60)
    assert g.geocode("2 Other Rd") is None and len(seen) == 1       # expired: no call, no crash


def test_search_text_matches_what_onemap_understands():
    assert geo.search_text("Blk 722 Yishun St 71") == "722 YISHUN ST 71"
    assert geo.search_text("BLOCK 233C, Sumang Lane, Singapore 823233") == "233C SUMANG LANE 823233"
    assert geo.search_text("1 Test Rd #05-12") == "1 TEST RD"
    assert geo.search_text("The Skywoods") == "THE SKYWOODS"
