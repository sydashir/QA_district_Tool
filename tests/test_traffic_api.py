"""Search traffic from the Search Console API — the path that replaces the 1,000-row UI export.

Measured 2026-09-15: RR left 81% of its findings unweighted and GL 74%, because the UI export stops at
1,000 rows. The API returns up to 25,000 rows a request and pages through the rest.

The fake mirrors the real API, including what Google documents least: clicks and impressions arrive
as doubles, an empty page carries no "rows" key, and the last page is signalled only by a response
with 0 rows ("increasing the startRow value by 25,000 ... until you reach the last page (a response
with 0 rows)" — developers.google.com/webmaster-tools/v1/how-tos/all-your-data).
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scripts.traffic_import import api_rows, fetch_pages, pick_property
from server.models import Base, Brand, PageTraffic, TrafficImport
from server.traffic import load_brand_traffic

PERIOD = "2026-06-13..2026-09-12"


def site(url, level="siteFullUser"):
    return {"siteUrl": url, "permissionLevel": level}


# --------------------------------------------------------------------------- which property
def test_an_exact_url_prefix_property_is_used_for_the_host_we_audit():
    sites = [site("sc-domain:renaissancerecovery.com"), site("https://www.renaissancerecovery.com/")]
    assert pick_property("https://www.renaissancerecovery.com", sites)["siteUrl"] == \
        "https://www.renaissancerecovery.com/"


def test_a_domain_property_covers_a_brand_with_no_url_prefix_property():
    assert pick_property("https://connectionsoc.com", [site("sc-domain:connectionsoc.com")])["siteUrl"] \
        == "sc-domain:connectionsoc.com"
    # A domain property spans subdomains, so it also covers a www brand.
    assert pick_property("https://www.gratitudelodge.com", [site("sc-domain:gratitudelodge.com")])[
        "siteUrl"] == "sc-domain:gratitudelodge.com"


def test_a_url_prefix_property_on_the_other_host_is_never_used():
    """The design doc's trap: a URL-prefix property on the bare host cannot contain the www pages we
    audit, returns zero rows, and looks exactly like a failed grant. Better no property than that."""
    assert pick_property("https://www.renaissancerecovery.com",
                         [site("https://renaissancerecovery.com/")]) is None


def test_no_matching_property_is_none_not_a_guess():
    assert pick_property("https://inpatientmentalhealthfinder.com",
                         [site("sc-domain:connectionsoc.com")]) is None


# --------------------------------------------------------------------------- the fake API
class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.ok = 200 <= status < 300
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeApi:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls, self.bodies = [], []

    def post(self, url, json=None, timeout=None):
        self.urls.append(url)
        self.bodies.append(json)
        return self.responses.pop(0)


def row(url, clicks, impressions, position=3.2):
    return {"keys": [url], "clicks": float(clicks), "impressions": float(impressions),
            "ctr": clicks / impressions if impressions else 0.0, "position": position}


def page(*rows):
    return _Resp(200, {"rows": list(rows), "responseAggregationType": "byPage"})


EMPTY = _Resp(200, {"responseAggregationType": "byPage"})       # no "rows" key at all


# --------------------------------------------------------------------------- paging
def test_pages_are_fetched_until_google_returns_zero_rows():
    """A short page is not the end — Google's stated signal is a response with 0 rows."""
    api = FakeApi([page(row("https://x.com/a/", 9, 90), row("https://x.com/b/", 8, 80)),
                   page(row("https://x.com/c/", 7, 70), row("https://x.com/d/", 6, 60)),
                   page(row("https://x.com/e/", 5, 50)),
                   EMPTY])
    got = fetch_pages(api, "sc-domain:x.com", "2026-06-13", "2026-09-12", page_size=2,
                      sleep=lambda s: None)
    assert [r["keys"][0] for r in got] == [f"https://x.com/{p}/" for p in "abcde"]
    assert [b["startRow"] for b in api.bodies] == [0, 2, 4, 6]
    first = api.bodies[0]
    assert first["rowLimit"] == 2 and first["dimensions"] == ["page"]
    assert first["startDate"] == "2026-06-13" and first["endDate"] == "2026-09-12"
    assert first["type"] == "web" and first["dataState"] == "final"
    assert "sc-domain%3Ax.com" in api.urls[0]


def test_a_property_with_no_data_is_one_request_and_no_rows():
    api = FakeApi([EMPTY])
    assert fetch_pages(api, "https://x.com/", "2026-06-13", "2026-09-12", sleep=lambda s: None) == []
    assert len(api.bodies) == 1


def test_rate_limiting_is_retried_not_treated_as_the_end():
    """A 429 read as "no more rows" would silently truncate the site — the exact failure this path
    exists to remove."""
    api = FakeApi([_Resp(429, {"error": {"code": 429, "message": "Quota exceeded"}}),
                   page(row("https://x.com/a/", 1, 10)), EMPTY])
    got = fetch_pages(api, "https://x.com/", "2026-06-13", "2026-09-12", sleep=lambda s: None)
    assert len(got) == 1


def test_a_refusal_raises_with_googles_own_message():
    api = FakeApi([_Resp(403, {"error": {"code": 403,
                                         "message": "User does not have sufficient permission"}})])
    with pytest.raises(RuntimeError) as e:
        fetch_pages(api, "https://x.com/", "2026-06-13", "2026-09-12", sleep=lambda s: None)
    assert "403" in str(e.value) and "sufficient permission" in str(e.value)


def test_api_rows_are_measured_and_keyed_like_every_other_traffic_row():
    [r] = api_rows([row("https://www.x.com/a/", 12, 340)])
    assert r == {"url": "https://www.x.com/a/", "url_key": "www.x.com/a", "clicks": 12,
                 "impressions": 340, "position": 3.2, "value_state": "measured"}


# --------------------------------------------------------------------------- reading it back
@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False, future=True)() as s:
        yield s
    engine.dispose()


def test_api_data_replaces_the_csv_for_the_same_period(session):
    """Mixing the two would rank some pages on a 1,000-row export and others on the full data, and
    make every number read as "not proof" because one source cannot vouch for its zeros."""
    b = Brand(code="GL", name="GL", enabled=True, base_url="https://www.x.com")
    session.add(b)
    session.flush()
    for key, clicks, impr, source, state in (("www.x.com/a", 1, 10, "gsc_csv", "unknown"),
                                             ("www.x.com/b", 2, 20, "gsc_csv", "unknown"),
                                             ("www.x.com/a", 5, 50, "gsc_api", "measured")):
        session.add(PageTraffic(brand_id=b.id, url_key=key, period=PERIOD, clicks=clicks,
                                impressions=impr, source=source, value_state=state))
    session.add(TrafficImport(brand_id=b.id, period=PERIOD, source="gsc_csv", rows_read=1000,
                              pages_stored=2))
    session.add(TrafficImport(brand_id=b.id, period=PERIOD, source="gsc_api", rows_read=1,
                              pages_stored=1))
    session.commit()
    t = load_brand_traffic(session, "GL")
    assert t.pages == {"www.x.com/a": (5, 50)}
    assert t.measured is True
    assert t.capped is False        # paged to the end; the 1,000-row CSV cap no longer applies
