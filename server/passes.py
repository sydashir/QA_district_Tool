"""Attaching findings produced OUTSIDE the crawl — the accessibility and render passes.

WHY THESE ATTACH TO AN EXISTING RUN. Both passes need a browser, so they cannot run inside the
crawl. They describe the same pages the latest run audited, and inventing a run row for them would
put a second "latest run" in front of every report and diff.

WHY THEY NEED A STATUS OF THEIR OWN. The run-diff has already finished by the time a pass writes,
so nothing else can decide whether these findings are new. Both passes used to stamp `status="new"`
unconditionally — 579 accessibility and 88 contrast rows in the database, not one of them anything
else. That was invisible while the Google Sheet was rebuilt from `findings.jsonl` (these findings
never reached it). The moment the export started reading the database on 2026-09-23 it became
visible and wrong: TDRC would have published "14 new" when 1 was new and 13 had been there since
run 131, and AH "32 new" against 4. The sheet's `new` column is the QA team's work queue, so that is
the same class of error as the "AH: 171 fixed" incident, pointing the other way.

So a finding is NEW only when its fingerprint is absent from the most recent EARLIER run that
carried that check — per check, because the two passes run on different cadences and a run where
only one of them ran must not make the other's findings all look new.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Brand, Finding, Run

# The checks written OUTSIDE the crawl, i.e. the ones whose status this module decides. Named here
# rather than in each script so "which findings does the run-diff not cover" has one answer.
# `accessibility` comes from scripts/accessibility_pass.py; `contrast` and `broken_image` from
# scripts/render_pass.py (tap targets are measured there but deliberately not published).
PASS_CHECKS = frozenset({"accessibility", "contrast", "broken_image"})


def _previous_run_with(session: Session, brand_id: int, run: Run, check: str) -> int | None:
    """The newest run of this brand, EARLIER than `run`, that carries a finding of `check`.

    Most recent rather than oldest on purpose: a defect that was fixed and later reintroduced is
    genuinely new again, and reading the oldest run would call it persisting and hide the
    regression.
    """
    return session.scalar(
        select(Finding.run_id)
        .join(Run, Run.id == Finding.run_id)
        .where(Finding.brand_id == brand_id, Finding.check == check,
               Run.started_at < run.started_at, Finding.run_id != run.id)
        .order_by(Run.started_at.desc())
        .limit(1))


def attach(session: Session, brand: Brand, run: Run, findings: list) -> int:
    """Write `findings` onto `run`, classified against that check's own history. Returns the count.

    Skips any fingerprint already on this run, so re-running a pass is idempotent — the behaviour
    the original per-script `store` had, and what `UNIQUE(run_id, fingerprint)` relies on.
    """
    existing = set(session.scalars(select(Finding.fingerprint).where(Finding.run_id == run.id)))

    # One lookup per check, not per finding: a brand can carry thousands.
    history: dict[str, dict[str, str | None]] = {}
    for check in {f.check for f in findings}:
        prior_id = _previous_run_with(session, brand.id, run, check)
        if prior_id is None:
            history[check] = {}
            continue
        history[check] = {
            fp: first for fp, first in session.execute(
                select(Finding.fingerprint, Finding.first_seen)
                .where(Finding.run_id == prior_id, Finding.check == check)).all()}

    run_day = (run.started_at.date().isoformat() if run.started_at else None)
    rows = []
    for f in findings:
        if f.fingerprint in existing:
            continue
        existing.add(f.fingerprint)
        seen_before = history.get(f.check, {})
        persisting = f.fingerprint in seen_before
        rows.append(Finding(
            brand_id=brand.id, run_id=run.id,
            fingerprint=f.fingerprint, fingerprint_hash=_fp_hash(f.fingerprint),
            url=f.url, check=f.check,
            severity=str(getattr(f.severity, "value", f.severity)),
            issue=f.issue, location=f.location, snippet=f.snippet, suggestion=f.suggestion,
            details=f.details or {},
            status="persisting" if persisting else "new",
            # Carried forward, not re-stamped: the sheet prints first_seen, and re-dating a defect
            # every run would make an old problem look like it appeared today.
            first_seen=(seen_before.get(f.fingerprint) or run_day) if persisting else run_day,
            page_count=int((f.details or {}).get("page_count") or 1),
            sources=(f.details or {}).get("sources")))
    session.bulk_save_objects(rows)
    session.commit()
    return len(rows)


def _fp_hash(fp: str) -> str:
    from .models import fp_hash
    return fp_hash(fp)
