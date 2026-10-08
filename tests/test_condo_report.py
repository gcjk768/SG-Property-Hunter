from datetime import date, datetime
from types import SimpleNamespace

from propbot import condo_report, hdb_report, tracker
from propbot.hunt import listing_key
from propbot.reportlib import Checker

from .test_bot import CHAT, HISTORY, TOPIC, make_bot

TODAY = date(2026, 10, 11)


def cand(name, price, sqft, bench, n=5, url=None, **kw):
    c = {"name": name, "area": "Bishan", "district": "D20", "bedrooms": 3, "tenure": "99 year", "remaining_lease_years": 80,
         "price_sgd": price, "floor_area": sqft, "floor_area_unit": "sqft", "site": "edgeprop.sg",
         "url": url or f"https://www.edgeprop.sg/listing/{name.replace(' ', '-')}", "from_snippet": False,
         "benchmark_psf": bench, "benchmark_sales_count": n, "benchmark_note": "EdgeProp Aug-Sep 2026"}
    c.update(kw)
    return c


CANDS = [
    cand("Cheap", 1_800_000, 1200, 1800),                   # 1500 psf vs 1800: -17%, ranked
    cand("Fair", 2_100_000, 1200, 1800),                    # 1750 psf: -3%
    cand("Thin", 1_000_000, 1000, 1500, n=4),               # under 5 sales: not reliable
    cand("NoSource", 1_000_000, 1000, 1500, benchmark_note=""),   # no named source: not reliable
    cand("NoSize", 1_000_000, None, 1500),                  # no psf: last
    cand("Blocked", 1_000_000, 1000, 1500, url="https://www.99.co/listing/1"),
    cand("Dear", 9_000_000, 1000, 1500),                    # over budget
    cand("Project Page", 1_000_000, 1000, 1500, url="https://www.edgeprop.sg/condo-apartment/x"),   # resale needs a unit page
]


def test_validate_and_rank(settings):
    keep, dropped = condo_report.validate(CANDS, settings, "resale")
    assert [x.name for x in keep] == ["Cheap", "Fair", "Thin", "NoSource", "NoSize"] and len(dropped) == 3
    good, rest = condo_report.split(keep)
    assert [x.name for x in good] == ["Cheap", "Fair"] and [x.name for x in rest][-1] == "NoSize"
    assert round(good[0].prem, 2) == -0.17


def test_new_launch_may_use_project_page_and_gone_listings_are_skipped(settings):
    keep, _ = condo_report.validate([CANDS[-1]], settings, "new_launch")
    assert len(keep) == 1
    keep, dropped = condo_report.validate(CANDS[:1], settings, "resale", frozenset({listing_key(CANDS[0]["url"])}))
    assert not keep and "marked gone" in dropped[0]


class FakeClaude:
    def __init__(self, previous=()):
        self.labels, self.previous = [], list(previous)
        self.stdins = []

    def call(self, **kw):
        self.labels.append(kw["label"])
        self.stdins.append(kw["stdin_text"])
        if kw["label"] == "hdb-recheck":
            return SimpleNamespace(structured={"previous_status": self.previous}, text="")
        return SimpleNamespace(structured={"run_note": "", "candidates": CANDS, "previous_status": self.previous}, text="")


def test_tracker_price_moves_new_and_gone(db):
    x = SimpleNamespace(key=listing_key("https://e/listing/a"), name="A", area="", url="https://e/listing/a", bedrooms=3, tenure="99 year", lease_left=80,
                        sqft=1000, price=1_000_000)
    tracker.record(db, "condo_resale", [x], date(2026, 10, 4))
    assert tracker.change_label(db.one("SELECT * FROM report_listing"), date(2026, 10, 6)) == "🆕"
    x.price = 950_000
    tracker.record(db, "condo_resale", [x], date(2026, 10, 11))
    row = db.one("SELECT * FROM report_listing")
    assert tracker.change_label(row, date(2026, 10, 12)) == "🟢▼5%"
    assert tracker.changes(db, "condo_resale", date(2026, 10, 12))[0][0] == "🟢"
    assert tracker.to_recheck(db, "condo_resale", date(2026, 10, 12))[0]["price"] == 950_000
    tracker.apply_checks(db, [{"url": "https://e/listing/a", "status": "unknown", "price_now": None}], date(2026, 10, 12))
    assert db.scalar("SELECT status FROM report_listing") == "listed"       # a blocked page proves nothing
    tracker.apply_checks(db, [{"url": "https://e/listing/a", "status": "gone", "price_now": None}], date(2026, 10, 12))
    assert db.scalar("SELECT status FROM report_listing") == "gone" and x.key in tracker.gone_keys(db)
    tracker.record(db, "condo_resale", [x], date(2026, 10, 13))             # a gone listing is not revived by a search
    assert db.scalar("SELECT status FROM report_listing") == "gone"
    assert tracker.active(db, "condo_resale", date(2026, 10, 13)) == []


def test_stale_listings_drop_out(db):
    x = SimpleNamespace(key="k", name="A", area="", url="u", bedrooms=None, tenure="x", lease_left=None, sqft=None, price=1)
    tracker.record(db, "hdb_resale", [x], date(2026, 9, 1))
    assert tracker.active(db, "hdb_resale", date(2026, 9, 20)) and not tracker.active(db, "hdb_resale", date(2026, 9, 23))


def test_hdb_rows_rank_against_block_sales(settings, db):
    for i, p in enumerate([600_000, 610_000, 590_000, 600_000]):
        db.insert("hdb_resale", {"key": f"s{i}", "month": "2026-08", "town": "TAMPINES", "flat_type": "4 ROOM", "block": "1",
                                 "street": "TEST ST", "storey": "04 TO 06", "sqm": 100.0, "model": "A", "lease_start": 1996,
                                 "remaining_lease": 68.0, "price": p, "first_seen": "2026-08-30"})
    for name, price, url in (("1 Test Street", 570_000, "https://www.propertyguru.com.sg/listing/hdb-for-sale-1-test-street-1"),
                             ("2 Nowhere Road", 500_000, "https://www.propertyguru.com.sg/listing/hdb-for-sale-2-nowhere-road-2")):
        tracker.record(db, "hdb_resale", [SimpleNamespace(key=listing_key(url), name=name, area="TAMPINES", url=url, bedrooms=None,
                                                          tenure="99 year", lease_left=None, sqft=None, price=price)], TODAY)
    rows, skipped = hdb_report.rows_for(db, TODAY, Checker(settings, db, TODAY))
    assert skipped == 1 and len(rows) == 1
    r = rows[0]
    assert r.type_label == "4 Room" and r.reliable and round(r.prem, 2) == -0.05 and r.afford not in ("", "n/a")
    assert r.drag is not None and r.change == "🆕"


def test_pdfs_build_with_every_column(settings, db, tmp_path):
    out = tmp_path / "c.pdf"
    text, found = condo_report.weekly(FakeClaude(), settings, db, TODAY, out)
    assert found == 11 and out.read_bytes().startswith(b"%PDF") and "CONDO WEEKLY REPORT" in text
    # second week: last week's listings go to Claude to recheck and a verified-gone one disappears
    c2 = FakeClaude([{"url": CANDS[0]["url"], "status": "gone", "price_now": None}])
    text2, found2 = condo_report.weekly(c2, settings, db, date(2026, 10, 18), tmp_path / "c2.pdf")
    assert "Cheap" in c2.stdins[0] and found2 == 9 and "❌ 1 gone" in text2
    assert hdb_report.weekly(None, settings, db, TODAY, tmp_path / "h.pdf") == ("", 0)


def test_sunday_9am_posts_once(settings, db, limiter, tmp_path):
    bot = make_bot(settings, db, limiter, HISTORY)
    bot.s = bot.s.model_copy(update={"base_dir": tmp_path})
    (tmp_path / "data").mkdir()
    bot.claude = FakeClaude()
    docs = []
    bot.tg.send_document = lambda chat, path, caption="", **kw: docs.append((chat, kw.get("thread_id"), path.name))
    bot.tick(datetime(2026, 10, 10, 9, 5))                  # Saturday: nothing
    bot.tick(datetime(2026, 10, 11, 8, 59))                 # Sunday before 9
    assert not docs
    bot.tick(datetime(2026, 10, 11, 9, 5))                  # Sunday 09:05
    bot.tick(datetime(2026, 10, 11, 12, 5))                 # later the same day: not again
    assert docs == [(CHAT, TOPIC, "condo-report-2026-10-11.pdf")]       # no HDB listings recorded yet, so no HDB PDF
    assert [l for l in bot.claude.labels if l.startswith("condo")] == ["condo-resale", "condo-new_launch"]
    assert any("CONDO WEEKLY REPORT" in m[2] and "Cheap" in m[2] for m in bot.tg.sent)


def test_propgone_removes_a_listing(settings, db, limiter):
    bot = make_bot(settings, db, limiter, HISTORY)
    x = SimpleNamespace(key="k1", name="Narra Residences", area="", url="https://www.propertyguru.com.sg/listing/narra-1",
                        bedrooms=2, tenure="99 year", lease_left=90, sqft=800, price=1_000_000)
    tracker.record(db, "condo_resale", [x], TODAY)
    bot.cmd_gone("", (CHAT, TOPIC))
    assert "propgone narra" in bot.tg.sent[-1][2]
    bot.cmd_gone("zzz", (CHAT, TOPIC))
    assert "nothing matches" in bot.tg.sent[-1][2]
    bot.cmd_gone("narra", (CHAT, TOPIC))
    assert "removed" in bot.tg.sent[-1][2] and "k1" in tracker.gone_keys(db)
