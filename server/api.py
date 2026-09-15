"""FastAPI app — the product API.

Wraps the finished audit engine; does not reimplement any of it. Every endpoint is a query over
the tables the importer fills, except `POST /runs` which enqueues a real `run_audit`.

The one piece of domain logic that lives here rather than in the engine is **what "open" means**.
The diff emits six statuses and only two of them are open defects:

    new, persisting              -> OPEN, a human should look at these
    resolved                     -> the defect went away
    rule_changed                 -> its identity changed because a RULE changed, not the site
    page_unsitemapped, page_removed -> the page was not audited this run; state unknown

Getting this wrong is not academic: counting `rule_changed` as open reported RR at 25,904 when the
truth was 16,951, and counting it as *resolved* made a sheet claim "171 fixed" when nothing had been
fixed. `OPEN_STATUSES` is the single definition, used by every count in this file.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from auditor.humanize import CHECK_LABELS
from .db import get_session
from .models import Brand, Finding, Page, Run, Triage, fp_hash
from .traffic import (NOT_CONNECTED, affected, coverage, load_brand_traffic,
                      ranks_on_impressions, reach, sentence, short, url_key)

OPEN_STATUSES = ("new", "persisting")

app = FastAPI(title="District Site Auditor", version="0.1.0")
# The Vite dev server is the DEFAULT, not the only possibility. Hardcoding it was correct under the
# one topology deploy/README.md describes — Caddy serving web/dist from the same origin as the API,
# where CORS never engages — and silently wrong under any other, where the browser blocks every call
# and the API logs nothing to explain it. Nothing in compose enforces that topology, so make it
# configurable rather than leave a deployment to discover it.
#
# `allow_credentials=True` makes the browser reject a wildcard origin anyway, so the list has to be
# explicit. Comma-separated; blanks dropped so a trailing comma is harmless.
_CORS_ORIGINS = [o.strip() for o in
                 os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware, allow_origins=_CORS_ORIGINS,
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


# --------------------------------------------------------------------------- auth (stubbed)
class CurrentUser(BaseModel):
    email: str
    role: str = "admin"


def get_current_user() -> CurrentUser:
    """STUB. Deploy-time this reads the Cloudflare Access JWT and verifies signature + iss + aud.

    Deliberately a single seam so that swapping in real auth touches this function and nothing
    else — no endpoint signature changes.
    """
    return CurrentUser(email="local@dev", role="admin")


# --------------------------------------------------------------------------- schemas
class BrandOut(BaseModel):
    code: str
    name: str
    base_url: str
    enumeration_mode: str
    scheduled: bool
    # None = this brand's origin can take a full census. A number means it cannot, and every run
    # caps there unless a human overrides it. Only MHD has one today (see `trigger_run`).
    default_sample_size: int | None = None
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    open_error: int = 0
    open_warning: int = 0
    open_info: int = 0
    untriaged_error: int = 0


class FindingOut(BaseModel):
    id: int
    brand: str
    fingerprint: str
    url: str
    check: str
    check_label: str
    severity: str
    issue: str
    location: str | None
    snippet: str | None
    suggestion: str | None
    status: str | None
    first_seen: str | None
    page_count: int
    triage_state: str = "open"
    triage_note: str | None = None
    # Search traffic, only when one brand is selected. `traffic_short` is the table cell,
    # `traffic_note` the full sentence — see server/traffic.py for the five states.
    traffic_state: str | None = None
    traffic_short: str | None = None
    traffic_note: str | None = None


class Paged(BaseModel):
    total: int
    page: int
    per_page: int
    items: list[FindingOut]
    # How this list is ordered, in words. Traffic changes the order, so the screen has to say so.
    ordering_note: str | None = None


class RunOut(BaseModel):
    id: int
    brand: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    pages_audited: int
    enumeration_method: str | None
    partial_sample: bool
    history_written: bool
    # The cap this run actually started with; None = it went for a full census. Reported so
    # "1,204 pages audited" can never be read as "the whole site is clean" when it was a sample.
    max_pages: int | None = None
    # A human asked for this run to stop. On a `running` run this is the only visible sign, because
    # a crawl in flight is not abortable — see `cancel_run`.
    cancel_requested: bool = False
    open_error: int = 0
    new_count: int = 0
    error_text: str | None = None


class TriageIn(BaseModel):
    state: Literal["open", "acknowledged", "wontfix", "fixed"]
    note: str | None = None


# --------------------------------------------------------------------------- helpers
def _latest_run_ids(session: Session) -> dict[int, int]:
    """brand_id -> id of its most recent run, by started_at (NOT by report dir name)."""
    sub = (select(Run.brand_id, func.max(Run.started_at).label("mx"))
           .where(Run.status == "ok").group_by(Run.brand_id).subquery())
    rows = session.execute(
        select(Run.brand_id, Run.id).join(
            sub, and_(Run.brand_id == sub.c.brand_id, Run.started_at == sub.c.mx))
    ).all()
    return {b: r for b, r in rows}


def _brand_or_404(session: Session, code: str) -> Brand:
    brand = session.scalar(select(Brand).where(Brand.code == code.upper()))
    if brand is None:
        raise HTTPException(404, f"unknown brand {code!r}")
    return brand


# --------------------------------------------------------------------------- endpoints
@app.get("/api/health")
def health(session: Session = Depends(get_session)) -> dict:
    # `code_fingerprint` is here so a caller OUTSIDE the container can tell whether the image is
    # running the repo's code. The container cannot see the repo, so it cannot answer that alone —
    # but it can publish what it IS running, and the host can compare. A nine-brand crawl once ran
    # against an image built 3h37m before the code it was meant to run, with nothing warning.
    from auditor.checks_version import code_fingerprint
    return {"ok": True, "brands": session.scalar(select(func.count(Brand.id))),
            "runs": session.scalar(select(func.count(Run.id))),
            "findings": session.scalar(select(func.count(Finding.id))),
            "code_fingerprint": code_fingerprint()}


@app.get("/api/brands", response_model=list[BrandOut])
def list_brands(session: Session = Depends(get_session)) -> list[BrandOut]:
    latest = _latest_run_ids(session)
    out: list[BrandOut] = []
    for brand in session.scalars(select(Brand).order_by(Brand.code)):
        run_id = latest.get(brand.id)
        b = BrandOut(code=brand.code, name=brand.name, base_url=brand.base_url,
                     enumeration_mode=brand.enumeration_mode,
                     scheduled=bool(brand.schedule_cron),
                     default_sample_size=brand.default_sample_size)
        if run_id:
            run = session.get(Run, run_id)
            b.last_run_at, b.last_run_status = run.started_at, run.status
            counts = session.execute(
                select(Finding.severity, func.count(Finding.id))
                .where(Finding.run_id == run_id, Finding.status.in_(OPEN_STATUSES))
                .group_by(Finding.severity)).all()
            for sev, n in counts:
                setattr(b, f"open_{sev}", n)
            # untriaged ERRORs — the number that actually measures the team's workload
            b.untriaged_error = session.scalar(
                select(func.count(Finding.id))
                .outerjoin(Triage, and_(Triage.brand_id == Finding.brand_id,
                                        Triage.fingerprint_hash == Finding.fingerprint_hash))
                .where(Finding.run_id == run_id, Finding.status.in_(OPEN_STATUSES),
                       Finding.severity == "error",
                       or_(Triage.id.is_(None), Triage.state == "open"))) or 0
        out.append(b)
    return out


@app.get("/api/findings", response_model=Paged)
def list_findings(
    brand: str | None = None,
    check: str | None = None,
    severity: str | None = None,
    state: str | None = Query(None, description="triage state"),
    status: str | None = Query(None, description="diff status; default = open only"),
    q: str | None = None,
    page: int = 1,
    per_page: int = Query(50, le=500),
    session: Session = Depends(get_session),
) -> Paged:
    latest = _latest_run_ids(session)
    traffic = None
    if brand:
        b = _brand_or_404(session, brand)
        run_ids = [latest[b.id]] if b.id in latest else []
        traffic = load_brand_traffic(session, b.code)
        ordering_note = NOT_CONNECTED if traffic is None else (
            f"Within each severity, findings on the pages with the most Google search visits come "
            f"first (Search Console, {traffic.label}); problems with how pages appear in search "
            f"rank by impressions instead.")
        # What happens to the findings the data does not cover is said by `coverage()`, appended
        # below with the real share — saying it here as well printed the same sentence twice.
    else:
        run_ids = list(latest.values())
        # Deliberately not ranked across brands: visits to different sites are not comparable, and
        # the two brands with no traffic data would sink to the bottom of every page for it.
        ordering_note = ("Choose one brand to order its findings by search traffic. Across all "
                         "brands the list stays in severity and page-count order.")
    if not run_ids:
        return Paged(total=0, page=page, per_page=per_page, items=[], ordering_note=ordering_note)

    stmt = (select(Finding, Brand.code, Triage.state, Triage.note)
            .join(Brand, Brand.id == Finding.brand_id)
            .outerjoin(Triage, and_(Triage.brand_id == Finding.brand_id,
                                    Triage.fingerprint_hash == Finding.fingerprint_hash))
            .where(Finding.run_id.in_(run_ids)))
    stmt = stmt.where(Finding.status.in_(OPEN_STATUSES) if not status
                      else Finding.status == status)
    if check:
        stmt = stmt.where(Finding.check == check)
    if severity:
        stmt = stmt.where(Finding.severity == severity)
    if state:
        stmt = (stmt.where(or_(Triage.state.is_(None), Triage.state == "open"))
                if state == "open" else stmt.where(Triage.state == state))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Finding.url.ilike(like), Finding.issue.ilike(like),
                              Finding.snippet.ilike(like)))

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    # ERRORs first, then the widest blast radius, then newest — the order the team triages in.
    # A CASE rather than array_position: portable, and it avoids the dialect-specific array cast
    # that produced "no function matches array_position(character varying, character varying)".
    sev_rank = case({"error": 0, "warning": 1, "info": 2}, value=Finding.severity, else_=3)
    if traffic is None:
        rows = session.execute(
            stmt.order_by(sev_rank.asc().nullslast(), Finding.page_count.desc(), Finding.id.desc())
            .offset((page - 1) * per_page).limit(per_page)).all()
    else:
        # Traffic is joined through each finding's page LIST, which SQL cannot sort on, so the
        # order is worked out over light columns for the whole filtered set (one brand: at most a
        # few thousand rows, no `details` blob) and only the requested page is loaded in full.
        rank = {"error": 0, "warning": 1, "info": 2}
        light = session.execute(stmt.with_only_columns(
            Finding.id, Finding.url, Finding.sources, Finding.page_count, Finding.check,
            Finding.severity, Finding.details["class"].as_string())).all()
        # ONE metric for the whole list. The client report can rank search-results problems on
        # impressions because each of its sections holds one kind of problem. This list mixes every
        # kind within a severity, and impressions run ~100x visits: measured on GL, two schema
        # findings with 97 and 473 visits outranked the site-wide phone fault with 34,974 visits
        # purely because their 128,276 impressions were compared against its visits. So impressions
        # only when every row in the filtered list is a search-results problem.
        by_impressions = bool(light) and all(
            ranks_on_impressions(chk, cls) for _i, _u, _s, _p, chk, _sev, cls in light)
        if by_impressions:
            ordering_note = ordering_note.replace(
                "the most Google search visits", "the most impressions in Google's results").replace(
                "; problems with how pages appear in search rank by impressions instead", "")
        else:
            ordering_note = ordering_note.replace(
                "; problems with how pages appear in search rank by impressions instead", "")
        ordered = []
        weighted = 0
        for fid, url, sources, pages, chk, sev, cls in light:
            keys, _complete = affected(url, sources, pages)
            r = reach(keys, pages, traffic, chk, cls)
            weighted += r.state == "weighted"
            # A finding on an address that redirects is a copy of the finding on the page it lands on
            # (the audit follows redirects) and carries the same traffic. The real page wins that tie:
            # the newest-id tie-break alone put 21 COC copies above the original, finding 404738.
            copy = 1 if url_key(url) in traffic.redirects else 0
            ordered.append(((rank.get(sev, 3),) + r.rank(by_impressions)
                            + (-(pages or 1), copy, -fid), fid))
        ordered.sort()
        ordering_note = " ".join(p for p in (ordering_note, coverage(
            weighted, len(light), traffic.capped, noun="findings in this list")) if p)
        page_ids = [fid for _key, fid in ordered[(page - 1) * per_page:page * per_page]]
        position = {fid: i for i, fid in enumerate(page_ids)}
        rows = sorted(session.execute(stmt.where(Finding.id.in_(page_ids))).all(),
                      key=lambda row: position[row[0].id])

    items = []
    for f, code, tstate, tnote in rows:
        out = FindingOut(
            id=f.id, brand=code, fingerprint=f.fingerprint, url=f.url, check=f.check,
            check_label=CHECK_LABELS.get(f.check, f.check), severity=f.severity, issue=f.issue,
            location=f.location, snippet=f.snippet, suggestion=f.suggestion, status=f.status,
            first_seen=f.first_seen, page_count=f.page_count,
            triage_state=tstate or "open", triage_note=tnote)
        if traffic is not None:
            keys, _complete = affected(f.url, f.sources, f.page_count)
            r = reach(keys, f.page_count, traffic, f.check, (f.details or {}).get("class"))
            out.traffic_state, out.traffic_short, out.traffic_note = r.state, short(r), sentence(r)
        items.append(out)
    return Paged(total=total, page=page, per_page=per_page, items=items,
                 ordering_note=ordering_note)


@app.get("/api/findings/{fingerprint_hash}")
def finding_detail(fingerprint_hash: str, session: Session = Depends(get_session)) -> dict:
    """One defect plus its full history — when it appeared, and whether it ever went away.

    This history is what makes the product a defect tracker rather than a list.
    """
    rows = session.execute(
        select(Finding, Run.started_at, Run.id, Brand.code)
        .join(Run, Run.id == Finding.run_id).join(Brand, Brand.id == Finding.brand_id)
        .where(Finding.fingerprint_hash == fingerprint_hash)
        .order_by(Run.started_at.desc())).all()
    if not rows:
        raise HTTPException(404, "unknown finding")
    latest = rows[0][0]
    tri = session.scalar(select(Triage).where(Triage.brand_id == latest.brand_id,
                                              Triage.fingerprint_hash == fingerprint_hash))
    traffic = load_brand_traffic(session, rows[0][3])
    keys, _complete = affected(latest.url, latest.sources, latest.page_count)
    r = reach(keys, latest.page_count, traffic, latest.check, (latest.details or {}).get("class"))
    return {
        "traffic": {"state": r.state,
                    "note": NOT_CONNECTED if r.state == "not_connected" else sentence(r)},
        "fingerprint": latest.fingerprint, "fingerprint_hash": fingerprint_hash,
        "brand": rows[0][3], "url": latest.url, "check": latest.check,
        "check_label": CHECK_LABELS.get(latest.check, latest.check),
        "severity": latest.severity, "issue": latest.issue, "snippet": latest.snippet,
        "suggestion": latest.suggestion, "details": latest.details,
        "page_count": latest.page_count, "sources": latest.sources,
        "triage": {"state": tri.state if tri else "open", "note": tri.note if tri else None},
        "history": [{"run_id": rid, "at": at, "status": f.status} for f, at, rid, _c in rows],
    }


@app.patch("/api/triage/{fingerprint_hash}")
def set_triage(fingerprint_hash: str, body: TriageIn,
               user: CurrentUser = Depends(get_current_user),
               session: Session = Depends(get_session)) -> dict:
    f = session.scalar(select(Finding).where(Finding.fingerprint_hash == fingerprint_hash))
    if f is None:
        raise HTTPException(404, "unknown finding")
    tri = session.scalar(select(Triage).where(Triage.brand_id == f.brand_id,
                                              Triage.fingerprint_hash == fingerprint_hash))
    if tri is None:
        tri = Triage(brand_id=f.brand_id, fingerprint=f.fingerprint,
                     fingerprint_hash=fingerprint_hash)
        session.add(tri)
    tri.state, tri.note, tri.updated_by = body.state, body.note, user.email
    session.commit()
    return {"fingerprint_hash": fingerprint_hash, "state": tri.state, "note": tri.note}


@app.get("/api/runs", response_model=list[RunOut])
def list_runs(brand: str | None = None, limit: int = Query(50, le=200),
              session: Session = Depends(get_session)) -> list[RunOut]:
    stmt = select(Run, Brand.code).join(Brand, Brand.id == Run.brand_id)
    if brand:
        stmt = stmt.where(Brand.id == _brand_or_404(session, brand).id)
    rows = session.execute(stmt.order_by(Run.started_at.desc()).limit(limit)).all()
    out: list[RunOut] = []
    for run, code in rows:
        counts = dict(session.execute(
            select(Finding.status, func.count(Finding.id))
            .where(Finding.run_id == run.id).group_by(Finding.status)).all())
        errs = session.scalar(
            select(func.count(Finding.id)).where(
                Finding.run_id == run.id, Finding.severity == "error",
                Finding.status.in_(OPEN_STATUSES))) or 0
        out.append(RunOut(
            id=run.id, brand=code, started_at=run.started_at, finished_at=run.finished_at,
            status=run.status, pages_audited=run.pages_audited,
            enumeration_method=run.enumeration_method, partial_sample=run.partial_sample,
            history_written=run.history_written, max_pages=run.max_pages,
            cancel_requested=bool(run.cancel_requested), open_error=errs,
            new_count=counts.get("new", 0), error_text=run.error_text))
    return out


@app.get("/api/changes")
def changes(brand: str, session: Session = Depends(get_session)) -> dict:
    """What changed in the latest run: new findings, resolved findings, and new pages."""
    b = _brand_or_404(session, brand)
    latest = _latest_run_ids(session).get(b.id)
    if not latest:
        return {"brand": b.code, "run_id": None, "new": [], "resolved": [], "new_pages": []}

    def rows(status: str) -> list[dict]:
        return [{"fingerprint_hash": f.fingerprint_hash, "url": f.url, "check": f.check,
                 "check_label": CHECK_LABELS.get(f.check, f.check),
                 "severity": f.severity, "issue": f.issue, "page_count": f.page_count}
                for f in session.scalars(
                    select(Finding).where(Finding.run_id == latest, Finding.status == status)
                    .order_by(Finding.severity, Finding.page_count.desc()).limit(200))]

    new_pages = [p.url for p in session.scalars(
        select(Page).where(Page.brand_id == b.id, Page.first_seen_run_id == latest).limit(200))]
    return {"brand": b.code, "run_id": latest, "new": rows("new"),
            "resolved": rows("resolved"), "new_pages": new_pages}


@app.get("/api/checks")
def checks(session: Session = Depends(get_session)) -> list[dict]:
    """Filter options, with the plain-English labels the sheet already uses."""
    rows = session.execute(
        select(Finding.check, func.count(Finding.id))
        .where(Finding.status.in_(OPEN_STATUSES)).group_by(Finding.check)).all()
    return sorted(({"check": c, "label": CHECK_LABELS.get(c, c), "count": n} for c, n in rows),
                  key=lambda r: -r["count"])

# --------------------------------------------------------------------------- actions
# A run in one of these has already stopped, whatever the reason. Nothing can be cancelled.
TERMINAL_RUN_STATUSES = ("ok", "failed", "refused", "cancelled")


class RunRequest(BaseModel):
    reason: str | None = None
    # A page cap for THIS run only. Omit it to get the brand's own default (see `trigger_run`).
    max_pages: int | None = None


@app.post("/api/brands/{code}/runs", status_code=202)
def trigger_run(code: str, body: RunRequest | None = None,
                user: CurrentUser = Depends(get_current_user),
                session: Session = Depends(get_session)) -> dict:
    """Queue an audit. Returns 202 immediately — the job runs for HOURS, so this never blocks.

    Refuses if the brand already has a run in flight. The queue's per-brand lock enforces this too,
    but returning 409 here gives the UI something honest to say instead of silently queueing a
    second job behind a six-hour one.

    **The page cap.** MHD's origin 503s under concurrent load, so its crawl is locked at
    concurrency 2 and moves at ~2.1 pages/min: a full 11,439-page census is ~54h of wall clock,
    which is why MHD has only ever been published as a labelled partial sample. The CLI could
    always bound that (`audit -n 900`); this endpoint could not, so one click on MHD queued all
    11,439 pages and every other brand sat behind it. The cap resolves in one order:

        explicit body.max_pages  ->  brand.default_sample_size  ->  None (full census)

    A capped run is NOT a lesser full run: `run_audit` refuses to move the diff baseline when it
    truncates (`audit.py:750`), and the importer reads that back as `partial_sample`. So the cap
    travels all the way to the UI as "this brand was sampled, not cleared".
    """
    if user.role != "admin":
        raise HTTPException(403, "only an admin can start a run")
    brand = _brand_or_404(session, code)
    requested = body.max_pages if body else None
    if requested is not None and requested <= 0:
        # Validated by hand rather than with Field(gt=0) so the message is a sentence a human can
        # act on. `max_pages: 0` is the plausible mistake — it reads like "no limit" and would
        # otherwise mean "audit nothing", which run_audit would report as a brand with no defects.
        raise HTTPException(
            422, "max_pages must be at least 1 page. Leave it out entirely to audit every page.")
    max_pages = requested if requested is not None else brand.default_sample_size
    in_flight = session.scalar(
        select(func.count(Run.id)).where(Run.brand_id == brand.id,
                                         Run.status.in_(("queued", "running"))))
    if in_flight:
        raise HTTPException(409, f"{brand.code} already has a run in progress")
    # The run row is created HERE, not in the worker. Between deferring a job and a worker picking
    # it up there is a window of minutes; if the row only appeared on pickup, the in-flight check
    # above would see nothing and a second click would queue a duplicate audit of the same brand.
    # Measured: it did exactly that before this change.
    run = Run(brand_id=brand.id, status="queued", max_pages=max_pages)
    session.add(run)
    session.commit()
    try:
        from procrastinate.exceptions import AlreadyEnqueued

        from .jobs import app as job_app, audit_brand
        # Procrastinate needs an open pool to defer. Per-request is fine at this volume (a handful
        # of manual triggers a day) and avoids holding a pool inside the API process.
        #
        # The two locks are the REAL guard; the in-flight check above is only the fast, friendly
        # one. That check is a read-then-write race, and it loses: six simultaneous POSTs were
        # measured producing TWO 202s, two deferred jobs and two `queued` rows — i.e. two concurrent
        # audits of one live client origin, racing on the same resume cache. On MHD's degraded host
        # that is the exact thing that must never happen.
        #   queueing_lock -> the database refuses a second job while one is still WAITING
        #                    (AlreadyEnqueued), which is what makes the guard atomic.
        #   lock          -> two jobs for one brand can never RUN at the same time; the second
        #                    waits. Covers the window after a worker picks the first job up, when
        #                    the queueing lock has already been released.
        # jobs.py's docstring promised both of these from the start. Until now neither was set.
        with job_app.open():
            job_id = (audit_brand
                      .configure(lock=f"brand:{brand.code}",
                                 queueing_lock=f"brand:{brand.code}")
                      .defer(brand_code=brand.code, run_id=run.id, max_pages=max_pages))
    except AlreadyEnqueued:
        # Lost the race by microseconds. Delete the row we just wrote — leaving it would be a
        # phantom `queued` run that never becomes a job and blocks every future trigger.
        session.delete(run)
        session.commit()
        raise HTTPException(409, f"{brand.code} already has a run in progress")
    except Exception as e:                       # noqa: BLE001 — worker/queue may not be up
        run.status, run.error_text = "failed", f"could not queue: {type(e).__name__}: {e}"
        session.commit()
        raise HTTPException(
            503, f"could not queue the run — is the worker running? ({type(e).__name__}: {e})")
    return {"queued": True, "brand": brand.code, "run_id": run.id, "job_id": job_id,
            "max_pages": max_pages}


def _dequeue_waiting_job(run_id: int) -> str:
    """Pull a not-yet-started audit back out of the queue. Returns "" on success, else the reason.

    Best effort on purpose. The run ROW is the product's record of what happened; the queue is an
    implementation detail that may be down. A queue we cannot reach must never stop a human from
    cancelling a run, and it must never turn a cancel click into a 500 — so everything that can go
    wrong here comes back as a sentence we show the user instead.
    """
    from .jobs import app as job_app

    # Same per-request pool as `trigger_run`: opening one for a handful of clicks a day is cheaper
    # than holding a connection pool inside the API process.
    with job_app.open():
        manager = job_app.job_manager
        waiting = [j for j in manager.list_jobs(task="audit_brand", status="todo")
                   if (j.task_kwargs or {}).get("run_id") == run_id]
        if not waiting:
            picked_up = any((j.task_kwargs or {}).get("run_id") == run_id
                            for j in manager.list_jobs(task="audit_brand", status="doing"))
            # No waiting job left. Either a worker claimed it in the seconds since we read the run
            # row, or it was never there. Only the first case can still crawl the site.
            return "a worker had already claimed it" if picked_up else ""
        stubborn = [j.id for j in waiting if not manager.cancel_job_by_id(j.id)]
        return f"the queue would not release job {stubborn}" if stubborn else ""


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: int, user: CurrentUser = Depends(get_current_user),
               session: Session = Depends(get_session)) -> dict:
    """Stop a run a human no longer wants — and say only what is actually true about it.

    The two cases are genuinely different and the API must not blur them:

    * **queued** — nothing has been crawled yet, so cancelling is real. The job comes out of the
      queue and the run ends as `cancelled`, which is neither a failure nor a clean result.
    * **running** — the crawl DOES NOT STOP. `run_audit` has no cancellation point; it fetches
      until it is done or the worker dies. All we can do is record the request, so that when the
      worker does stop, `reconcile_orphaned_runs` writes `cancelled` ("a human stopped it") rather
      than `failed` ("our bug"). Claiming the crawl stopped would be the worst kind of lie this
      product can tell: the client's site keeps getting hit while the UI says it stopped.

    `took_effect` is that distinction in one boolean, so the UI never has to parse prose.
    """
    if user.role != "admin":
        raise HTTPException(403, "only an admin can cancel a run")
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, f"unknown run {run_id}")
    brand = session.get(Brand, run.brand_id)
    code = brand.code if brand else "this brand"

    if run.status in TERMINAL_RUN_STATUSES:
        raise HTTPException(
            409, f"run {run_id} has already finished (status: {run.status}), so there is nothing "
                 f"to cancel")

    problem = ""
    if run.status == "queued":
        try:
            problem = _dequeue_waiting_job(run_id)
        except Exception as e:                   # noqa: BLE001 — the queue may be down entirely
            problem = f"the job queue could not be reached ({type(e).__name__}: {e})"
        # Re-read the row before deciding which of the two answers to give. A worker can claim the
        # job while we are talking to the queue and flip this row to `running` in its own session;
        # our read above would then be stale, and writing `cancelled` + finished_at over it would
        # put "stopped, nothing was crawled" on the screen while the crawl is still fetching the
        # client's site. That is the one lie this endpoint exists to avoid, so the branch is chosen
        # on the CURRENT status, not the one we happened to read first.
        session.refresh(run)
        if run.status in TERMINAL_RUN_STATUSES:
            raise HTTPException(
                409, f"run {run_id} finished while the cancel was being processed (status: "
                     f"{run.status}), so there is nothing to cancel")

    if run.status == "queued":
        detail = (f"{code}'s run was still waiting in the queue and never started, so no pages "
                  f"were crawled and nothing was audited. It is recorded as cancelled.")
        error_text = ("a human cancelled this run before it started. No pages were crawled, so "
                      "nothing was audited and the previous run's results are unchanged.")
        if problem:
            # Do not paper over this: the job may still be sitting there, and if a worker takes it
            # the worker ADOPTS this same row and flips it back to `running`. Say so.
            tail = (f" It could not be taken out of the job queue ({problem}), so a worker may "
                    f"still start it; if that happens it runs as a normal audit.")
            detail += tail
            error_text += tail
        run.status = "cancelled"
        run.cancel_requested = True   # a worker that starts it anyway then ends as cancelled, not failed
        run.finished_at = datetime.now(timezone.utc)
        run.error_text = error_text
        session.commit()
        return {"cancelled": True, "took_effect": True, "run_id": run.id, "brand": code,
                "status": run.status, "detail": detail}

    # running — either it already was when the request arrived, or a worker claimed it while we
    # were reaching into the queue above. Both mean the same thing to the person clicking: pages
    # are being fetched right now and this button does not stop that.
    run.cancel_requested = True
    session.commit()
    return {
        "cancelled": True, "took_effect": False, "run_id": run.id, "brand": code,
        "status": run.status,
        "detail": (f"{code} is already being crawled and this does NOT stop it — an audit in "
                   f"flight has no mid-crawl abort, so it keeps fetching until it finishes or the "
                   f"worker is stopped. Your request is recorded: if the worker stops before this "
                   f"run finishes, the run is recorded as cancelled (a human stopped it, the "
                   f"brand was not fully audited) instead of failed. If it finishes first, it "
                   f"finishes normally and its results are recorded."),
    }


@app.post("/api/export/sheet")
def export_sheet(brand: str, dry_run: bool = True,
                 user: CurrentUser = Depends(get_current_user),
                 session: Session = Depends(get_session)) -> dict:
    """Push the current open set to the Google Sheet.

    The sheet stays as an EXPORT, not the interface — the team already has workflows built on it
    and there is no reason to force them off. Defaults to dry_run so a mis-click cannot overwrite
    a client-facing tab.
    """
    b = _brand_or_404(session, brand)
    latest = _latest_run_ids(session).get(b.id)
    if not latest:
        raise HTTPException(404, f"{b.code} has no completed run to export")
    run = session.get(Run, latest)
    if not run.report_dir:
        raise HTTPException(
            409, f"{b.code}'s latest run has no report on disk to publish "
                 f"(it was recorded via the API, not a file-based run)")
    try:
        from .export import export_brand
        line = export_brand(b.code, run.report_dir, dry_run=dry_run)
    except FileNotFoundError as e:
        raise HTTPException(409, f"the report for that run is no longer on disk: {e}")
    except Exception as e:                       # noqa: BLE001 — creds/sheet may be absent locally
        raise HTTPException(503, f"sheet export failed: {type(e).__name__}: {e}")
    return {"brand": b.code, "dry_run": dry_run, "result": line}


@app.get("/api/pages/new")
def new_pages(brand: str, session: Session = Depends(get_session)) -> dict:
    """Pages first seen in the latest run.

    A brand-new page carrying a broken CTA is worse than an old one, so this is a first-class view
    rather than a filter buried in the findings list.
    """
    b = _brand_or_404(session, brand)
    latest = _latest_run_ids(session).get(b.id)
    if not latest:
        return {"brand": b.code, "run_id": None, "pages": []}
    urls = [p.url for p in session.scalars(
        select(Page).where(Page.brand_id == b.id, Page.first_seen_run_id == latest).limit(500))]
    return {"brand": b.code, "run_id": latest, "count": len(urls), "pages": urls}
