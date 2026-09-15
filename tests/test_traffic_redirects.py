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


def test_the_old_addresss_own_rows_are_not_added_to_where_it_lands():
    """Verified on live data 2026-09-15: an old address's rows inside the period can belong to a
    DIFFERENT page it used to be. COC's homepage gained 50,773 impressions from
    /mental-health/therapy/orange-county-ca, and a Tennessee Brentwood page gained a Los Angeles
    Brentwood page's. Nothing stored tells a renamed page from a replaced one, so only the page visitors
    land on counts: a floor, never another page's traffic."""
    traffic = t({"old": (5, 50), "new": (120, 3_000)}, {"old": "new"})
    r = reach({url_key(f"{H}/old")}, 1, traffic, "phone", "x")
    assert (r.clicks, r.impressions) == (120, 3_000)


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


# --------------------------------------------------------------------------- one page, however many addresses
# Found by the review of 8795ae1. A redirecting address and its destination are ONE page reached two
# ways: its traffic is the sum of both addresses' rows, each counted once, and it is one page in every
# count and sentence.
from server.traffic import sentence  # noqa: E402


def test_two_addresses_redirecting_to_one_page_count_it_once():
    """RR has 102 addresses that redirect to its homepage. A mutant that de-duplicated only against the
    named keys took an RR report row from 464 to 29,768 visits."""
    traffic = t({"home": (400, 9_000)}, {"a": "home", "b": "home"})
    r = reach({url_key(f"{H}/a"), url_key(f"{H}/b")}, 2, traffic, "phone", "x")
    assert (r.clicks, r.impressions, r.matched) == (400, 9_000, 1)


def test_one_page_reached_by_two_addresses_is_one_page_in_the_sentence():
    """142 single-page findings said 'The busiest affected page had N visits', N being the destination's
    figure — the old address and its destination were counted as two pages."""
    traffic = t({"old": (5, 50), "new": (120, 3_000)}, {"old": "new"})
    r = reach({url_key(f"{H}/old")}, 1, traffic, "phone", "x")
    assert (r.clicks, r.impressions, r.matched, r.busiest) == (120, 3_000, 1, 120)
    assert "busiest" not in sentence(r)


def test_a_copy_on_the_old_address_and_the_original_carry_the_same_traffic():
    """The audit follows redirects, so a finding on /old is usually a copy of the finding on /new. The
    copy got /old's rows plus /new's, the original only /new's, and copies filled the top of the
    dashboard above the real row (COC: 20 copies at positions 5-24). Both describe the page visitors
    land on, so both carry exactly that page's traffic."""
    traffic = t({"old": (5, 50), "new": (120, 3_000)}, {"old": "new"})
    copy = reach({url_key(f"{H}/old")}, 1, traffic, "phone", "x")
    original = reach({url_key(f"{H}/new")}, 1, traffic, "phone", "x")
    assert (copy.clicks, copy.impressions) == (original.clicks, original.impressions) == (120, 3_000)


def test_sitemap_coverage_findings_are_about_the_address_not_where_it_lands():
    """'Indexable page missing from sitemap' is about the requested address. Its destination — often
    the homepage, which IS in the sitemap — is not this finding's reach (CAD: three such findings read
    519 visits, the homepage's)."""
    traffic = t({"home": (519, 102_522)}, {"locations-served/x": "home"})
    r = reach({url_key(f"{H}/locations-served/x")}, 1, traffic, "enumeration", "indexable_unsitemapped")
    assert r.state == "unmatched"


def test_a_landing_on_the_www_twin_is_same_site():
    rows = [crawl("https://www.gratitudelodge.com/a", "https://gratitudelodge.com/b/")]
    assert redirect_pairs(rows, "www.gratitudelodge.com") == {
        "www.gratitudelodge.com/a": "gratitudelodge.com/b"}


def test_the_loader_does_not_carry_another_brands_redirects(session):
    gl = Brand(code="GL", name="GL", enabled=True, base_url=H)
    rr = Brand(code="RR", name="RR", enabled=True, base_url="https://www.renaissancerecovery.com")
    session.add_all([gl, rr])
    session.flush()
    session.add(PageTraffic(brand_id=gl.id, url_key="www.gratitudelodge.com/new", period=PERIOD,
                            clicks=1, impressions=10, source="gsc_api", value_state="measured"))
    session.add_all([PageRedirect(brand_id=gl.id, url_key="www.gratitudelodge.com/old",
                                  final_key="www.gratitudelodge.com/new"),
                     PageRedirect(brand_id=rr.id, url_key="www.renaissancerecovery.com/x",
                                  final_key="www.renaissancerecovery.com/y")])
    session.commit()
    assert load_brand_traffic(session, "GL").redirects == {
        "www.gratitudelodge.com/old": "www.gratitudelodge.com/new"}


def test_gl_no_longer_claims_redirecting_addresses_are_unranked():
    """GL's evidence sentence ended 'apart from 35 addresses that redirect to other pages'. Those are
    ranked by their destination now, so the clause became a false statement in a client report."""
    t_gl = BrandTraffic(period=PERIOD, pages={}, measured=True, capped=False)
    run = (1, __import__("datetime").datetime(2026, 9, 4), 100, False, "Gratitude Lodge", H)
    out = cr.render("GL", run, [cr_row(f"{H}/a", "fp-a")], None, t_gl)
    assert "Google recorded no search impressions" in out
    assert "35 addresses" not in out


# --------------------------------------------------------------------------- the import writes the map
import json as _json  # noqa: E402

import scripts.traffic_import as ti  # noqa: E402
from server.models import Run  # noqa: E402


class _Ok:
    ok, status_code = True, 200

    def __init__(self, body):
        self._body, self.text = body, _json.dumps(body)

    def json(self):
        return self._body


class _Google:
    def get(self, url, timeout=None):
        return _Ok({"siteEntry": [{"siteUrl": "https://www.gratitudelodge.com/",
                                   "permissionLevel": "siteFullUser"}]})


@pytest.fixture()
def api_env(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        b = Brand(code="GL", name="GL", enabled=True, base_url=H)
        s.add(b)
        s.flush()
        s.add(Run(brand_id=b.id, status="ok", pages_audited=3))
        s.commit()
    monkeypatch.setattr(ti, "SessionLocal", factory)
    monkeypatch.setattr(ti, "_api_session", lambda: _Google())
    monkeypatch.setattr(ti, "fetch_pages", lambda *a, **k: [
        {"keys": [f"{H}/new/"], "clicks": 120.0, "impressions": 3000.0, "position": 4.0}])

    def no_network(url):
        raise AssertionError(f"the import tried to fetch {url}")
    monkeypatch.setattr(ti, "_fetch_no_follow", no_network)
    cache = tmp_path / "resume.done.jsonl"
    state = {"cache": cache}
    monkeypatch.setattr(ti, "_cache_path", lambda code: state["cache"])

    def write_cache(rows):
        cache.write_text("\n".join(_json.dumps(r) for r in rows) + "\n")
    state["write"] = write_cache
    state["factory"] = factory
    return state


def _stored(factory):
    with factory() as s:
        return {r.url_key: r.final_key for r in s.query(PageRedirect).all()}


THREE_PAGES = [crawl(f"{H}/old", f"{H}/new/"), crawl(f"{H}/older", f"{H}/new/"), crawl(f"{H}/plain", f"{H}/plain/")]


def test_the_import_records_the_crawls_redirects(api_env):
    api_env["write"](THREE_PAGES)
    ti.import_api(["GL"], PERIOD)
    assert _stored(api_env["factory"]) == {"www.gratitudelodge.com/old": "www.gratitudelodge.com/new",
                                           "www.gratitudelodge.com/older": "www.gratitudelodge.com/new"}


def test_a_reimport_replaces_the_map(api_env):
    api_env["write"](THREE_PAGES)
    ti.import_api(["GL"], PERIOD)
    api_env["write"]([crawl(f"{H}/old", f"{H}/new/"), crawl(f"{H}/older", f"{H}/older/"),
                      crawl(f"{H}/plain", f"{H}/plain/")])
    ti.import_api(["GL"], PERIOD)
    assert _stored(api_env["factory"]) == {"www.gratitudelodge.com/old": "www.gratitudelodge.com/new"}


def test_a_missing_cache_keeps_the_last_map(api_env, capsys):
    """GL's crawl cache vanished once (2026-09-03). An import at that moment must not wipe the map and
    quietly un-rank 98 findings."""
    api_env["write"](THREE_PAGES)
    ti.import_api(["GL"], PERIOD)
    api_env["cache"] = None
    ti.import_api(["GL"], PERIOD)
    assert len(_stored(api_env["factory"])) == 2
    assert "not refreshed" in capsys.readouterr().out


def test_a_cache_from_a_different_crawl_does_not_replace_the_map(api_env, capsys):
    """MHD's cache holds the 334 pages of a REFUSED attempt, while its report shows run 108's 621. A map
    built from it would describe pages the report is not about."""
    api_env["write"](THREE_PAGES)
    ti.import_api(["GL"], PERIOD)
    with api_env["factory"]() as s:
        s.query(Run).update({"pages_audited": 900})
        s.commit()
    api_env["write"]([crawl(f"{H}/other", f"{H}/elsewhere/")])
    ti.import_api(["GL"], PERIOD)
    assert _stored(api_env["factory"]) == {"www.gratitudelodge.com/old": "www.gratitudelodge.com/new",
                                           "www.gratitudelodge.com/older": "www.gratitudelodge.com/new"}
    assert "different crawl" in capsys.readouterr().out


# --------------------------------------------------------------------------- the migration matches the model
def test_migration_0005_creates_exactly_the_model_table():
    """No test runs a migration. Tests build tables from the models, so a migration that drifts from
    PageRedirect (a misnamed table, a dropped unique constraint) would pass here and break a fresh
    `alembic upgrade` on the server."""
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa
    path = Path(__file__).resolve().parent.parent / "migrations" / "versions" / "20260915_0005_page_redirects.py"
    spec = importlib.util.spec_from_file_location("m0005", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    made = {}

    class _Op:
        def create_table(self, name, *items, **kw):
            made["table"] = sa.Table(name, sa.MetaData(), *items)
    mod.op = _Op()
    mod.upgrade()
    table, model = made["table"], PageRedirect.__table__
    assert table.name == model.name
    assert set(table.columns.keys()) == set(model.columns.keys())
    uniques = lambda tb: {(c.name, tuple(c.columns.keys())) for c in tb.constraints  # noqa: E731
                          if isinstance(c, sa.UniqueConstraint)}
    assert uniques(table) == uniques(model)



# --------------------------------------------------------------------------- found by verification 2026-09-15
def test_a_finding_on_the_destination_does_not_take_a_redirecting_addresss_rows():
    """COC's homepage title finding 404738 read 73,643 impressions instead of its own 22,869: the extra
    50,773 were an Orange County page's, from before that address redirected to the homepage."""
    traffic = t({"home": (479, 22_869), "orange-county-ca": (67, 50_773)}, {"orange-county-ca": "home"})
    r = reach({url_key(f"{H}/home")}, 1, traffic, "meta", "x")
    assert (r.clicks, r.impressions) == (479, 22_869)


def test_a_later_refused_run_does_not_open_the_guard(api_env, capsys):
    """MHD: its newest run (139) was REFUSED with 0 pages audited, and its report shows run 108. Compared
    with the refused run, the guard would see 0 and let a 334-page cache through."""
    from datetime import datetime
    api_env["write"](THREE_PAGES)
    ti.import_api(["GL"], PERIOD)
    with api_env["factory"]() as s:
        run = s.query(Run).one()
        run.pages_audited, run.started_at = 900, datetime(2026, 8, 28)
        s.add(Run(brand_id=run.brand_id, status="refused", pages_audited=0,
                  started_at=datetime(2026, 9, 7)))
        s.commit()
    api_env["write"]([crawl(f"{H}/other", f"{H}/elsewhere/")])
    ti.import_api(["GL"], PERIOD)
    assert len(_stored(api_env["factory"])) == 2
    assert "different crawl" in capsys.readouterr().out


def test_a_cache_that_repeats_urls_is_measured_in_pages_not_lines(api_env, capsys):
    """RR's cache has 14,210 lines for 7,998 distinct URLs. Counting lines would let a smaller crawl
    pass as the run the report shows."""
    api_env["write"](THREE_PAGES)
    ti.import_api(["GL"], PERIOD)
    with api_env["factory"]() as s:
        s.query(Run).update({"pages_audited": 5})
        s.commit()
    api_env["write"]([crawl(f"{H}/other", f"{H}/elsewhere/")] * 6)
    ti.import_api(["GL"], PERIOD)
    assert len(_stored(api_env["factory"])) == 2
    assert "different crawl" in capsys.readouterr().out


def test_the_guard_compares_with_the_latest_ok_run_not_an_older_one(api_env):
    """AR's oldest ok run audited 502 pages; its latest audited 474, which its cache matches. Compared
    with the oldest, AR's map would freeze."""
    from datetime import datetime
    with api_env["factory"]() as s:
        run = s.query(Run).one()
        run.pages_audited, run.started_at = 3, datetime(2026, 9, 4)
        s.add(Run(brand_id=run.brand_id, status="ok", pages_audited=900, started_at=datetime(2026, 8, 1)))
        s.commit()
    api_env["write"](THREE_PAGES)
    ti.import_api(["GL"], PERIOD)
    assert len(_stored(api_env["factory"])) == 2
