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
    hunt_msgs = [s for s in bot.tg.sent if "LISTING HUNT" in s[2] or s[2].startswith("🏘")]
    assert hunt_msgs[0][2].startswith("🏘 <b>LISTING HUNT</b> · 2 new")
    assert [m[2].split("\n")[0] for m in hunt_msgs[1:]] == ["🏘 <b>GOOD ONE</b> · Resale condo · Bishan",
                                                         "🏘 <b>SNIPPET</b> · Resale condo · Bishan"]
    assert "check the listing" in hunt_msgs[2][2] and hunt_msgs[-1][3] and not hunt_msgs[1][3]
    assert all((c, t) == (CHAT, TOPIC) for c, t, *_ in hunt_msgs)
    n = len(bot.tg.sent)
    bot.tick(datetime(2026, 10, 3, 10, 40))                 # next hour: same listings, nothing new
    assert bot.claude.calls == 2 and len(bot.tg.sent) == n
