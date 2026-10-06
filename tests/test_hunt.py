from datetime import datetime
from types import SimpleNamespace

from propbot import hunt

from .test_bot import CHAT, HISTORY, TOPIC, make_bot


def cand(name, url, price=800_000, **kw):
    c = {"category_key": "condo_resale", "name": name, "address": "1 Test Rd", "area": "Bishan", "price_sgd": price,
         "price_phrase": f"S${price}", "price_label": "asking", "floor_area": 900, "floor_area_unit": "sqft",
         "tenure": "99 year", "lease_start_year": None, "remaining_lease_years": 80, "floor": None,
         "asking_rent_sgd": 3600, "completion_year": None, "url": url, "site": "edgeprop.sg", "page_date": None,
         "gist": "Near MRT", "from_snippet": False}
    c.update(kw)
    return c


CANDS = [
    cand("Good One", "https://www.edgeprop.sg/listing/1?ref=x"),
    cand("Dup Of Good", "https://edgeprop.sg/listing/1/"),
    cand("Blocked Site", "https://www.99.co/listing/2"),
    cand("Too Dear", "https://www.edgeprop.sg/listing/3", price=9_000_000),
    cand("Old Sale", "https://www.edgeprop.sg/listing/4", price_label="transacted"),
    cand("Short Lease", "https://www.edgeprop.sg/listing/5", remaining_lease_years=40),
    cand("Snippet", "https://www.propertyguru.com.sg/listing/6", from_snippet=True),
]


class FakeClaude:
    def __init__(self):
        self.calls = 0

    def call(self, **kw):
        self.calls += 1
        assert kw["label"] == "hunt" and "WebSearch" in kw["allowed_tools"]
        return SimpleNamespace(structured={"run_note": "", "candidates": CANDS}, text="")


def test_validate_keeps_only_checkable_new_listings(settings, db, limiter):
    bot = make_bot(settings, db, limiter, HISTORY)
    keep, dropped = hunt.validate(CANDS, bot.s, db)
    assert [x.name for x in keep] == ["Good One", "Snippet"]
    assert len(dropped) == 5 and any("already posted" in d for d in dropped)
    assert any("99.co" in d for d in dropped)


def test_hourly_hunt_posts_header_and_cards_once(settings, db, limiter):
    bot = make_bot(settings, db, limiter, HISTORY)
    bot.claude = FakeClaude()
    bot.tick(datetime(2026, 10, 3, 9, 40))                  # pulse :07 and hunt :37 both due
    first = next(i for i, s in enumerate(bot.tg.sent) if "PROPERTY FOR SALE" in s[2])
    hunt_msgs = bot.tg.sent[first:]                         # summary message, then one message per estate
    assert all((c, t) == (CHAT, TOPIC) for c, t, *_ in hunt_msgs)
    assert "New for sale: <b>2</b>" in hunt_msgs[0][2]
    assert len(hunt_msgs) == 2 and "BISHAN" in hunt_msgs[1][2]
    text = chr(10).join(m[2] for m in hunt_msgs)
    assert '🏘 <b>1. <a href="https://www.edgeprop.sg/listing/1?ref=x">Good One</a></b> · Resale condo 🆕 <i>NEW</i>' in text
    assert "<b>2. <a" in text
    assert "check the listing" in text and hunt_msgs[-1][3]
    n = len(bot.tg.sent)
    bot.tick(datetime(2026, 10, 3, 10, 40))                 # next hour: same listings, nothing new
    assert bot.claude.calls == 2 and len(bot.tg.sent) == n


def test_resale_needs_unit_listing_but_new_launch_may_use_project_page(settings, db, limiter):
    bot = make_bot(settings, db, limiter, HISTORY)
    keep, dropped = hunt.validate([
        cand("Echelon", "https://www.edgeprop.sg/condo-apartment/echelon"),
        cand("New One", "https://www.edgeprop.sg/new-launch/new-one", category_key="condo_new_launch"),
    ], bot.s, db)
    assert [x.name for x in keep] == ["New One"] and "not a unit listing" in dropped[0]
    assert "book a showflat" in hunt.listing_card(1, keep[0], bot.s)


def test_quiet_during_us_session(settings, db, limiter):
    from zoneinfo import ZoneInfo
    from propbot.bot import us_session_open
    sgt = ZoneInfo("Asia/Singapore")
    assert us_session_open(datetime(2026, 10, 5, 22, 0, tzinfo=sgt))       # Mon 10:00 New York
    assert not us_session_open(datetime(2026, 10, 5, 20, 0, tzinfo=sgt))   # before the open
    assert not us_session_open(datetime(2026, 10, 3, 23, 0, tzinfo=sgt))   # Saturday
    assert us_session_open(datetime(2026, 11, 10, 4, 30, tzinfo=sgt))      # winter time: closes 05:00 SGT
    bot = make_bot(settings, db, limiter, HISTORY)
    bot.claude = FakeClaude()
    bot.tick(datetime(2026, 10, 5, 22, 40, tzinfo=sgt))
    assert bot.tg.sent == [] and bot.claude.calls == 0


def test_outlook_line_marks_and_sanity_range():
    from propbot.render import outlook_line, outlook_years
    assert outlook_line(800_000, 12, 5, "MRT 2028") == "🔮 🟢 5y est ▲12% · ~S$896,000 · MRT 2028"
    assert outlook_line(500_000, -8, 5).startswith("🔮 🔴 5y est ▼8% · ~S$460,000")
    assert outlook_line(500_000, None, 5) == "" and outlook_line(500_000, 900, 5) == ""
    assert outlook_years("bto") == 10 and outlook_years("hdb_resale") == 5


def test_commercial_category_budget_and_outlook_on_card(settings, db, limiter):
    bot = make_bot(settings, db, limiter, HISTORY)
    bot.s.categories["shophouse"].budget_max_sgd = 8_000_000
    shop = cand("Shophouse", "https://www.commercialguru.com.sg/listing/9", price=4_500_000, category_key="shophouse",
                outlook_pct=-5, outlook_reason="Lease decay")
    condo = cand("Dear Condo", "https://www.edgeprop.sg/listing/10", price=4_500_000)
    keep, dropped = hunt.validate([shop, condo], bot.s, db)
    assert [x.name for x in keep] == ["Shophouse"] and "outside budget" in dropped[0]
    assert "🔮 🔴 5y est ▼5% · ~S$4,275,000 · Lease decay" in hunt.listing_card(1, keep[0], bot.s)


def test_messages_group_by_estate_then_type_in_one_message(settings, db, limiter):
    bot = make_bot(settings, db, limiter, HISTORY)
    cs = [cand("A", "https://edgeprop.sg/listing/11", area="Yishun"),
          cand("B", "https://edgeprop.sg/listing/12", area="Bukit Batok", category_key="hdb_resale"),
          cand("C", "https://edgeprop.sg/listing/13", area="Yishun", category_key="hdb_resale"),
          cand("D", "https://edgeprop.sg/listing/14", area="Yishun", category_key="bto")]
    keep, _ = hunt.validate(cs, bot.s, db)
    msgs = hunt.messages(keep, "", 0, bot.s)
    assert len(msgs) == 3                                              # summary, Bukit Batok, Yishun
    assert "BUKIT BATOK" in msgs[1] and "YISHUN" not in msgs[1]
    y = msgs[2]
    assert "BUKIT BATOK" not in y and y.index("<b>BTO</b>") < y.index("<b>HDB resale</b>") < y.index("<b>Resale condo</b>")
