from datetime import date
from types import SimpleNamespace

from propbot import pgcheck, site, tracker
from propbot.hunt import listing_key

TODAY = date(2026, 10, 11)
URL = "https://www.propertyguru.com.sg/listing/hdb-for-sale-216-serangoon-avenue-4-500279788"


def add(db, name, price, url=URL):
    tracker.record(db, "hdb_resale", [SimpleNamespace(key=listing_key(url), name=name, area="", url=url, bedrooms=None,
                                                      tenure="99 year", lease_left=None, sqft=None, price=price)], TODAY)


class Verifier:
    def __init__(self, **out):
        self.out, self.calls = out, 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: self.out)


def shown(db):
    return [i["name"] for i in site.items(db, TODAY)]


def test_name_matches():
    assert pgcheck.name_matches("Block 216 Serangoon Avenue 4", "216 Serangoon Avenue 4, Hougang HDB Flat For Sale at S$ 749,997")
    assert not pgcheck.name_matches("216 Serangoon Avenue 4", "215 Serangoon Avenue 4 HDB Flat For Sale")   # other block


def test_listed_page_shows_card_with_page_price_and_photo(db):
    add(db, "216 Serangoon Avenue 4", 740_000)
    assert shown(db) == []                                                 # never opened: hidden
    v = Verifier(status="listed", price=749_997, image="https://sg1-cdn.pgimgs.com/a.jpg",
                 title="216 Serangoon Avenue 4, Hougang HDB Flat For Sale at S$ 749,997")
    assert pgcheck.run(db, TODAY, client=v)["listed"] == 1
    item = site.items(db, TODAY)[0]
    assert item["price"] == 749_997 and item["photo"].endswith("a.jpg")    # the page's price wins
    assert pgcheck.run(db, TODAY, client=v)["listed"] == 0 and v.calls == 1   # once a day


def test_wrong_page_gone_and_blocked_stay_hidden(db):
    add(db, "216 Serangoon Avenue 4", 740_000)
    pgcheck.run(db, TODAY, client=Verifier(status="listed", price=1, title="999 Other Road For Sale at S$ 1"))
    assert shown(db) == []                                                 # title does not match the listing
    pgcheck.run(db, date(2026, 10, 12), client=Verifier(status="blocked"))
    assert shown(db) == []
    pgcheck.run(db, date(2026, 10, 13), client=Verifier(status="gone"))
    assert tracker.gone_keys(db) == {listing_key(URL)} and shown(db) == []
