import json
from datetime import date
from types import SimpleNamespace

from propbot import site, tracker
from propbot.hunt import listing_key

TODAY = date(2026, 10, 11)


def listing(db, kind, name, price, url, area=""):
    tracker.record(db, kind, [SimpleNamespace(key=listing_key(url), name=name, area=area, url=url, bedrooms=None,
                                              tenure="99 year", lease_left=None, sqft=None, price=price)], TODAY)


def test_export_ranks_hdb_against_its_block_and_fills_photos(db, tmp_path):
    for i, p in enumerate([600_000, 610_000, 590_000, 600_000]):
        db.insert("hdb_resale", {"key": f"s{i}", "month": "2026-08", "town": "TAMPINES", "flat_type": "4 ROOM", "block": "1",
                                 "street": "TEST ST", "storey": "04 TO 06", "sqm": 100.0, "model": "A", "lease_start": 1996,
                                 "remaining_lease": 68.0, "price": p, "first_seen": "2026-08-30"})
    listing(db, "hdb_resale", "1 Test Street", 570_000, "https://www.edgeprop.sg/listing/hdb/1-test-street/m_1")
    listing(db, "hdb_resale", "9 Test Street", 500_000, "https://www.propertyguru.com.sg/listing/hdb-for-sale-9-test-street-2")
    listing(db, "condo_resale", "Logo Condo", 1_500_000, "https://www.edgeprop.sg/listing/condo/m_3", "Bishan")

    pages = {"m_1": '<meta property="og:image" content="https://img.tepcdn.com/a.jpg">',
             "m_3": '<meta property="og:image" content="https://sg.tepcdn.com/EdgeProp-logo.png">'}
    fetched = []

    class Fetcher:
        def fetch(self, url):
            fetched.append(url)
            return SimpleNamespace(text=pages[url.rsplit("/", 1)[1]])

    assert site.fetch_photos(db, Fetcher(), TODAY) == 1
    assert not any("propertyguru" in u for u in fetched)          # Cloudflare challenge: never fetched
    assert site.fetch_photos(db, Fetcher(), TODAY) == 0 and len(fetched) == 2   # failures remembered, no refetch

    assert site.export(db, tmp_path, TODAY) == 2      # PropertyGuru card hidden until pgcheck has opened its page
    db.insert("pg_check", {"key": listing_key("https://www.propertyguru.com.sg/listing/hdb-for-sale-9-test-street-2"),
                           "checked_on": TODAY.isoformat(), "status": "listed", "price": 500_000, "name_ok": 1, "title": "9 Test Street"})
    assert site.export(db, tmp_path, TODAY) == 3
    items = {x["name"]: x for x in json.loads((tmp_path / "data.json").read_text(encoding="utf-8"))["items"]}
    a = items["1 Test Street"]
    assert a["type"] == "4 Room" and a["area"] == "Tampines" and a["reliable"] and a["deal_pct"] == 5.0
    assert a["photo"] == "https://img.tepcdn.com/a.jpg" and a["change"] == "new"
    assert items["9 Test Street"]["deal_pct"] is None and items["9 Test Street"]["area"] == "Tampines"   # town from street
    assert items["Logo Condo"]["photo"] == "" and items["Logo Condo"]["label"] == "Resale condo"


def test_verdict_tiers_and_reasons():
    base = {"kind": "hdb_resale", "type": "4 Room", "price": 570_000, "median": 600_000, "sales": 4, "reliable": True,
            "deal_pct": 5.0, "tenure": "99 year", "lease_left": 80, "change": "", "prev_price": None, "mrt": "Tampines", "mrt_m": 300}
    tier, score, why = site.verdict(base)
    assert tier == "Top pick" and score == 7 and "4 sales of 4 Room flats" in why[0] and "any adult buyer" in why[1]
    assert site.verdict(dict(base, lease_left=55))[0] == "Short lease"
    assert site.verdict(dict(base, price=660_000, deal_pct=-10.0))[0] == "Above market"
    assert site.verdict(dict(base, kind="condo_resale", deal_pct=None, reliable=False, mrt_m=None))[0] == "Not enough data"


def test_bto_keeps_only_allowed_links(settings):
    from propbot import bto
    rows = bto.clean([{"project": "Bayshore Vista", "url": "https://www.hdb.gov.sg/x"},
                      {"project": "bayshore vista", "url": "https://www.hdb.gov.sg/y"},           # duplicate
                      {"project": "Tengah Grove", "url": "https://www.99.co/bto"}], settings)        # never fetch
    assert [(r["project"], r["url"]) for r in rows] == [("Bayshore Vista", "https://www.hdb.gov.sg/x"), ("Tengah Grove", "")]


def test_fengshui_reads_the_good_and_the_bad():
    from propbot import fengshui
    assert fengshui.kind_of_park("BEDOK RESERVOIR PK") == "water" and fengshui.kind_of_park("BUKIT BATOK NATURE PARK") == "hill"
    assert fengshui.kind_of_park("GROVE LANE PG") is None and fengshui._nice("BEDOK RESERVOIR PK") == "Bedok Reservoir Park"
    good = fengshui.reading("889A Tampines St 81", 888_000, {"water": ("BEDOK RESERVOIR PK", 600)}, "Tampines MRT", 300)
    assert good["stars"] == 5 and not good["bad"] and good["good"][0].startswith("Triple 8")
    bad = fengshui.reading("114 Alkaff", 540_000, {"yin": ("Mount Vernon Columbarium", 700), "water": ("X RESERVOIR", 5000)})
    assert bad["stars"] == 1 and not bad["good"] and len(bad["bad"]) == 4     # columbarium, 4, ends in 14, 4 in the price


def test_dedupe_merges_the_same_home_from_two_sites():
    a = {"kind": "condo_new_launch", "name": "Narra Residences", "price": 1_500_000, "last_seen": "2026-10-08", "first_seen": "2026-10-05",
         "url": "https://www.edgeprop.sg/x", "site": "edgeprop.sg", "photo": ""}
    b = dict(a, url="https://www.propertyguru.com.sg/y", site="propertyguru.com.sg", last_seen="2026-10-07", photo="p.jpg", first_seen="2026-10-03")
    out = site.dedupe([b, a, dict(a, price=2_000_000)])
    assert len(out) == 2 and out[0]["url"] == a["url"] and out[0]["also"] == [{"site": "propertyguru.com.sg", "url": b["url"]}]
    assert out[0]["photo"] == "p.jpg" and out[0]["first_seen"] == "2026-10-03"


PROPNEX_PAGE = """[![Image 1](https://s3.example.com/pnimgs/listing/1/2/909165/a.jpg)](https://www.propnex.com/listing-details/909165/125-bedok-north-road)

### [125 Bedok North Road](https://www.propnex.com/listing-details/909165/125-bedok-north-road) Shortlist 

[* ![Image 2](https://www.propnex.com/img/listing/ic_location.png)Bedok North Road - D16 * ![Image 3](https://www.propnex.com/img/listing/ic_type.png)HDB Apartment for sale! * ![Image 4](x.png) 3 * ![Image 5](y.png) 2 *  893 sqft #### $520,000 $582 psf](https://www.propnex.com/listing-details/909165/125-bedok-north-road)

### [624A TAMPINES AVENUE 12](https://www.propnex.com/listing-details/1030953/624a-tampines-avenue-12) Shortlist 

[* Tampines Avenue 12 - D18 * HDB Apartment for sale! *  4 *  2 *  1,001 sqft #### $8,280,000](https://www.propnex.com/listing-details/1030953/624a-tampines-avenue-12)
"""


def test_propnex_page_is_parsed_and_budget_checked(settings, db):
    from propbot import propnex
    xs = propnex.parse(PROPNEX_PAGE)
    assert [(x["name"], x["district"], x["bedrooms"], x["sqft"], x["price"]) for x in xs] == [
        ("125 Bedok North Road", "D16", 3, 893.0, 520_000.0), ("624A Tampines Avenue 12", "D18", 4, 1001.0, 8_280_000.0)]
    assert xs[0]["photo"].endswith("/a.jpg") and xs[1]["photo"] == ""

    class Http:
        def get(self, url, timeout):
            return SimpleNamespace(status_code=200, text=PROPNEX_PAGE if "HDB" in url else "Title: Human Verification")

    class Limiter:
        def acquire(self, *a):
            return 0

    assert propnex.run(settings, db, Http(), Limiter(), TODAY) == 1          # the 8.28M flat is over budget; the challenge stops the rest
    assert [r["name"] for r in tracker.active(db, "hdb_resale", TODAY)] == ["125 Bedok North Road"]
    assert db.scalar("SELECT image FROM listing_image") .endswith("/a.jpg")


def test_propnex_retail_maps_to_our_categories():
    from propbot.propnex import retail_kind
    assert retail_kind("Food & Beverage", "Kopitiam at Bedok") == "coffeeshop"
    assert retail_kind("Shop / Shophouse", "Jalan Besar Road Adjoining Shophouses") == "shophouse"
    assert retail_kind("Shop / Shophouse", "3 Everton Park") == "hdb_shop"
    assert retail_kind("Other Retail", "846 Yishun Ring Road") == "hdb_shop"
    assert retail_kind("Shop / Shophouse", "Jurong East Prime Corner Shop, YUHUA VILLAGE") == "hdb_shop"
    assert retail_kind("Mall Shop", "Kembangan Plaza") == "strata_commercial"


def test_insights_money_rent_proof_and_outlook(settings, db):
    from propbot import insights
    from propbot.engine.card import rates_from_db
    from propbot.rules.loader import Rules
    rules = Rules.load(settings.rules_dir / "sg_property_rules.yaml")
    rates = rates_from_db(db, settings, rules, None)

    hdb = {"kind": "hdb_resale", "price": 600_000, "lease_left": 80, "type": "4 Room"}
    m = insights.money(hdb, rules, rates)
    assert m["hdb"]["down"] == 150_000 and m["bank"]["down"] == 150_000 and m["bsd"] == 12_600      # 75% loan; BSD 1% of 180k + 2% of 180k + 3% of 240k
    assert m["grants"]["cpf_housing_grant"] == 80_000 and m["hdb"]["monthly"] > 0 and m["hdb"]["years"] == 25
    assert "hdb" not in insights.money(dict(hdb, kind="condo_resale"), rules, rates)                 # no HDB loan for a condo
    shop = insights.money(dict(hdb, kind="shophouse", price=2_000_000), rules, rates)
    assert shop["bank"]["cash_only"] and shop["bank"]["ltv"] == 70                                  # commercial: no CPF, 70% planning loan

    db.insert("hdb_rent", {"quarter": "2026-Q2", "town": "TAMPINES", "flat_type": "4-RM", "median_rent": 3000})
    assert insights.rent(db, "Tampines", "4 Room", 600_000) == {"monthly": 3000, "quarter": "2026-Q2", "yield_pct": 6.0}
    assert insights.rent(db, "Tampines", "", 600_000) is None

    rows = [{"month": f"2026-0{i}", "storey": s, "sqm": 90.0, "price": p}
            for i, (s, p) in enumerate([("01 TO 03", 500_000), ("07 TO 09", 560_000), ("13 TO 15", 640_000)], 1)]
    pr = insights.block_proof(rows)
    assert pr["recent"][0]["month"] == "2026-03" and [b["band"][:3] for b in pr["bands"]] == ["Low", "Mid", "Hig"]

    base = {"tenure": "99 year", "lease_left": 85, "area": "Tampines", "type": "4 Room", "deal_pct": 6.0, "reliable": True,
            "upcoming_mrt": [], "trend": {"cagr_pct": 5.0, "sales": 40}}
    up = insights.outlook(base, settings)
    assert up["label"] == "Likely to rise" and up["pct"] > 3 and any("at most +4.0%" in w["text"] for w in up["why"])
    fall = insights.outlook(dict(base, lease_left=45, trend={"cagr_pct": 0.0, "sales": 40}, deal_pct=-8.0), settings)
    assert fall["label"] == "Likely to fall" and fall["pct"] < -3
    none = insights.outlook(dict(base, trend=None, lease_left=None, tenure="unknown"), settings)
    assert none["label"] == "Not enough data" and none["pct"] is None
    free = insights.outlook(dict(base, tenure="freehold", lease_left=None, trend=None), settings)
    assert free["pct"] == 0 and any("Freehold" in w["text"] for w in free["why"])
