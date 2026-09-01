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
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text as sql_text

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

    # COOL DOWN before touching the network. The queue used to start the next brand's enumeration
    # the instant the previous run ended; on 2026-09-01 that put three brands into the 8 seconds
    # after a 400-request link-probe burst, got all three rate limited, and recorded all three as
    # "the website was unreachable". Waiting is cheaper than a false report.
    with SessionLocal() as session:
        last = session.execute(sql_text("""
            SELECT extract(epoch FROM (now() - max(finished_at)))
            FROM runs WHERE finished_at IS NOT NULL AND id <> :rid"""), {"rid": run_id}).scalar()
    wait = cooldown_seconds(float(last) if last is not None else None)
    if wait:
        print(f"[cooldown] a run finished {float(last):.0f}s ago — waiting {wait:.0f}s before "
              f"crawling {brand_code} so we are not throttled", flush=True)
        time.sleep(wait)

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
            verdict = crawl_verdict(result)
            if verdict.refuse:
                r.status = "refused"
                r.error_text = verdict.reason
                session.commit()
                return {"run_id": run_id, "status": "refused"}
            r.status, r.error_text = "failed", "the audit ran but wrote no report directory"
            session.commit()
            raise RuntimeError(r.error_text)
        # THE CASE THAT ACTUALLY BIT. The refusal above only fires when no report was written.
        # RR run 119 DID write one — it reached 77 of 8,029 pages, produced 7,777 "page unreachable"
        # findings, and was recorded `ok` because 77 is not 0. Check the crawl itself, not just
        # whether a file exists, and refuse BEFORE importing so the false findings never land.
        verdict = crawl_verdict(result)
        if verdict.refuse:
            r.status = "refused"
            r.error_text = verdict.reason
            session.commit()
            return {"run_id": run_id, "status": "refused"}

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


# A crawl that reached almost none of the site is not a census, and must never publish as one.
#
# MEASURED, not chosen. Across the 106 historical run summaries on disk the fetch success rate has a
# median of 99.7% and a 10th percentile of 90.5%. Exactly ONE run falls below 50% — RR run 119, which
# attempted 8,029 pages, got 77 back, and was recorded `ok`. The legitimate lows sit well clear of
# the floor: COC 58.7% (its sitemap really is ~38% dead), MHD 69% (degraded origin), DBH 75%.
# So a 50% floor fires once in 106 runs, on the one run we know was wrong.
CRAWL_FLOOR = 0.50

# A RELATIVE test — "did this collapse from the brand's own norm" — was designed, measured, and
# DROPPED. The idea is sound: one number cannot separate "throttled tonight" from "this brand has
# always been at 70%". The data does not support it. Measured against the same 106 runs, refusing at
# `rate < ratio x brand_median` costs:
#
#     ratio 0.50 -> 0 legitimate runs refused
#     ratio 0.60 -> 1 refused: COC at 58.7%, a REAL run (its sitemap is ~38% dead)
#     ratio 0.70 -> 2 refused: COC at 62.4% and 58.7%
#
# So 0.50 is the tightest safe ratio, and at 0.50 the relative threshold lands at ~49.9% for a brand
# with a 99.7% median — below the hard floor, for every brand we have. It could never fire.
# Shipping a guard that cannot trigger is worse than shipping none: it reads as protection.
#
# The hard floor already achieves what the relative test was for. MHD is the case that motivated it —
# always ~69-88%, never healthy — and 50% clears MHD comfortably while catching the 1% run.
# Revisit only with new evidence: a brand whose legitimate floor is genuinely below 50%.


# Seconds to wait before starting a crawl when another has only just finished.
#
# At 05:58 on 2026-09-01 the queue started GL, RR and MHD in the 8 seconds after COC's run ended
# with a 400-request link-probe burst. All three were rate limited, each failed enumeration in 2-3
# seconds, and each was recorded as "the website was unreachable" — a confident statement about the
# client's sites produced entirely by our own burst. Ten minutes later all three served 200.
#
# 90s is chosen to be longer than a typical rate-limit window and short enough to be invisible
# against runs measured in hours. It is not tuned; if throttling recurs, raise it.
COOLDOWN_SECONDS = 90


def cooldown_seconds(seconds_since_last_run: float | None) -> float:
    """How long to wait before hitting the network again. 0 when the last run is long past."""
    if seconds_since_last_run is None:
        return 0
    remaining = COOLDOWN_SECONDS - seconds_since_last_run
    return remaining if remaining > 0 else 0


@dataclass
class CrawlVerdict:
    """Whether this run may stand as a result, and the sentence a human reads if not."""
    refuse: bool
    reason: str = ""


def crawl_verdict(result: dict, history: list[float] | None = None) -> CrawlVerdict:
    """Decide whether a finished crawl is a result or a failure to reach the site.

    The old test was `pages_audited == 0`. 77 is not 0, so a run that reached 0.96% of RR was
    recorded as a successful full census — and the 7,777 "page unreachable" findings it produced
    read as the client's site being down. They were our throttling.

    Every reason here says whose fault it is. That is the point: the previous refusal text —
    "The website was unreachable or its page index is missing" — was a confident statement about
    the client's site, produced by our own condition.
    """
    fetched = int(result.get("fetched") or 0)
    ok = int(result.get("fetched_ok") or 0)
    resumed = int(result.get("resumed_from_cache") or 0)
    blocked = bool(result.get("sitemap_blocked"))

    # Nothing to fetch at all: enumeration found no URLs.
    if fetched == 0 and resumed == 0:
        if blocked:
            return CrawlVerdict(True, (
                "the page index could not be read because the site BLOCKED our crawler, so nothing "
                "was audited. This is a access problem between us and the site, not evidence that "
                "the site is broken. Previous results are unchanged — this brand has NOT been "
                "audited and has NOT been given a clean bill of health."))
        return CrawlVerdict(True, (
            "no pages could be enumerated, so nothing was audited. The site's page index is "
            "missing or empty. Previous results are unchanged — this brand has NOT been audited "
            "and has NOT been given a clean bill of health."))

    if fetched == 0:
        return CrawlVerdict(False)          # everything served from cache; a legitimate resume

    rate = ok / fetched
    common = (f"Previous results are unchanged — this brand has NOT been audited and has NOT been "
              f"given a clean bill of health. Try again later, and more slowly.")

    if rate < CRAWL_FLOOR:
        return CrawlVerdict(True, (
            f"this run reached only {ok:,} of {fetched:,} pages ({rate:.1%}). A crawl that fails "
            f"that broadly is almost always OUR side being throttled or rate limited, not the "
            f"site going down — pages do not stop existing all at once. Reporting it would claim "
            f"{fetched - ok:,} of the client's pages are unreachable when they are not. {common}"))

    return CrawlVerdict(False)


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
