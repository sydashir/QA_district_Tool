"""Page caps and run cancellation — the two things the product could not do that the CLI could.

The incident behind this file: MHD's origin 503s under concurrent requests, so its crawl is locked
at concurrency 2 and moves at ~2.1 pages/min. A full 11,439-page census is ~54h of wall clock,
which is why MHD has only ever been *published* as a labelled PARTIAL SAMPLE. The CLI could always
bound that with `audit -n 900`. `POST /api/brands/{code}/runs` accepted no cap at all, so one click
queued the whole census and parked every other brand behind it — RR, the brand the acceptance run
actually needed, sat in `queued` for two days.

Two judgements are under test here, and both are the kind a human later reads as fact:

* **A capped run must never read as a full one.** `max_pages` is recorded per-run, so a row cannot
  later claim a scope it never had.
* **A cancelled run is neither a failure nor a result.** `failed` sends someone hunting a bug that
  does not exist; `ok` would say a part-crawled site is clean. It is the third thing.

Same harness as `test_server_api.py`: throwaway in-memory SQLite, `get_session` overridden, and the
queue stubbed. Nothing here may touch the live Postgres or defer a real audit.
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
from server.models import Base, Brand, Run

T0 = datetime(2026, 8, 19, 2, 0, 0)

# The real value `ensure_brands` gives MHD. Named once so a change to the policy breaks these tests
# loudly instead of leaving them asserting a number nobody maintains.
MHD_CAP = 900


# --------------------------------------------------------------------------- fixtures
@pytest.fixture()
def sessions():
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


@pytest.fixture()
def brands(sessions):
    """MHD carries a cap; RR does not. That contrast is the whole point of the resolution rule."""
    with sessions() as s:
        s.add(Brand(code="MHD", name="Inpatient Mental Health Finder",
                    base_url="https://inpatientmentalhealthfinder.com",
                    enabled=True, schedule_cron=None, default_sample_size=MHD_CAP))
        s.add(Brand(code="RR", name="Renaissance Recovery",
                    base_url="https://www.renaissancerecovery.com",
                    enabled=True, schedule_cron="0 2 * * *", default_sample_size=None))
        s.commit()


class _StubTask:
    """Stands in for the Procrastinate task. Records the deferral; defers nothing.

    Mirrors the real `defer` including its return of a job id, because the endpoint puts that id in
    its response and a stub that returned None would let a broken response pass.
    """

    def __init__(self, boom: Exception | None = None):
        self.calls: list[dict] = []
        self.boom = boom
        self.configured: dict = {}

    def configure(self, **options):
        """The real `Task.configure` returns a JobDeferrer so `.configure(...).defer(...)` chains."""
        self.configured = options
        return self

    def defer(self, **kwargs):
        self.calls.append(kwargs)
        if self.boom:
            raise self.boom
        return 4242


class _StubQueueApp:
    def __init__(self, manager: object | None = None):
        self.job_manager = manager

    def open(self):
        return contextlib.nullcontext()


class _StubJobManager:
    """A queue that can be inspected and cancelled — and can also refuse, like the real one.

    A test double must mirror the real thing including its degenerate states; this repo has already
    been burned once by a fake that was kinder than reality. So `stubborn` reproduces
    `cancel_job_by_id` returning False, which is exactly the case the endpoint has to admit to the
    user instead of silently claiming the run was pulled from the queue.
    """

    def __init__(self, jobs: list | None = None, stubborn: bool = False):
        self._jobs = jobs or []
        self.stubborn = stubborn
        self.cancelled: list[int] = []

    def list_jobs(self, task=None, status=None, **kw):
        return [j for j in self._jobs if j.status == status]

    def cancel_job_by_id(self, job_id: int, **kw) -> bool:
        if self.stubborn:
            return False
        self.cancelled.append(job_id)
        self._jobs = [j for j in self._jobs if j.id != job_id]
        return True


class _StubJob:
    def __init__(self, job_id: int, run_id: int, status: str = "todo"):
        self.id = job_id
        self.status = status
        # The real procrastinate Job exposes the deferred kwargs under exactly this name.
        self.task_kwargs = {"run_id": run_id}


def _stub_queue(monkeypatch, boom=None, manager=None) -> _StubTask:
    from server import jobs
    task = _StubTask(boom)
    monkeypatch.setattr(jobs, "app", _StubQueueApp(manager))
    monkeypatch.setattr(jobs, "audit_brand", task)
    return task


def _mk_run(sessions, code: str, status: str, **kw) -> int:
    with sessions() as s:
        brand = s.scalar(select(Brand).where(Brand.code == code))
        run = Run(brand_id=brand.id, started_at=T0, status=status, **kw)
        s.add(run)
        s.commit()
        return run.id


# =========================================================================== cap resolution
def test_a_brand_with_a_cap_is_sampled_without_anyone_asking(client, brands, sessions, monkeypatch):
    """The whole bug in one test.

    Before this, a plain MHD trigger meant all 11,439 pages. The brand's own `default_sample_size`
    now bounds it, so the default behaviour is the SAFE one — nobody has to remember the incident
    to avoid repeating it.
    """
    task = _stub_queue(monkeypatch)
    r = client.post("/api/brands/MHD/runs", json={})
    assert r.status_code == 202
    assert r.json()["max_pages"] == MHD_CAP
    assert task.calls == [{"brand_code": "MHD", "run_id": r.json()["run_id"],
                           "max_pages": MHD_CAP}]


def test_a_brand_without_a_cap_runs_the_whole_site(client, brands, monkeypatch):
    """None is not a missing value here — it is the positive statement "audit everything"."""
    task = _stub_queue(monkeypatch)
    r = client.post("/api/brands/RR/runs", json={})
    assert r.status_code == 202
    assert r.json()["max_pages"] is None
    assert task.calls[0]["max_pages"] is None


def test_an_explicit_cap_beats_the_brand_default(client, brands, monkeypatch):
    task = _stub_queue(monkeypatch)
    r = client.post("/api/brands/MHD/runs", json={"max_pages": 50})
    assert r.json()["max_pages"] == 50, "the human asking overrides the brand policy"
    assert task.calls[0]["max_pages"] == 50


def test_an_explicit_cap_can_be_set_on_a_brand_that_has_none(client, brands, monkeypatch):
    task = _stub_queue(monkeypatch)
    r = client.post("/api/brands/RR/runs", json={"max_pages": 40})
    assert r.json()["max_pages"] == 40
    assert task.calls[0]["max_pages"] == 40


@pytest.mark.parametrize("bad", [0, -1, -900])
def test_a_nonsense_cap_is_refused_before_anything_is_queued(client, brands, sessions,
                                                             monkeypatch, bad):
    """`max_pages: 0` is the plausible mistake — it reads like "no limit".

    Taken literally it means audit nothing, which `run_audit` would finish instantly and report as a
    brand with no defects: a clean bill of health for a site nobody looked at. That is the single
    worst output this product can produce, so it is rejected at the door.
    """
    task = _stub_queue(monkeypatch)
    r = client.post("/api/brands/MHD/runs", json={"max_pages": bad})
    assert r.status_code == 422
    assert "at least 1 page" in r.json()["detail"]
    assert task.calls == [], "nothing was queued"
    with sessions() as s:
        assert s.scalars(select(Run)).all() == [], "and no run row was left behind"


def test_the_cap_is_recorded_on_the_run_itself(client, brands, sessions, monkeypatch):
    """Recorded per-run, not read back off the brand.

    The brand's default can change later. If a historical row resolved its scope through the brand,
    a run that sampled 900 pages would silently start claiming whatever the policy says today.
    """
    _stub_queue(monkeypatch)
    run_id = client.post("/api/brands/MHD/runs", json={}).json()["run_id"]
    with sessions() as s:
        assert s.get(Run, run_id).max_pages == MHD_CAP
        # change the policy afterwards; the run must not follow it
        s.scalar(select(Brand).where(Brand.code == "MHD")).default_sample_size = 5
        s.commit()
    with sessions() as s:
        assert s.get(Run, run_id).max_pages == MHD_CAP


def test_the_cap_is_visible_to_anyone_reading_the_run_list(client, brands, sessions, monkeypatch):
    """`pages_audited` alone cannot distinguish "we chose to stop" from "we only got that far"."""
    _stub_queue(monkeypatch)
    run_id = client.post("/api/brands/MHD/runs", json={}).json()["run_id"]
    with sessions() as s:
        run = s.get(Run, run_id)
        run.status, run.pages_audited, run.partial_sample = "ok", MHD_CAP, True
        s.commit()
    row = client.get("/api/runs", params={"brand": "MHD"}).json()[0]
    assert row["max_pages"] == MHD_CAP and row["partial_sample"] is True


def test_the_brand_list_says_which_brands_are_sampled_by_policy(client, brands):
    rows = {b["code"]: b for b in client.get("/api/brands").json()}
    assert rows["MHD"]["default_sample_size"] == MHD_CAP
    assert rows["RR"]["default_sample_size"] is None


# =========================================================================== the double-run race
class _LockingStubTask:
    """A queue stub that enforces `queueing_lock` the way Postgres does.

    The real guarantee is a partial unique index on procrastinate's own table: a second job with a
    live queueing_lock raises `AlreadyEnqueued`. A stub that just accepted every defer would be
    kinder than reality and would let the very bug this test exists for pass — the fake Sheets
    client already taught this lesson once.
    """

    def __init__(self):
        self.calls: list[dict] = []
        self._held: set[str] = set()
        self._pending: str | None = None

    def configure(self, lock=None, queueing_lock=None):
        self._pending = queueing_lock
        return self

    def defer(self, **kwargs):
        from procrastinate.exceptions import AlreadyEnqueued
        key = self._pending
        if key is not None and key in self._held:
            raise AlreadyEnqueued(f"already enqueued: {key}")
        if key is not None:
            self._held.add(key)
        self.calls.append(kwargs)
        return 1000 + len(self.calls)


def test_losing_the_queue_race_is_a_409_and_leaves_no_phantom_run(client, brands, sessions,
                                                                  monkeypatch):
    """The case the in-flight SELECT cannot catch, and what must happen when it doesn't.

    Measured before the fix: 6 concurrent POSTs produced 2x202, two deferred jobs and two `queued`
    rows — two audits of one live client origin, racing on a single resume cache. The guard is a
    read-then-write race and cannot fix itself, so the database has to refuse.

    Driven deterministically (the queue stub already holds the brand's queueing lock) rather than
    with threads: the race is real, but reproducing it through SQLite's single-writer model is
    flaky, and a flaky test for a correctness guarantee is worse than none.
    """
    from server import jobs

    task = _LockingStubTask()
    task._held.add("brand:RR")          # a job for RR is already waiting in the queue
    monkeypatch.setattr(jobs, "app", _StubQueueApp())
    monkeypatch.setattr(jobs, "audit_brand", task)

    r = client.post("/api/brands/RR/runs", json={})
    assert r.status_code == 409
    assert "already has a run in progress" in r.json()["detail"]
    assert task.calls == [], "nothing was queued"

    with sessions() as s:
        assert s.scalars(select(Run)).all() == [], (
            "the losing request must not leave a phantom queued row — it would never become a job "
            "and would block every future trigger for this brand")


def test_the_brand_lock_is_actually_set(client, brands, monkeypatch):
    """Guards the regression directly: jobs.py's docstring promised a per-brand lock from the
    start, and for months neither `lock` nor `queueing_lock` was ever passed."""
    from server import jobs

    seen = {}

    class _Recorder(_StubTask):
        def configure(self, lock=None, queueing_lock=None):
            seen["lock"], seen["queueing_lock"] = lock, queueing_lock
            return self

    task = _Recorder()
    monkeypatch.setattr(jobs, "app", _StubQueueApp())
    monkeypatch.setattr(jobs, "audit_brand", task)

    assert client.post("/api/brands/RR/runs", json={}).status_code == 202
    assert seen == {"lock": "brand:RR", "queueing_lock": "brand:RR"}


# =========================================================================== cancellation
def test_cancelling_a_queued_run_really_stops_it(client, brands, sessions, monkeypatch):
    """Nothing has been crawled yet, so this cancel is real and may say so."""
    manager = _StubJobManager(jobs=[_StubJob(7, run_id=1)])
    _stub_queue(monkeypatch, manager=manager)
    run_id = _mk_run(sessions, "MHD", "queued")
    manager._jobs = [_StubJob(7, run_id=run_id)]

    r = client.post(f"/api/runs/{run_id}/cancel")
    assert r.status_code == 200
    body = r.json()
    assert body["cancelled"] is True and body["took_effect"] is True
    assert manager.cancelled == [7], "the job was pulled out of the queue"
    with sessions() as s:
        run = s.get(Run, run_id)
        assert run.status == "cancelled"
        assert run.finished_at is not None
        assert "no pages were crawled" in (run.error_text or "").lower()


def test_cancelling_a_running_run_does_not_claim_it_stopped(client, brands, sessions, monkeypatch):
    """The crawl DOES NOT STOP, and the response must not pretend otherwise.

    `run_audit` has no cancellation point; adding one would mean editing `auditor/`, which
    invalidates every brand's resume cache. So a running cancel is a REQUEST, and the honest
    signal is `took_effect: False` plus a status that still says running. Someone told "stopped"
    who then watches the elapsed timer keep climbing stops believing the rest of the product.
    """
    _stub_queue(monkeypatch, manager=_StubJobManager())
    run_id = _mk_run(sessions, "MHD", "running")

    r = client.post(f"/api/runs/{run_id}/cancel")
    assert r.status_code == 200
    body = r.json()
    assert body["cancelled"] is True
    assert body["took_effect"] is False, "it has NOT stopped"
    assert body["status"] == "running", "and it still says so"
    assert "does not stop it" in body["detail"].lower()
    with sessions() as s:
        run = s.get(Run, run_id)
        assert run.cancel_requested is True
        assert run.status == "running", "the request is recorded; the status is untouched"
        assert run.finished_at is None, "an unfinished run has no finish time"


def test_a_queued_job_the_queue_will_not_release_is_admitted_to(client, brands, sessions,
                                                                monkeypatch):
    """The job may still be sitting there, and a worker that takes it flips this row back to
    `running`. Saying "cancelled" and stopping there would be a lie with a live crawl behind it."""
    manager = _StubJobManager(stubborn=True)
    _stub_queue(monkeypatch, manager=manager)
    run_id = _mk_run(sessions, "MHD", "queued")
    manager._jobs = [_StubJob(9, run_id=run_id)]

    body = client.post(f"/api/runs/{run_id}/cancel").json()
    assert "could not be taken out of the job queue" in body["detail"]
    assert "may" in body["detail"] and "still start it" in body["detail"]


def test_a_queue_that_is_down_cannot_block_a_cancel(client, brands, sessions, monkeypatch):
    """The run ROW is the product's record; the queue is an implementation detail that may be down.
    An unreachable queue must not turn a cancel click into a 500."""
    from server import jobs

    class _Exploding(_StubQueueApp):
        def open(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(jobs, "app", _Exploding())
    run_id = _mk_run(sessions, "MHD", "queued")

    r = client.post(f"/api/runs/{run_id}/cancel")
    assert r.status_code == 200
    assert "could not be reached" in r.json()["detail"]
    with sessions() as s:
        assert s.get(Run, run_id).status == "cancelled"


@pytest.mark.parametrize("status", ["ok", "failed", "refused", "cancelled"])
def test_cancelling_a_finished_run_is_a_409(client, brands, sessions, monkeypatch, status):
    _stub_queue(monkeypatch, manager=_StubJobManager())
    run_id = _mk_run(sessions, "MHD", status)
    r = client.post(f"/api/runs/{run_id}/cancel")
    assert r.status_code == 409
    assert "already finished" in r.json()["detail"]


def test_cancelling_an_unknown_run_is_a_404(client, brands, monkeypatch):
    _stub_queue(monkeypatch, manager=_StubJobManager())
    assert client.post("/api/runs/424242/cancel").status_code == 404


# =========================================================================== reconciliation
def test_a_deliberate_stop_and_a_crash_do_not_collapse_into_one_status(sessions, brands):
    """Both are settled by the same code path, and they must end differently.

    `failed` sends someone hunting a bug. For a run a human stopped on purpose there is no bug, and
    a startup log reading "2 failed" would send exactly that person looking. But neither is a
    result: both left the brand part-checked, and both have to say so.

    Tested against `settle_abandoned_run` rather than `reconcile_orphaned_runs` because the finding
    half is a Postgres-only query over procrastinate's own tables; this is the judgement, which is
    the part that was wrong before and the part a human reads.
    """
    from server.jobs import settle_abandoned_run

    stopped_id = _mk_run(sessions, "MHD", "running", cancel_requested=True)
    crashed_id = _mk_run(sessions, "RR", "running", cancel_requested=False)

    with sessions() as s:
        stopped, crashed = s.get(Run, stopped_id), s.get(Run, crashed_id)
        assert settle_abandoned_run(stopped) == "cancelled"
        assert settle_abandoned_run(crashed) == "failed"
        s.commit()

    with sessions() as s:
        stopped, crashed = s.get(Run, stopped_id), s.get(Run, crashed_id)
        assert (stopped.status, crashed.status) == ("cancelled", "failed")
        assert stopped.finished_at is not None and crashed.finished_at is not None
        # Neither may read as a clean result.
        assert "not fully audited" in (stopped.error_text or "").lower()
        assert "did not finish" in (crashed.error_text or "").lower()
        # ...and the deliberate one must not send anyone bug-hunting.
        assert "crash" not in (stopped.error_text or "").lower()


@pytest.mark.parametrize("status", ["ok", "failed", "refused", "cancelled"])
def test_a_run_that_already_ended_is_never_re_settled(sessions, brands, status):
    """A finished run's recorded ending is the record. Re-stamping it on the next worker start
    would rewrite history — and would overwrite a real `refused` with a generic `failed`."""
    from server.jobs import settle_abandoned_run

    run_id = _mk_run(sessions, "MHD", status, error_text="original reason")
    with sessions() as s:
        run = s.get(Run, run_id)
        assert settle_abandoned_run(run) is None
        assert run.status == status and run.error_text == "original reason"


def test_settling_nothing_is_not_an_error(brands):
    """`session.get` returns None for a job whose run row is gone; that must not raise on startup."""
    from server.jobs import settle_abandoned_run

    assert settle_abandoned_run(None) is None


def test_the_digest_never_reports_a_stopped_run_as_a_finished_one(sessions, brands):
    """`render_digest` branches on every degraded status. `cancelled` was the one that fell
    through to the "N new problems found" wording, which would present a part-crawled site's
    findings as a completed audit."""
    from server.mail import render_digest

    run_id = _mk_run(sessions, "MHD", "cancelled", error_text="a person stopped it")
    with sessions() as s:
        run = s.get(Run, run_id)
        brand = s.scalar(select(Brand).where(Brand.code == "MHD"))
        subject, body = render_digest(brand, run, new_errors=[])

    assert "stopped before finishing" in subject
    assert "new problem" not in subject, "it did not finish, so it found nothing conclusive"
    assert "only partly checked" in body
    assert "previous results are unchanged" in body


# =========================================================================== brand policy
def test_only_mhd_is_capped_by_policy():
    """Read from the real config rather than asserted against a hand-written list, so a new brand
    with a degraded origin cannot be silently added without this test noticing."""
    from server.importer import ensure_brands

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        made = ensure_brands(s)
        s.commit()
        capped = {c: b.default_sample_size for c, b in made.items()
                  if b.default_sample_size is not None}
    engine.dispose()

    assert capped == {"MHD": MHD_CAP}, (
        "MHD is the only brand whose origin cannot take a full census. If another brand needs a "
        "cap, that is a real policy decision and belongs in the same place, not in a caller.")
