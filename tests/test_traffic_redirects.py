"""Traffic for findings on pages that REDIRECT — keyed on where the visitor actually lands.

Measured 2026-09-15 from every brand's crawl cache: 313 audited URLs redirect to a different page on the
same site, 1,047 findings sit on them, and 534 of those read "not in the traffic data" although 513
could be weighted — Google reports the destination, and the join used the address before the redirect.
The design doc said so from the start: "the key should be built from both the requested URL and the
final URL". Three ways to get this wrong, each pinned below:

* letting an OFF-SITE redirect inherit another brand's traffic (AH, TDRC and MHD have them);
* counting one page twice when a group names both the redirecting URL and its destination;
* inflating the page count ("on 2 pages") because a second key was consulted for traffic.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scripts import client_report as cr
from scripts.traffic_import import redirect_pairs
from server.models import Base, Brand, PageRedirect, PageTraffic
from server.traffic import BrandTraffic, affected, load_brand_traffic, reach, url_key

PERIOD = "2026-06-13..2026-09-12"
H = "https://www.gratitudelodge.com"


def crawl(url, final, status=200):
    return {"url": url, "final_url": final, "status": status}


# --------------------------------------------------------------------------- which redirects count
def test_only_same_site_redirects_are_recorded():
    rows = [
        crawl(f"{H}/old", f"{H}/new/"),                                   # same host: kept
        crawl("https://gratitudelodge.com/bare", f"{H}/landed/"),         # www twin: kept
        crawl(f"{H}/to-rr", "https://www.renaissancerecovery.com/x/"),    # another brand: NEVER
        crawl(f"{H}/slash", f"{H}/slash/"),                               # same key: not a redirect
        crawl(f"{H}/gone", f"{H}/404/", status=404),                      # did not land on a page
        crawl(f"{H}/plain", None),
    ]
    assert redirect_pairs(rows, "www.gratitudelodge.com") == {
        "www.gratitudelodge.com/old": "www.gratitudelodge.com/new",
        "gratitudelodge.com/bare": "www.gratitudelodge.com/landed",
    }


# --------------------------------------------------------------------------- reading traffic through them
def t(pages, redirects):
    return BrandTraffic(period=PERIOD, measured=True, capped=False,
                        pages={url_key(f"{H}/{p}"): v for p, v in pages.items()},
                        redirects={url_key(f"{H}/{a}"): url_key(f"{H}/{b}") for a, b in redirects.items()})


def test_a_finding_on_a_redirecting_url_is_weighted_by_where_visitors_land():
    traffic = t({"new": (120, 3_000)}, {"old": "new"})
    r = reach({url_key(f"{H}/old")}, 1, traffic, "phone", "x")
    assert r.state == "weighted"
    assert (r.clicks, r.impressions) == (120, 3_000)
    assert (r.known, r.total) == (1, 1)                 # still one page


def test_the_old_address_and_the_destination_are_both_counted_once():
    """Google can report both: a result shown under the old URL and one under the new. Different
    results, so different visits — added together, but each page only once."""
    traffic = t({"old": (5, 50), "new": (120, 3_000)}, {"old": "new"})
    r = reach({url_key(f"{H}/old")}, 1, traffic, "phone", "x")
    assert (r.clicks, r.impressions) == (125, 3_050)


def test_a_group_naming_both_urls_does_not_count_the_destination_twice():
    traffic = t({"new": (120, 3_000)}, {"old": "new"})
    rows = [cr_row(f"{H}/old", "fp-old"), cr_row(f"{H}/new", "fp-new")]
    merged, _ = cr.section_rows(rows, ["phone:*"], traffic=traffic)
    assert merged[0][13].clicks == 120                  # not 240
    assert merged[0][7] == 2                            # two audited URLs named, nothing inflated


def test_an_unrelated_page_is_untouched():
    traffic = t({"new": (120, 3_000)}, {"old": "new"})
    assert reach({url_key(f"{H}/other")}, 1, traffic, "phone", "x").state == "unmatched"


def cr_row(url, fp):
    # (check, cls, severity, issue, url, snippet, suggestion, pages, first_seen, shot, absent, fp, sources)
    return ("phone", "cross_brand_dial", "error", "dials RR", url, None, None, 1, None, None, None, fp, None)


# --------------------------------------------------------------------------- loaded from the database
@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False, future=True)() as s:
        yield s
    engine.dispose()


def test_the_loader_carries_the_brands_redirects(session):
    b = Brand(code="GL", name="GL", enabled=True, base_url=H)
    session.add(b)
    session.flush()
    session.add(PageTraffic(brand_id=b.id, url_key="www.gratitudelodge.com/new", period=PERIOD,
                            clicks=120, impressions=3_000, source="gsc_api", value_state="measured"))
    session.add(PageRedirect(brand_id=b.id, url_key="www.gratitudelodge.com/old",
                             final_key="www.gratitudelodge.com/new"))
    session.commit()
    loaded = load_brand_traffic(session, "GL")
    assert loaded.redirects == {"www.gratitudelodge.com/old": "www.gratitudelodge.com/new"}
    keys, _ = affected(f"{H}/old", None, 1)
    assert reach(keys, 1, loaded, "phone", "x").clicks == 120
