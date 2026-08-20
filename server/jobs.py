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

**`cancelled`** is the third non-`failed` ending, and it exists for the same reason in the other
direction: a human deliberately stopped the run. `failed` means chase the bug; there is no bug to
chase here, so a cancelled run must not page anyone. But it is not a result either — the crawl was
cut off partway, so the brand was NOT fully audited and the wording says so. A cancel never aborts
a crawl mid-flight: the API only sets `cancel_requested`, and the run is recorded as `cancelled`
when the worker next stops (see `reconcile_orphaned_runs`).
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
def audit_brand(context, brand_code: str, run_id: int | None = None,
                max_pages: int | None = None) -> dict:
    """Run one brand's audit. Hours long by design — the worker must not be time-limited.

    `run_id` is the row the API already created when it queued this job. The worker ADOPTS it
    rather than inserting another, so a queued run is visible in the UI the moment it is queued
    and the API's "already running?" guard has something to see.

    `max_pages` is the cap the API already resolved (explicit request, else the brand's
    `default_sample_size`, else None for a full census). None means audit everything. It is handed
    straight to `run_audit(limit=...)`, the same parameter the CLI's `audit -n N` uses — the
    capability existed all along and only the product could not reach it, which is how MHD queued
    all 11,439 of its pages and blocked every other brand behind a ~54h crawl.

    Do NOT infer `partial_sample` here. `run_audit` refuses to move the diff baseline when the cap
    actually truncated the URL set, records that in summary.json, and `load_report_into_run` reads
    it back. Setting it by hand would lie in the one case that matters: a cap of 900 on a brand
    that only has 300 pages is a FULL audit, not a sample.
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
        # Persist the cap the run ACTUALLY started with, on the adopted row as well as on a
        # worker-created one. Without it, a 900-page MHD run and a full census are the same row
        # afterwards, and nobody reading the history can tell which one they are looking at.
        run.max_pages = max_pages
        session.commit()
        run_id = run.id

    try:
        cfg = load_brand(brand_code.lower())
        result = asyncio.run(run_audit(cfg, resume=True, limit=max_pages))
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
            # No report written means run_audit found NOTHING TO AUDIT — enumeration returned zero
            # URLs (host down, sitemap blocked, no page index). That is a REFUSAL, not a crash, and
            # the difference is the whole point of the status: a brand that could not be checked
            # must never read as a brand that was checked and found clean.
            #
            # The CLI gets this via publish's EmptyAuditRefused; the worker never calls publish, so
            # it has to make the same judgement itself. Found by the acceptance run: MHD's degraded
            # origin was recorded as `failed`, which reads like our bug rather than their outage.
            pages = int(result.get("pages_audited") or 0)
            if pages == 0:
                r.status = "refused"
                r.error_text = (
                    "no pages could be enumerated, so nothing was audited. The website was "
                    "unreachable or its page index is missing. Previous results are unchanged — "
                    "this brand has NOT been given a clean bill of health.")
                session.commit()
                return {"run_id": run_id, "status": "refused"}
            r.status, r.error_text = "failed", "the audit ran but wrote no report directory"
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


def settle_abandoned_run(run: Run | None) -> str | None:
    """Decide how a run abandoned by a dead worker ends. Returns the new status, or None.

    Split out from `reconcile_orphaned_runs` because the two halves have nothing to do with each
    other: finding abandoned jobs is a Postgres-specific query against procrastinate's own tables,
    while THIS is the judgement a human later reads as fact. Keeping the judgement in a plain
    function means it can be tested directly on any database — the query is plumbing, this is the
    product's promise.

    Two endings, and collapsing them is the bug this guards against:

    * `cancel_requested` -> **cancelled**. A person stopped it. There is no bug to chase, and a
      startup log reading "3 failed" would send someone chasing one anyway.
    * otherwise -> **failed**. The worker died on its own; something may genuinely be wrong.

    Neither is a result. Both left the brand part-checked and both have to say so, because the one
    thing this product may never do is let a brand that was not checked read as a brand that was
    checked and found clean.
    """
    if run is None or run.status not in ("running", "queued"):
        return None
    run.finished_at = datetime.now(timezone.utc)
    if run.cancel_requested:
        run.status = "cancelled"
        run.error_text = (
            "this audit was stopped on purpose by a person, so there is nothing to fix — but it "
            "did not finish, so THIS BRAND WAS NOT FULLY AUDITED. No results were recorded for "
            "it and the previous run's results are unchanged. Run it again for an up-to-date "
            "picture.")
    else:
        run.status = "failed"
        run.error_text = (
            "the audit was interrupted and did not finish — the worker stopped (restart, deploy "
            "or crash). No results were recorded for it, and the previous run's results are "
            "unchanged. Start it again when ready.")
    return run.status


def reconcile_orphaned_runs(stale_after_seconds: int = 60) -> int:
    """Mark runs abandoned by a dead worker, so nothing sits in `running` forever.

    The signal is the WORKER HEARTBEAT, not elapsed time. RR legitimately crawls for six hours, so
    duration can never distinguish "still working" from "died" — but a live worker keeps beating in
    `procrastinate_workers` and a dead one stops. A job still claiming `doing` whose worker has no
    live heartbeat is abandoned.

    Expressed in SQL rather than via `job_manager.get_stalled_jobs`, which is async-only in
    procrastinate 3.9 and would mean running an event loop inside sync worker startup.

    Safe with multiple workers: a job whose worker is still beating is left completely alone, so a
    healthy long crawl is never killed.

    Found by the acceptance run — a worker killed mid-crawl left RR `running` indefinitely, which
    on the dashboard is indistinguishable from a normal six-hour RR crawl.

    This is also where a cancel lands. A crawl cannot be aborted mid-flight, so `POST /cancel` on a
    running run only sets `cancel_requested`; the run keeps going until the worker next stops, and
    the stop is noticed here. So a run carrying `cancel_requested` ends as `cancelled`, not
    `failed`: nobody needs to go looking for a crash a human caused on purpose.
    """
    from sqlalchemy import text as sql_text

    with SessionLocal() as session:
        abandoned = session.execute(sql_text("""
            SELECT j.id, (j.args->>'run_id')::int AS run_id
            FROM procrastinate_jobs j
            LEFT JOIN procrastinate_workers w ON w.id = j.worker_id
            WHERE j.status = 'doing'
              AND (w.id IS NULL
                   OR w.last_heartbeat < now() - make_interval(secs => :stale))
        """), {"stale": stale_after_seconds}).all()

        n = 0
        cancelled = 0
        for job_id, run_id in abandoned:
            if run_id:
                r = session.get(Run, int(run_id))
                settled = settle_abandoned_run(r)
                if settled:
                    cancelled += settled == "cancelled"
                    n += 1
            # retire the job: the run is restarted from scratch, never resumed half-done
            session.execute(sql_text(
                "UPDATE procrastinate_jobs SET status='failed' WHERE id = :jid"), {"jid": job_id})
        session.commit()

    if n:
        # Counted apart because the startup log is the first thing read after a deploy, and
        # "3 failed" when two of them were deliberate cancels sends someone hunting a bug.
        print(f"[startup] marked {n - cancelled} interrupted run(s) as failed, "
              f"{cancelled} as cancelled", flush=True)
    return n
