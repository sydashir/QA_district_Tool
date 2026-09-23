"""Google Sheet export.

The sheet stays as an EXPORT, not the interface. The QA team already has workflows hanging off it
and there is no reason to force them off; the dashboard is the better tool, the sheet is the
familiar one.

Rather than re-implement publishing, this rebuilds the exact `result` dict that
`auditor.publish.publish_brand_from_result` already consumes. Reusing that path means the sheet
keeps its existing triage-preservation, its Summary row, and its partial-sample labelling — all of
which were hard-won and are already tested.

THE FINDINGS COME FROM THE DATABASE, NOT FROM THE CRAWL'S REPORT FILE (changed 2026-09-23).
`findings.jsonl` holds only what the CRAWL wrote. `scripts/accessibility_pass.py` and
`scripts/render_pass.py` attach their findings to the run row in Postgres and never touch that
file, so the sheet and the client HTML report — which reads the database — disagreed about the same
run. Measured: TDRC run 141 exported as "80 open, 7 ERRORs" while the database held 93 and 20, and
across the nine brands **265** accessibility/contrast findings could not reach the sheet at all.
That is a whole CATEGORY missing rather than a sample, so a sheet reader would reasonably conclude
contrast and screen-reader problems are not checked — the exact false impression Section D was
fixed for. The database is already the system of record (the client report reads it, the web app
reads it, the passes write to it), so reading it here means any FUTURE pass is visible everywhere
at once instead of recreating this divergence.

Side effect, measured and accepted: the database holds one row per `UNIQUE(run_id, fingerprint)`,
so 43 duplicate-fingerprint rows across the nine brands collapse. Verified that the distinct
fingerprint count on disk equals the row count in the database for every brand — no distinct
finding is lost, and the sheet is keyed on fingerprint for triage anyway, so duplicates could never
have held separate triage state.

THREE RUN-LEVEL SCALARS STILL COME FROM summary.json, deliberately: `css_status`,
`sitemap_partial`, and the audit-set denominator. They exist nowhere else and no pass ever changes
them. In particular the denominator is NOT `runs.pages_enumerated` — that is the SITEMAP count,
which on AR is 4 against an audit set of 474 (CLAUDE.md 6a), so the PARTIAL SAMPLE banner would
have claimed we audited 474 pages out of 4.

Requires Google service-account credentials to actually write. Without them it raises, and the API
surfaces that as a 503; the CODE is finished either way, so credentials are a deployment step and
never a blocker for the build.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from auditor.report import Finding, Severity

from .models import Finding as DbFinding
from .models import Run

REPO = Path(__file__).resolve().parent.parent


class _Rollup:
    """Stands in for writers.Rollup — publish only reads `.by_status` off it."""

    def __init__(self, by_status: dict[str, int]):
        self.by_status = by_status


def _crawl_only_facts(report_dir: str | Path) -> dict:
    """The three things the run row does not record, read from the crawl's own summary.

    Raises if the report is gone rather than degrading: a missing `sitemap_partial` would publish a
    partial-sitemap run with no caveat, which reads as a complete, clean audit.
    """
    d = Path(report_dir)
    if not d.is_absolute():
        d = REPO / d
    if not (d / "summary.json").is_file():
        raise FileNotFoundError(f"no summary.json in {d}")
    summary = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    scope = summary.get("audit_scope") or {}
    return {
        "css_status": summary.get("css_status", ""),
        "sitemap_partial": bool(summary.get("sitemap_partial")),
        # The audit SET (sitemap union WP-REST), never runs.pages_enumerated — see the module note.
        "scope_total": int(scope.get("union") or summary.get("pages_enumerated") or 0),
        "out_dir": str(d),
    }


def result_from_run(session: Session, run: Run) -> dict:
    """Rebuild the `result` dict publish expects, from the DATABASE plus the crawl's summary.

    Every field below is one publish actually reads (checked against `publish_brand_from_result`):
    pages_audited, findings, run.rollup.by_status, run.resolved, run.changed, css_status,
    sitemap_partial, partial_sample, scope_total.
    """
    facts = _crawl_only_facts(run.report_dir or "")

    findings: list[Finding] = []
    resolved: list[Finding] = []
    by_status: dict[str, int] = {}
    rows = session.scalars(select(DbFinding).where(DbFinding.run_id == run.id)
                           .order_by(DbFinding.id))
    for r in rows:
        by_status[r.status or ""] = by_status.get(r.status or "", 0) + 1
        try:
            sev = Severity(str(r.severity or "info"))
        except ValueError:
            sev = Severity.INFO
        f = Finding(
            url=r.url or "", check=r.check or "", fingerprint=r.fingerprint or "",
            severity=sev, issue=r.issue or "", location=r.location, snippet=r.snippet,
            suggestion=r.suggestion, details=r.details or {},
            first_seen=r.first_seen, last_seen=r.last_seen, status=r.status,
        )
        # Publish filters `resolved` out of the open tab itself, but it counts the tail for the
        # "N fixed" figure — so both lists are handed over exactly as a live run would.
        (resolved if r.status == "resolved" else findings).append(f)

    return {
        "pages_audited": int(run.pages_audited or 0),
        "findings": findings + resolved,
        "run": {"rollup": _Rollup(by_status), "resolved": resolved,
                "out_dir": facts["out_dir"], "components": {},
                "changed": run.changed_components},
        "css_status": facts["css_status"],
        "sitemap_partial": facts["sitemap_partial"],
        "partial_sample": bool(run.partial_sample),
        "scope_total": facts["scope_total"],
    }


def export_run(session: Session, brand_code: str, run: Run, *, dry_run: bool = True) -> str:
    """Publish one brand's latest run to the sheet. Returns publish's own digest line."""
    from auditor.publish import publish_result
    return publish_result(brand_code.lower(), result_from_run(session, run), dry_run=dry_run)
