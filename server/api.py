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

OPEN_STATUSES = ("new", "persisting")

app = FastAPI(title="District Site Auditor", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:5173"],
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


class Paged(BaseModel):
    total: int
    page: int
    per_page: int
    items: list[FindingOut]


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
    return {"ok": True, "brands": session.scalar(select(func.count(Brand.id))),
            "runs": session.scalar(select(func.count(Run.id))),
            "findings": session.scalar(select(func.count(Finding.id)))}


@app.get("/api/brands", response_model=list[BrandOut])
def list_brands(session: Session = Depends(get_session)) -> list[BrandOut]:
    latest = _latest_run_ids(session)
    out: list[BrandOut] = []
    for brand in session.scalars(select(Brand).order_by(Brand.code)):
        run_id = latest.get(brand.id)
        b = BrandOut(code=brand.code, name=brand.name, base_url=brand.base_url,
                     enumeration_mode=brand.enumeration_mode,
                     scheduled=bool(brand.schedule_cron))
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
    if brand:
        b = _brand_or_404(session, brand)
        run_ids = [latest[b.id]] if b.id in latest else []
    else:
        run_ids = list(latest.values())
    if not run_ids:
        return Paged(total=0, page=page, per_page=per_page, items=[])

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
    rows = session.execute(
        stmt.order_by(sev_rank.asc().nullslast(), Finding.page_count.desc(), Finding.id.desc())
        .offset((page - 1) * per_page).limit(per_page)).all()

    items = [FindingOut(
        id=f.id, brand=code, fingerprint=f.fingerprint, url=f.url, check=f.check,
        check_label=CHECK_LABELS.get(f.check, f.check), severity=f.severity, issue=f.issue,
        location=f.location, snippet=f.snippet, suggestion=f.suggestion, status=f.status,
        first_seen=f.first_seen, page_count=f.page_count,
        triage_state=tstate or "open", triage_note=tnote) for f, code, tstate, tnote in rows]
    return Paged(total=total, page=page, per_page=per_page, items=items)


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
    return {
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
            history_written=run.history_written, open_error=errs,
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
class RunRequest(BaseModel):
    reason: str | None = None


@app.post("/api/brands/{code}/runs", status_code=202)
def trigger_run(code: str, _body: RunRequest | None = None,
                user: CurrentUser = Depends(get_current_user),
                session: Session = Depends(get_session)) -> dict:
    """Queue an audit. Returns 202 immediately — the job runs for HOURS, so this never blocks.

    Refuses if the brand already has a run in flight. The queue's per-brand lock enforces this too,
    but returning 409 here gives the UI something honest to say instead of silently queueing a
    second job behind a six-hour one.
    """
    if user.role != "admin":
        raise HTTPException(403, "only an admin can start a run")
    brand = _brand_or_404(session, code)
    in_flight = session.scalar(
        select(func.count(Run.id)).where(Run.brand_id == brand.id,
                                         Run.status.in_(("queued", "running"))))
    if in_flight:
        raise HTTPException(409, f"{brand.code} already has a run in progress")
    # The run row is created HERE, not in the worker. Between deferring a job and a worker picking
    # it up there is a window of minutes; if the row only appeared on pickup, the in-flight check
    # above would see nothing and a second click would queue a duplicate audit of the same brand.
    # Measured: it did exactly that before this change.
    run = Run(brand_id=brand.id, status="queued")
    session.add(run)
    session.commit()
    try:
        from .jobs import app as job_app, audit_brand
        # Procrastinate needs an open pool to defer. Per-request is fine at this volume (a handful
        # of manual triggers a day) and avoids holding a pool inside the API process.
        with job_app.open():
            job_id = audit_brand.defer(brand_code=brand.code, run_id=run.id)
    except Exception as e:                       # noqa: BLE001 — worker/queue may not be up
        run.status, run.error_text = "failed", f"could not queue: {type(e).__name__}: {e}"
        session.commit()
        raise HTTPException(
            503, f"could not queue the run — is the worker running? ({type(e).__name__}: {e})")
    return {"queued": True, "brand": brand.code, "run_id": run.id, "job_id": job_id}


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
