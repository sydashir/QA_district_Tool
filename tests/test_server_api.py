"""Product-layer API tests — the behaviours that have already cost us real incidents.

Everything here runs against a throwaway in-memory SQLite database. `models.py` declares
`JSONType` with a SQLite variant precisely so this is possible, and the `get_session`
dependency is overridden so **the live Postgres is never opened** — these tests run while a
nine-brand crawl is in flight and must not so much as connect to it. The one endpoint that
would enqueue real work has its queue stubbed for the same reason: a test must never defer a
six-hour audit.

What is worth testing here is not "does FastAPI serialise a model". It is the handful of
judgements the API makes that a human then reads as fact: what counts as open, whether a
triage decision survives the next run, and whether a brand that could not be audited can ever
be mistaken for a brand that was audited and found clean.
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from server.api import app as api_app
from server.db import get_session
from server.models import Base, Brand, Finding, Run, Triage, fp_hash

# A fixed clock. Run ordering is by `started_at`, so the tests have to be explicit about it
# rather than relying on insertion order — the importer learned that lesson from a 21-hour
# clock skew that made directory names sort backwards.
T0 = datetime(2026, 8, 14, 2, 0, 0)


# --------------------------------------------------------------------------- fixtures
@pytest.fixture()
def sessions():
    """A private SQLite database per test.

    StaticPool + check_same_thread=False because TestClient runs sync endpoints in a
    threadpool: a plain in-memory SQLite would hand each thread a different, empty database.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    yield factory
    engine.dispose()


@pytest.fixture()
def client(sessions):
    def _session_override():
        with sessions() as session:
            yield session

    api_app.dependency_overrides[get_session] = _session_override
    with TestClient(api_app) as c:
        yield c
    api_app.dependency_overrides.clear()


# --------------------------------------------------------------------------- seed helpers
def mk_brand(session, code: str, name: str = "", base_url: str = "", **kw) -> Brand:
    brand = Brand(code=code, name=name or code, enabled=True,
                  base_url=base_url or f"https://{code.lower()}.example.com", **kw)
    session.add(brand)
    session.flush()
    return brand


def mk_run(session, brand: Brand, *, at: datetime, status: str = "ok", **kw) -> Run:
    run = Run(brand_id=brand.id, started_at=at, finished_at=at, status=status,
              pages_audited=kw.pop("pages_audited", 100), **kw)
    session.add(run)
    session.flush()
    return run


def mk_finding(session, run: Run, *, fp: str, check: str = "phone", severity: str = "error",
               status: str = "new", page_count: int = 1, url: str = "",
               issue: str = "", snippet: str | None = None) -> Finding:
    f = Finding(
        brand_id=run.brand_id, run_id=run.id, fingerprint=fp, fingerprint_hash=fp_hash(fp),
        url=url or f"https://example.com/{check}", check=check, severity=severity,
        issue=issue or f"{check} problem", snippet=snippet, details={},
        status=status, page_count=page_count, first_seen="2026-08-01")
    session.add(f)
    session.flush()
    return f


# Fingerprints are the product's real identity, so the tests name them rather than passing
# strings inline — every cross-run assertion below turns on one of these being IDENTICAL in
# two different runs.
FP_PHONE = "RR|phone|cross_brand_dial|/detox"
FP_LINKS = "RR|broken_links|404|/programs"
FP_META = "RR|meta|missing_description|/about"
FP_BLANK = "RR|blank|thin|/contact"
FP_RESOLVED = "RR|phone|resolved-one|/old"
FP_RULE_CHANGED = "RR|phone|rule-changed-one|/rules"
FP_UNSITEMAPPED = "RR|phone|unsitemapped-one|/gone-from-sitemap"
FP_REMOVED = "RR|phone|removed-one|/deleted"
FP_GL = "GL|phone|cross_brand_dial|/rehab"
FP_MHD = "MHD|blank|thin|/facility"


@pytest.fixture()
def seed(sessions):
    """Three brands in the three states that matter: healthy, healthy, and refused.

    RR's latest run carries one finding of EVERY diff status, which is what makes the
    open-count assertions meaningful — the non-open rows genuinely exist and are genuinely
    excluded, rather than the query returning 4 because only 4 rows were inserted.
    """
    with sessions() as s:
        rr = mk_brand(s, "RR", "Renaissance Recovery", "https://www.renaissancerecovery.com",
                      schedule_cron="0 2 * * *")
        gl = mk_brand(s, "GL", "Gratitude Lodge", "https://www.gratitudelodge.com",
                      schedule_cron="0 2 * * *")
        # MHD is deliberately unscheduled: a full census is ~85h, so it is a labelled sample.
        mhd = mk_brand(s, "MHD", "Inpatient Mental Health Finder",
                       "https://inpatientmentalhealthfinder.com", schedule_cron=None)

        rr_old = mk_run(s, rr, at=T0 - timedelta(days=2))
        mk_finding(s, rr_old, fp=FP_PHONE, check="phone", status="persisting", page_count=3)

        rr_latest = mk_run(s, rr, at=T0 - timedelta(days=1), pages_audited=7994)
        # -- open --------------------------------------------------------------------
        mk_finding(s, rr_latest, fp=FP_PHONE, check="phone", severity="error",
                   status="new", page_count=3,
                   url="https://www.renaissancerecovery.com/detox",
                   issue="tel: link dials another brand")
        mk_finding(s, rr_latest, fp=FP_LINKS, check="broken_links", severity="error",
                   status="persisting", page_count=12)
        mk_finding(s, rr_latest, fp=FP_META, check="meta", severity="warning",
                   status="persisting", page_count=50)
        mk_finding(s, rr_latest, fp=FP_BLANK, check="blank", severity="info",
                   status="persisting", page_count=99)
        # -- NOT open, one per status ------------------------------------------------
        mk_finding(s, rr_latest, fp=FP_RESOLVED, status="resolved", severity="error")
        mk_finding(s, rr_latest, fp=FP_RULE_CHANGED, status="rule_changed", severity="error",
                   page_count=7)
        mk_finding(s, rr_latest, fp=FP_UNSITEMAPPED, status="page_unsitemapped",
                   severity="error")
        mk_finding(s, rr_latest, fp=FP_REMOVED, status="page_removed", severity="error")

        gl_run = mk_run(s, gl, at=T0 - timedelta(days=1))
        mk_finding(s, gl_run, fp=FP_GL, check="phone", severity="error", status="new",
                   page_count=2, url="https://www.gratitudelodge.com/rehab",
                   snippet="tel:8663309449 dials the Renaissance line")

        # MHD: a good run, then a refusal. The refusal is the most RECENT thing that
        # happened to this brand and must not erase what the good run found.
        mhd_ok = mk_run(s, mhd, at=T0 - timedelta(days=3), pages_audited=1200,
                        partial_sample=True)
        mk_finding(s, mhd_ok, fp=FP_MHD, check="blank", severity="error", status="persisting")
        mk_run(s, mhd, at=T0 - timedelta(hours=1), status="refused", pages_audited=0,
               error_text="no pages could be enumerated, so nothing was audited. The website "
                          "was unreachable or its page index is missing. This brand has NOT "
                          "been given a clean bill of health.")
        s.commit()
    return {"rr": "RR", "gl": "GL", "mhd": "MHD"}


def _brand(client, code: str) -> dict:
    return next(b for b in client.get("/api/brands").json() if b["code"] == code)


# =========================================================================== 1. open-ness
def test_open_counts_exclude_every_non_open_status(client, seed):
    """INCIDENT: counting `rule_changed` as open reported RR at 25,904 open findings when the
    truth was 16,951; counting it as *resolved* instead made a sheet claim "171 fixed" when
    nothing had been fixed. Only `new` and `persisting` are open defects.

    RR's latest run holds one finding of each of the six diff statuses. The four non-open ones
    must be present in the database (asserted below) and absent from every count.
    """
    body = client.get("/api/findings", params={"brand": "RR"}).json()
    assert body["total"] == 4, "open = new + persisting only"
    assert {i["status"] for i in body["items"]} == {"new", "persisting"}

    rr = _brand(client, "RR")
    assert (rr["open_error"], rr["open_warning"], rr["open_info"]) == (2, 1, 1)


@pytest.mark.parametrize("status", ["resolved", "rule_changed", "page_unsitemapped",
                                    "page_removed"])
def test_non_open_rows_exist_and_are_reachable_only_on_request(client, seed, status):
    """The counterweight to the test above: prove the excluded rows are really there.

    Without this, `total == 4` would also pass on a database that simply lost them, and the
    open-count test would be asserting nothing.
    """
    body = client.get("/api/findings", params={"brand": "RR", "status": status}).json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == status


def test_checks_facet_counts_only_open_findings(client, seed):
    """The filter sidebar's counts come from the same definition of open, or the numbers in
    the sidebar disagree with the list they filter."""
    counts = {row["check"]: row["count"] for row in client.get("/api/checks").json()}
    # RR's phone rows: 1 new (open) + resolved/rule_changed/unsitemapped/removed (not open),
    # plus RR's older run (persisting) and GL's new one.
    assert counts["phone"] == 3
    assert counts["broken_links"] == 1
    # ordered by count, descending — the sidebar leads with the biggest problem
    ordered = [row["count"] for row in client.get("/api/checks").json()]
    assert ordered == sorted(ordered, reverse=True)


def test_untriaged_error_counts_only_open_errors(client, seed, sessions):
    """`untriaged_error` is the number the team reads as "our workload". It must count open
    ERRORs that nobody has ruled on — not warnings, not resolved rows, not acknowledged ones."""
    assert _brand(client, "RR")["untriaged_error"] == 2

    r = client.patch(f"/api/triage/{fp_hash(FP_LINKS)}", json={"state": "wontfix",
                                                              "note": "third-party redirect"})
    assert r.status_code == 200
    assert _brand(client, "RR")["untriaged_error"] == 1

    # `open` triage is the absence of a decision, so it must NOT reduce the workload count
    client.patch(f"/api/triage/{fp_hash(FP_PHONE)}", json={"state": "open"})
    assert _brand(client, "RR")["untriaged_error"] == 1


# =========================================================================== 2. triage lifetime
def test_triage_survives_the_next_run(client, sessions):
    """THE single most important product guarantee.

    A finding row is an observation belonging to one run; a triage decision is a human
    judgement about a defect and has to outlive every run. Triage is therefore keyed on
    fingerprint, not on finding id — and this test is what stops anyone "simplifying" that.

    Run 2 re-observes the SAME fingerprint as a brand-new row with a different id. If triage
    were keyed on the row, the decision would silently evaporate and the finding would come
    back onto the team's queue as untriaged work they had already dismissed.
    """
    with sessions() as s:
        gl = mk_brand(s, "GL", "Gratitude Lodge")
        run1 = mk_run(s, gl, at=T0 - timedelta(days=2))
        first = mk_finding(s, run1, fp=FP_GL, severity="error", status="new")
        first_id = first.id
        s.commit()

    assert _brand(client, "GL")["untriaged_error"] == 1
    r = client.patch(f"/api/triage/{fp_hash(FP_GL)}",
                     json={"state": "acknowledged", "note": "ticket DIS-412"})
    assert r.status_code == 200
    assert _brand(client, "GL")["untriaged_error"] == 0

    # ---- the next run happens: same defect, brand-new row, brand-new id -------------
    with sessions() as s:
        gl = s.scalar(select(Brand).where(Brand.code == "GL"))
        run2 = mk_run(s, gl, at=T0)
        second = mk_finding(s, run2, fp=FP_GL, severity="error", status="persisting")
        assert second.id != first_id, "run 2 really is a different row"
        s.commit()

    items = client.get("/api/findings", params={"brand": "GL"}).json()["items"]
    assert len(items) == 1, "the list shows the latest run only"
    assert items[0]["id"] != first_id
    assert items[0]["triage_state"] == "acknowledged"
    assert items[0]["triage_note"] == "ticket DIS-412"
    assert _brand(client, "GL")["untriaged_error"] == 0, "the decision still holds"


def test_finding_detail_carries_the_cross_run_history(client, seed):
    """What makes this a defect tracker rather than a list: when it appeared, and whether it
    ever went away. RR's phone defect was seen in both runs."""
    r = client.get(f"/api/findings/{fp_hash(FP_PHONE)}")
    assert r.status_code == 200
    body = r.json()
    assert body["brand"] == "RR"
    assert body["check_label"] == "Phone numbers", "humanised for the reader, not the check id"
    assert len(body["history"]) == 2, "seen in both runs"
    # newest first, so the reader sees the current state at the top
    assert [h["status"] for h in body["history"]] == ["new", "persisting"]
    assert body["triage"]["state"] == "open", "no decision yet is 'open', never null"


def test_triage_update_is_idempotent_and_overwrites(client, seed):
    """Re-triaging must update the one row, not accumulate rows — the unique constraint is on
    (brand, fingerprint), and a second insert would be an outright 500."""
    client.patch(f"/api/triage/{fp_hash(FP_PHONE)}", json={"state": "acknowledged"})
    r = client.patch(f"/api/triage/{fp_hash(FP_PHONE)}",
                     json={"state": "fixed", "note": "deployed"})
    assert r.status_code == 200
    assert r.json()["state"] == "fixed"
    items = client.get("/api/findings", params={"brand": "RR"}).json()["items"]
    assert [i["triage_state"] for i in items if i["fingerprint"] == FP_PHONE] == ["fixed"]


# =========================================================================== 3. filters
def test_filter_by_brand(client, seed):
    body = client.get("/api/findings", params={"brand": "GL"}).json()
    assert body["total"] == 1
    assert {i["brand"] for i in body["items"]} == {"GL"}
    # and unfiltered spans every brand with a completed run
    assert client.get("/api/findings").json()["total"] == 6


def test_brand_filter_is_case_insensitive(client, seed):
    """The UI puts the code straight into the query string; a lowercase link must not 404."""
    assert client.get("/api/findings", params={"brand": "rr"}).json()["total"] == 4


def test_filter_by_check(client, seed):
    body = client.get("/api/findings", params={"brand": "RR", "check": "phone"}).json()
    assert body["total"] == 1
    assert body["items"][0]["check_label"] == "Phone numbers"


def test_filter_by_severity(client, seed):
    assert client.get("/api/findings",
                      params={"brand": "RR", "severity": "error"}).json()["total"] == 2
    assert client.get("/api/findings",
                      params={"brand": "RR", "severity": "warning"}).json()["total"] == 1


def test_filter_by_triage_state(client, seed):
    """`state=open` has to mean "no decision OR explicitly reopened" — a finding nobody has
    touched has no triage row at all, and an outer join leaves it NULL."""
    assert client.get("/api/findings",
                      params={"brand": "RR", "state": "open"}).json()["total"] == 4

    client.patch(f"/api/triage/{fp_hash(FP_LINKS)}", json={"state": "wontfix"})
    assert client.get("/api/findings",
                      params={"brand": "RR", "state": "open"}).json()["total"] == 3
    body = client.get("/api/findings", params={"brand": "RR", "state": "wontfix"}).json()
    assert body["total"] == 1
    assert body["items"][0]["fingerprint"] == FP_LINKS


def test_free_text_search_spans_url_issue_and_snippet(client, seed):
    """Three columns, because the team searches for all three: a page they are fixing, an
    issue they remember the wording of, and a phone number they saw in a snippet."""
    by_url = client.get("/api/findings", params={"q": "/detox"}).json()
    assert [i["fingerprint"] for i in by_url["items"]] == [FP_PHONE]

    by_issue = client.get("/api/findings", params={"q": "dials another brand"}).json()
    assert [i["fingerprint"] for i in by_issue["items"]] == [FP_PHONE]

    by_snippet = client.get("/api/findings", params={"q": "8663309449"}).json()
    assert [i["fingerprint"] for i in by_snippet["items"]] == [FP_GL]

    assert client.get("/api/findings", params={"q": "no-such-string"}).json()["total"] == 0


def test_filters_compose(client, seed):
    """Filters narrow, they do not replace each other."""
    body = client.get("/api/findings",
                      params={"brand": "RR", "severity": "error", "check": "phone"}).json()
    assert body["total"] == 1
    assert client.get("/api/findings",
                      params={"brand": "RR", "severity": "warning",
                              "check": "phone"}).json()["total"] == 0


# =========================================================================== 4. pagination
def test_pagination_total_is_the_whole_set_not_the_page(client, seed):
    """`total` drives "showing 1-2 of 4". Computing it after LIMIT is the classic bug and it
    makes the footer lie on every page."""
    p1 = client.get("/api/findings", params={"brand": "RR", "per_page": 2, "page": 1}).json()
    p2 = client.get("/api/findings", params={"brand": "RR", "per_page": 2, "page": 2}).json()

    assert p1["total"] == p2["total"] == 4
    assert len(p1["items"]) == len(p2["items"]) == 2
    ids1, ids2 = {i["id"] for i in p1["items"]}, {i["id"] for i in p2["items"]}
    assert ids1.isdisjoint(ids2), "page 2 must not repeat page 1"
    assert len(ids1 | ids2) == 4, "and between them they cover the set"

    assert client.get("/api/findings",
                      params={"brand": "RR", "per_page": 2, "page": 3}).json()["items"] == []


def test_per_page_is_capped(client, seed):
    """An uncapped per_page is a way to ask the API to serialise 25,000 findings into one
    response. RR alone has had 16,951 open."""
    assert client.get("/api/findings", params={"per_page": 501}).status_code == 422
    assert client.get("/api/findings", params={"per_page": 500}).status_code == 200


# =========================================================================== 5. sort order
def test_sort_is_severity_then_blast_radius(client, seed):
    """The order the team triages in: errors first, then the widest blast radius.

    The seed is arranged so the two rules disagree — the INFO finding has the largest
    page_count (99) of anything in the run. If page_count were leading the sort it would
    appear first, and the most trivial finding in the database would head the queue.
    """
    items = client.get("/api/findings", params={"brand": "RR"}).json()["items"]
    assert [i["severity"] for i in items] == ["error", "error", "warning", "info"]
    assert [i["page_count"] for i in items] == [12, 3, 50, 99]
    assert items[0]["fingerprint"] == FP_LINKS, "widest-reaching error leads"


# =========================================================================== 6. refusal
def test_a_refused_run_is_reported_distinctly_and_explains_itself(client, seed):
    """`refused` means the brand COULD NOT be audited — host unreachable, no page index. It
    must never render as "audited, found nothing", so it is its own status and it carries the
    error_text that says so in words a non-engineer can act on."""
    runs = client.get("/api/runs", params={"brand": "MHD"}).json()
    assert [r["status"] for r in runs] == ["refused", "ok"], "newest first"

    refused = runs[0]
    assert refused["pages_audited"] == 0
    assert refused["error_text"], "a refusal without an explanation is indistinguishable from a bug"
    assert "NOT been given a clean bill of health" in refused["error_text"]
    assert refused["open_error"] == 0, "it found nothing because it looked at nothing"


def test_a_refused_run_does_not_erase_the_last_known_truth(client, seed):
    """The other half of the same guarantee. MHD's most recent event is a refusal, but the
    findings a human still needs to act on came from the last run that actually completed —
    so the brand must keep reporting them rather than dropping to a reassuring zero."""
    mhd = _brand(client, "MHD")
    assert mhd["open_error"] == 1, "a failed attempt is not a clean site"
    assert client.get("/api/findings", params={"brand": "MHD"}).json()["total"] == 1

    # GAP, asserted as it actually behaves rather than as it should: `_latest_run_ids` filters
    # to status == "ok", so `last_run_status` on a brand row can only ever BE "ok". The brand
    # list therefore cannot show that the most recent attempt was refused — that fact lives
    # only in /api/runs, and the UI has to go there to find it. Worth fixing; not this file's
    # job to pretend it is already fixed.
    assert mhd["last_run_status"] == "ok"
    assert mhd["last_run_at"] == (T0 - timedelta(days=3)).isoformat()


def test_runs_list_carries_the_partial_sample_flag(client, seed):
    """MHD is audited as a labelled PARTIAL SAMPLE, never a full census (~131h at the ~10
    pages/min its host tolerates). A sample presented as a census is a false all-clear."""
    ok_run = [r for r in client.get("/api/runs", params={"brand": "MHD"}).json()
              if r["status"] == "ok"][0]
    assert ok_run["partial_sample"] is True
    rr_run = client.get("/api/runs", params={"brand": "RR"}).json()[0]
    assert rr_run["partial_sample"] is False


def test_runs_new_count_is_the_new_status_only(client, seed):
    """"New this run" on the dashboard means status=new. RR's latest run has exactly one."""
    rr_runs = client.get("/api/runs", params={"brand": "RR"}).json()
    assert rr_runs[0]["new_count"] == 1
    assert rr_runs[0]["open_error"] == 2


# =========================================================================== 7. double-run guard
class _StubTask:
    """Stands in for the Procrastinate task. Records the deferral; defers nothing."""

    def __init__(self, boom: Exception | None = None):
        self.calls: list[dict] = []
        self.boom = boom
        self.configured: dict = {}

    def configure(self, **options):
        """The real Task.configure returns a JobDeferrer, so `.configure(...).defer(...)` chains.
        A stub without this made every trigger 503 the moment the per-brand lock was added."""
        self.configured = options
        return self

    def defer(self, **kwargs):
        self.calls.append(kwargs)
        if self.boom:
            raise self.boom
        return 4242


class _StubQueueApp:
    def open(self):
        return contextlib.nullcontext()


def _stub_queue(monkeypatch, boom: Exception | None = None) -> _StubTask:
    """Replace the real queue. Nothing in this file may enqueue a real audit: a run takes
    HOURS and would hammer a live client origin."""
    from server import jobs
    task = _StubTask(boom)
    monkeypatch.setattr(jobs, "app", _StubQueueApp())
    monkeypatch.setattr(jobs, "audit_brand", task)
    return task


@pytest.mark.parametrize("in_flight", ["queued", "running"])
def test_second_run_request_is_refused_while_one_is_in_flight(client, seed, sessions,
                                                              monkeypatch, in_flight):
    """A second click must not queue a duplicate six-hour crawl of a live client origin.

    409 rather than silent success: the UI needs something honest to say instead of pretending
    it started something that is really just sitting behind the first job.
    """
    task = _stub_queue(monkeypatch)
    with sessions() as s:
        rr = s.scalar(select(Brand).where(Brand.code == "RR"))
        mk_run(s, rr, at=T0, status=in_flight)
        s.commit()

    r = client.post("/api/brands/RR/runs", json={"reason": "impatient"})
    assert r.status_code == 409
    assert "already has a run in progress" in r.json()["detail"]
    assert task.calls == [], "nothing was queued"


def test_queueing_a_run_writes_the_row_before_deferring(client, seed, sessions, monkeypatch):
    """The run row is created by the API, not by the worker.

    There are minutes between deferring a job and a worker picking it up. If the row only
    appeared on pickup, the in-flight guard above would see nothing during that window and a
    second click would queue a duplicate audit — measured: it did exactly that.
    """
    task = _stub_queue(monkeypatch)
    r = client.post("/api/brands/RR/runs", json={"reason": "manual"})
    assert r.status_code == 202, "202: the job runs for hours, so this never blocks"
    body = r.json()
    assert body["queued"] is True and body["brand"] == "RR" and body["job_id"] == 4242
    # max_pages travels with the job. RR has no `default_sample_size`, so it is None — a FULL
    # census, which is what every brand except MHD should get when nobody asks for a cap.
    assert task.calls == [{"brand_code": "RR", "run_id": body["run_id"], "max_pages": None}]

    with sessions() as s:
        assert s.get(Run, body["run_id"]).status == "queued"

    # ...and that queued row is exactly what makes the next click a 409
    assert client.post("/api/brands/RR/runs").status_code == 409


def test_a_run_that_could_not_be_queued_is_not_left_looking_queued(client, seed, sessions,
                                                                   monkeypatch):
    """If the worker is down, the row written a moment ago would otherwise sit in `queued`
    forever, indistinguishable from a run that is genuinely waiting its turn."""
    _stub_queue(monkeypatch, boom=RuntimeError("connection refused"))
    r = client.post("/api/brands/RR/runs")
    assert r.status_code == 503
    assert "is the worker running?" in r.json()["detail"]

    with sessions() as s:
        rr = s.scalar(select(Brand).where(Brand.code == "RR"))
        stuck = s.scalars(select(Run).where(Run.brand_id == rr.id,
                                            Run.status.in_(("queued", "running")))).all()
        assert stuck == [], "no phantom in-flight run is left behind"
        failed = s.scalars(select(Run).where(Run.status == "failed")).all()
        assert len(failed) == 1 and "could not queue" in failed[0].error_text


# =========================================================================== 8. 404s
@pytest.mark.parametrize("path,params", [
    ("/api/findings", {"brand": "NOPE"}),
    ("/api/runs", {"brand": "NOPE"}),
    ("/api/changes", {"brand": "NOPE"}),
    ("/api/pages/new", {"brand": "NOPE"}),
    ("/api/export/sheet", {"brand": "NOPE"}),
])
def test_unknown_brand_is_404_not_an_empty_result(client, seed, path, params):
    """An unknown brand returning `[]` reads as "this brand is clean". It has to be an error:
    an empty result and a typo'd brand code must never look the same."""
    r = client.get(path, params=params) if path != "/api/export/sheet" \
        else client.post(path, params=params)
    assert r.status_code == 404
    assert "unknown brand" in r.json()["detail"]


def test_unknown_brand_on_trigger_is_404(client, seed, monkeypatch):
    _stub_queue(monkeypatch)
    r = client.post("/api/brands/NOPE/runs")
    assert r.status_code == 404


def test_unknown_finding_hash_is_404(client, seed):
    missing = fp_hash("never|observed|anywhere")
    assert client.get(f"/api/findings/{missing}").status_code == 404
    assert client.patch(f"/api/triage/{missing}", json={"state": "fixed"}).status_code == 404


def test_triage_rejects_a_state_that_is_not_a_triage_state(client, seed):
    """The four states mirror what the team already uses. A free-text state would let the UI
    write a value no query filters on, and the finding would vanish from every view."""
    r = client.patch(f"/api/triage/{fp_hash(FP_PHONE)}", json={"state": "maybe-later"})
    assert r.status_code == 422


# =========================================================================== misc guarantees
def test_a_brand_with_no_completed_run_reports_nothing_rather_than_everything(client,
                                                                             sessions):
    """An empty `run_ids` list must short-circuit. Feeding an empty IN() to the findings query
    and letting it match nothing works by luck; a missing WHERE would return every finding for
    every brand instead."""
    with sessions() as s:
        mk_brand(s, "DBH", "District Behavioral Health")
        s.commit()
    body = client.get("/api/findings", params={"brand": "DBH"}).json()
    assert body == {"total": 0, "page": 1, "per_page": 50, "items": []}

    dbh = _brand(client, "DBH")
    assert dbh["last_run_at"] is None and dbh["last_run_status"] is None
    assert dbh["open_error"] == 0


def test_brands_list_reports_scheduling_honestly(client, seed):
    """Nothing schedules a brand with no cron. MHD is unscheduled by decision, and the UI has
    to be able to say so rather than implying it is covered."""
    assert _brand(client, "RR")["scheduled"] is True
    assert _brand(client, "MHD")["scheduled"] is False


def test_changes_view_separates_new_from_resolved(client, seed):
    """"What changed" is the view a human opens first. `rule_changed` belongs in NEITHER list:
    it is a defect whose identity changed because a RULE changed, and reporting it as resolved
    is how a sheet came to claim "171 fixed" when nothing had been fixed."""
    body = client.get("/api/changes", params={"brand": "RR"}).json()
    assert [f["fingerprint_hash"] for f in body["new"]] == [fp_hash(FP_PHONE)]
    assert [f["fingerprint_hash"] for f in body["resolved"]] == [fp_hash(FP_RESOLVED)]
    every = {f["fingerprint_hash"] for f in body["new"] + body["resolved"]}
    assert fp_hash(FP_RULE_CHANGED) not in every


def test_health_reports_real_counts(client, seed):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert (body["brands"], body["runs"], body["findings"]) == (3, 5, 11)
