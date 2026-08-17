"""Background jobs — wrapping `run_audit`, and the email digest.

Queue is **Procrastinate on the same Postgres the API uses**. No Redis: for a single-tenant tool
with one worker, a second datastore is an extra thing to run, back up and lose.

Two requirements the queue has to satisfy, both learned from operating the CLI:

* **A per-brand lock.** Two concurrent audits of one brand would double-crawl a client origin and
  race on the resume cache. `lock="brand:<code>"` guarantees the second waits.
* **A run row written BEFORE the job starts.** A crash must leave a visible `running`/`failed` row,
  not silence. The current CLI's failure mode is that a dead run looks like a run that never
  happened.

`EmptyAuditRefused` is caught and recorded as **`refused`**, never retried and never `failed`: it
means the brand could not be audited (host down, no enumeration), which is a correct refusal by the
publish guard, not an infrastructure error. Retrying it would hammer an origin that is already
struggling — exactly what MHD's degraded host must never receive.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from procrastinate import App, PsycopgConnector
from sqlalchemy import select

from .db import SessionLocal
from pathlib import Path

from .models import Brand, Finding, Run

PROCRASTINATE_DSN = os.getenv(
    "PROCRASTINATE_DSN", "postgresql://district:district@127.0.0.1:55432/district")

app = App(connector=PsycopgConnector(conninfo=PROCRASTINATE_DSN))

OPEN_STATUSES = ("new", "persisting")


def _report_dir(result: dict) -> Path | None:
    """Where run_audit wrote this run's report.

    Read from `result["run"]["out_dir"]` — verified against auditor/audit.py's actual return value
    rather than assumed. An earlier version of this file read `result["summary"]`, a key that does
    not exist, and silently defaulted every piece of run metadata.
    """
    run_blob = result.get("run") or {}
    out = run_blob.get("out_dir")
    return Path(out) if out else None


@app.task(name="audit_brand", queue="audits", pass_context=True)
def audit_brand(context, brand_code: str, run_id: int | None = None) -> dict:
    """Run one brand's audit. Hours long by design — the worker must not be time-limited.

    `run_id` is the row the API already created when it queued this job. The worker ADOPTS it
    rather than inserting another, so a queued run is visible in the UI the moment it is queued
    and the API's "already running?" guard has something to see.
    """
    from auditor.audit import run_audit
    from auditor.config import load_brand
    from auditor.publish import EmptyAuditRefused

    with SessionLocal() as session:
        brand = session.scalar(select(Brand).where(Brand.code == brand_code.upper()))
        if brand is None:
            raise ValueError(f"unknown brand {brand_code!r}")
        # written BEFORE the work starts, so a crash is visible rather than absent
        run = session.get(Run, run_id) if run_id else None
        if run is None:                       # scheduled/CLI trigger with no pre-made row
            run = Run(brand_id=brand.id)
            session.add(run)
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        session.commit()
        run_id = run.id

    try:
        cfg = load_brand(brand_code.lower())
        result = asyncio.run(run_audit(cfg, resume=True))
    except EmptyAuditRefused as e:
        with SessionLocal() as session:
            r = session.get(Run, run_id)
            r.status, r.error_text = "refused", str(e)
            r.finished_at = datetime.now(timezone.utc)
            session.commit()
        return {"run_id": run_id, "status": "refused"}
    except Exception as e:                       # noqa: BLE001 — must record, then re-raise
        with SessionLocal() as session:
            r = session.get(Run, run_id)
            r.status, r.error_text = "failed", f"{type(e).__name__}: {e}"
            r.finished_at = datetime.now(timezone.utc)
            session.commit()
        raise

    with SessionLocal() as session:
        from .importer import load_report_into_run
        brand = session.scalar(select(Brand).where(Brand.code == brand_code.upper()))
        r = session.get(Run, run_id)
        r.status = "ok"
        r.finished_at = datetime.now(timezone.utc)
        d = _report_dir(result)
        if d is None or not d.is_dir():
            r.status, r.error_text = "failed", "the audit finished but wrote no report directory"
            session.commit()
            raise RuntimeError(r.error_text)
        # Load from the REPORT ON DISK via the same function the backfill importer uses, so a run
        # made through the product is byte-for-byte the same shape as an imported one — including
        # the resolved tail, which the previous in-memory path silently dropped.
        n = load_report_into_run(session, brand, r, d)
        session.commit()

    digest_for_run.defer(run_id=run_id)
    return {"run_id": run_id, "status": "ok", "findings": n}


@app.task(name="digest_for_run", queue="mail")
def digest_for_run(run_id: int) -> dict:
    """Email digest — sent ONLY when a run found new ERRORs or failed.

    Silence otherwise, deliberately: a digest that arrives every night regardless is a digest
    nobody opens, and this system's whole value proposition is not crying wolf.
    """
    from .mail import render_digest, send

    with SessionLocal() as session:
        run = session.get(Run, run_id)
        if run is None:
            return {"sent": False, "reason": "unknown run"}
        brand = session.get(Brand, run.brand_id)
        new_errors = list(session.scalars(
            select(Finding).where(Finding.run_id == run_id, Finding.status == "new",
                                  Finding.severity == "error")
            .order_by(Finding.page_count.desc()).limit(20)))
        if run.status in ("ok",) and not new_errors:
            return {"sent": False, "reason": "nothing new worth an email"}
        subject, body = render_digest(brand, run, new_errors)
        sent = send(subject, body)
        return {"sent": sent, "subject": subject, "new_errors": len(new_errors)}
