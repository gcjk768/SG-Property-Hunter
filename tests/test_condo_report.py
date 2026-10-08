from datetime import date, datetime
from types import SimpleNamespace

from propbot import condo_report

from .test_bot import CHAT, HISTORY, TOPIC, make_bot


def cand(name, price, sqft, bench, n=5, url=None, **kw):
    c = {"name": name, "area": "Bishan", "district": "D20", "bedrooms": 3, "tenure": "99 year", "remaining_lease_years": 80,
         "price_sgd": price, "floor_area": sqft, "floor_area_unit": "sqft", "site": "edgeprop.sg",
         "url": url or f"https://www.edgeprop.sg/listing/{name.replace(' ', '-')}", "from_snippet": False,
         "benchmark_psf": bench, "benchmark_sales_count": n, "benchmark_note": "EdgeProp"}
    c.update(kw)
    return c


CANDS = [
    cand("Cheap", 1_800_000, 1200, 1800),                   # 1500 psf vs 1800: -17%, ranked
    cand("Fair", 2_100_000, 1200, 1800),                    # 1750 psf: -3%
    cand("Thin", 1_000_000, 1000, 1500, n=2),               # too few sales: not reliable
    cand("NoSize", 1_000_000, None, 1500),                  # no psf: last
    cand("Blocked", 1_000_000, 1000, 1500, url="https://www.99.co/listing/1"),
    cand("Dear", 9_000_000, 1000, 1500),                    # over budget
    cand("Project Page", 1_000_000, 1000, 1500, url="https://www.edgeprop.sg/condo-apartment/x"),   # resale needs a unit page
]


def test_validate_and_rank(settings):
    keep, dropped = condo_report.validate(CANDS, settings, "resale")
    assert [x.name for x in keep] == ["Cheap", "Fair", "Thin", "NoSize"] and len(dropped) == 3
    good, rest = condo_report.split(keep)
    assert [x.name for x in good] == ["Cheap", "Fair"] and [x.name for x in rest] == ["Thin", "NoSize"]
    assert round(good[0].prem, 2) == -0.17


def test_new_launch_may_use_project_page(settings):
    keep, _ = condo_report.validate([CANDS[-1]], settings, "new_launch")
    assert len(keep) == 1


def test_pdf_builds(settings, tmp_path):
    data = {s: condo_report.validate(CANDS, settings, s)[0] for s in condo_report.SEGMENTS}
    out = tmp_path / "c.pdf"
    condo_report.build_pdf(out, data, date(2026, 10, 11))
    assert out.read_bytes().startswith(b"%PDF")


class FakeClaude:
    def __init__(self):
        self.labels = []

    def call(self, **kw):
        self.labels.append(kw["label"])
        return SimpleNamespace(structured={"run_note": "", "candidates": CANDS}, text="")


def test_sunday_9am_posts_once(settings, db, limiter, tmp_path):
    bot = make_bot(settings, db, limiter, HISTORY)
    bot.s = bot.s.model_copy(update={"base_dir": tmp_path})
    (tmp_path / "data").mkdir()
    bot.claude = FakeClaude()
    docs = []
    bot.tg.send_document = lambda chat, path, caption="", **kw: docs.append((chat, kw.get("thread_id"), path.name))
    bot.tick(datetime(2026, 10, 10, 9, 5))                  # Saturday: nothing
    assert not docs
    bot.tick(datetime(2026, 10, 11, 8, 59))                 # Sunday before 9
    assert not docs
    bot.tick(datetime(2026, 10, 11, 9, 5))                  # Sunday 09:05
    bot.tick(datetime(2026, 10, 11, 12, 5))                 # later the same day: not again
    assert docs == [(CHAT, TOPIC, "condo-report-2026-10-11.pdf")]
    assert [l for l in bot.claude.labels if l.startswith("condo")] == ["condo-resale", "condo-new_launch"]
    assert any("CONDO WEEKLY REPORT" in m[2] and "Cheap" in m[2] for m in bot.tg.sent)
