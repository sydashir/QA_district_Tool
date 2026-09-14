"""The findings list, ordered by search traffic when one brand is selected.

Same rules as the client report (server/traffic.py is shared by both), tested here through the API
because the API has its own way to get them wrong: the order is worked out outside SQL, so paging,
filters and the fall-back order all have to survive that. Throwaway SQLite, never the live database.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from server.api import app as api_app
from server.db import get_session
from server.models import Base, Brand, Finding, PageTraffic, Run, fp_hash
from server.traffic import NOT_CONNECTED

T0 = datetime(2026, 9, 4, 2, 0, 0)
GL = "https://www.gratitudelodge.com"


@pytest.fixture()
def sessions():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, future=True)
    engine.dispose()


@pytest.fixture()
def client(sessions):
    def _override():
        with sessions() as s:
            yield s
    api_app.dependency_overrides[get_session] = _override
    with TestClient(api_app) as c:
        yield c
    api_app.dependency_overrides.clear()


def _finding(s, run, name, *, severity, page_count=1, check="phone", cls="x", url=None):
    fp = f"{run.brand_id}|{name}"
    s.add(Finding(brand_id=run.brand_id, run_id=run.id, fingerprint=fp, fingerprint_hash=fp_hash(fp),
                  url=url or f"{GL}/{name}", check=check, severity=severity, issue=name,
                  details={"class": cls}, status="persisting", page_count=page_count,
                  first_seen="2026-08-01"))


@pytest.fixture()
def seed(sessions):
    with sessions() as s:
        gl = Brand(code="GL", name="Gratitude Lodge", enabled=True, base_url=GL)
        cad = Brand(code="CAD", name="California Detox", enabled=True,
                    base_url="https://californiadetox.com")
        s.add_all([gl, cad])
        s.flush()
        gl_run = Run(brand_id=gl.id, started_at=T0, finished_at=T0, status="ok", pages_audited=10)
        cad_run = Run(brand_id=cad.id, started_at=T0, finished_at=T0, status="ok", pages_audited=10)
        s.add_all([gl_run, cad_run])
        s.flush()
        _finding(s, gl_run, "quiet", severity="error")
        _finding(s, gl_run, "busiest", severity="warning")
        _finding(s, gl_run, "busy", severity="error")
        _finding(s, gl_run, "wide", severity="error", page_count=40)
        _finding(s, gl_run, "hidden", severity="error", check="enumeration",
                 cls="noindex_unsitemapped")
        for path, clicks, impr in (("busiest", 50_000, 90_000), ("busy/", 900, 2_000),
                                   ("hidden", 7_000, 9_000)):
            s.add(PageTraffic(brand_id=gl.id, url_key=f"www.gratitudelodge.com/{path}".rstrip("/"),
                              period="2026-06-13..2026-09-12", clicks=clicks, impressions=impr,
                              source="gsc_csv", value_state="unknown"))
        # CAD has findings and no traffic data at all.
        _finding(s, cad_run, "one", severity="error", url="https://californiadetox.com/one")
        _finding(s, cad_run, "many", severity="error", page_count=30,
                 url="https://californiadetox.com/many")
        s.commit()


def _issues(body):
    return [i["issue"] for i in body["items"]]


def test_one_brand_is_ordered_by_traffic_within_severity(client, seed):
    body = client.get("/api/findings", params={"brand": "GL"}).json()
    # busy (error, 900 visits) > the unweighted errors by page count > the warning with 50,000.
    # `hidden` has 7,000 visits in the data and is still unweighted: a no-indexed page's traffic is
    # not the finding's reach.
    assert _issues(body) == ["busy", "wide", "hidden", "quiet", "busiest"]
    by = {i["issue"]: i for i in body["items"]}
    assert by["busy"]["traffic_short"] == "900 search visits · 2,000 impressions"
    assert by["quiet"]["traffic_short"] == "not in the traffic data"
    assert by["hidden"]["traffic_state"] == "by_design"
    assert "Search Console, 13 Jun – 12 Sep 2026" in body["ordering_note"]


def test_paging_follows_the_traffic_order(client, seed):
    page2 = client.get("/api/findings", params={"brand": "GL", "per_page": 2, "page": 2}).json()
    assert page2["total"] == 5
    assert _issues(page2) == ["hidden", "quiet"]


def test_filters_still_apply_when_ordering_by_traffic(client, seed):
    body = client.get("/api/findings", params={"brand": "GL", "severity": "warning"}).json()
    assert _issues(body) == ["busiest"]
    assert client.get("/api/findings", params={"brand": "GL", "q": "qui"}).json()["total"] == 1


def test_a_brand_with_no_traffic_keeps_the_old_order_and_says_why(client, seed):
    body = client.get("/api/findings", params={"brand": "CAD"}).json()
    assert _issues(body) == ["many", "one"]
    assert body["ordering_note"] == NOT_CONNECTED
    assert all(i["traffic_short"] is None for i in body["items"])


def test_all_brands_are_never_ranked_against_each_other(client, seed):
    body = client.get("/api/findings").json()
    assert "Choose one brand" in body["ordering_note"]
    assert _issues(body)[:2] == ["wide", "many"]          # page count, as before
    assert all(i["traffic_state"] is None for i in body["items"])


def test_the_finding_detail_carries_the_traffic_sentence(client, seed):
    busy = client.get(f"/api/findings/{fp_hash('1|busy')}").json()
    assert busy["traffic"]["state"] == "weighted"
    assert "900 visits from Google" in busy["traffic"]["note"]
    cad = client.get(f"/api/findings/{fp_hash('2|one')}").json()
    assert cad["traffic"] == {"state": "not_connected", "note": NOT_CONNECTED}


def test_a_mixed_list_ranks_every_row_on_visits(client, seed, sessions):
    """Measured on GL's live data: two schema findings with 97 and 473 visits outranked the
    site-wide phone fault with 34,974, because their impressions (128,276) were compared against its
    visits. Within one list, one number. Impressions only when every row is a search problem."""
    with sessions() as s:
        gl = s.scalar(select(Brand).where(Brand.code == "GL"))
        run = s.scalar(select(Run).where(Run.brand_id == gl.id))
        _finding(s, run, "shown", severity="error", check="schema", cls="missing")
        _finding(s, run, "clicked", severity="error", check="schema", cls="missing")
        for path, clicks, impr in (("shown", 5, 90_000), ("clicked", 50, 1_000)):
            s.add(PageTraffic(brand_id=gl.id, url_key=f"www.gratitudelodge.com/{path}",
                              period="2026-06-13..2026-09-12", clicks=clicks, impressions=impr,
                              source="gsc_csv", value_state="unknown"))
        s.commit()
    mixed = client.get("/api/findings", params={"brand": "GL"}).json()
    assert _issues(mixed)[:3] == ["busy", "clicked", "shown"]       # 900 > 50 > 5 visits
    assert "impressions instead" not in mixed["ordering_note"]
    only = client.get("/api/findings", params={"brand": "GL", "check": "schema"}).json()
    assert _issues(only) == ["shown", "clicked"]                     # 90,000 > 1,000 impressions
    assert "impressions in Google's results" in only["ordering_note"]
