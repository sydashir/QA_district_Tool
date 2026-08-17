"""Google Sheet export.

The sheet stays as an EXPORT, not the interface. The QA team already has workflows hanging off it
and there is no reason to force them off; the dashboard is the better tool, the sheet is the
familiar one.

Rather than re-implement publishing, this rebuilds the exact `result` dict that
`auditor.publish.publish_brand_from_result` already consumes, from the report the run wrote to
disk. Reusing that path means the sheet keeps its existing triage-preservation, its Summary row,
and its partial-sample labelling — all of which were hard-won and are already tested.

Requires Google service-account credentials to actually write. Without them it raises, and the API
surfaces that as a 503; the CODE is finished either way, so credentials are a deployment step and
never a blocker for the build.
"""
from __future__ import annotations

import json
from pathlib import Path

from auditor.report import Finding, Severity

REPO = Path(__file__).resolve().parent.parent


class _Rollup:
    """Stands in for writers.Rollup — publish only reads `.by_status` off it."""

    def __init__(self, by_status: dict[str, int]):
        self.by_status = by_status


def result_from_report(report_dir: str | Path) -> dict:
    """Rebuild the `result` dict publish expects, from a report directory on disk.

    Every field below is one publish actually reads (checked against
    `publish_brand_from_result`): pages_audited, findings, run.rollup.by_status, run.resolved,
    css_status, sitemap_partial, partial_sample, scope_total.
    """
    d = Path(report_dir)
    if not d.is_absolute():
        d = REPO / d
    if not (d / "findings.jsonl").is_file():
        raise FileNotFoundError(f"no findings.jsonl in {d}")

    summary = json.loads((d / "summary.json").read_text(encoding="utf-8"))

    findings: list[Finding] = []
    resolved: list[Finding] = []
    by_status: dict[str, int] = {}
    with (d / "findings.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            status = r.get("status")
            by_status[status or ""] = by_status.get(status or "", 0) + 1
            try:
                sev = Severity(str(r.get("severity") or "info"))
            except ValueError:
                sev = Severity.INFO
            f = Finding(
                url=r.get("url") or "", check=r.get("check") or "",
                fingerprint=r.get("fingerprint") or "", severity=sev,
                issue=r.get("issue") or "", location=r.get("location"),
                snippet=r.get("snippet"), suggestion=r.get("suggestion"),
                details=r.get("details") or {},
                first_seen=r.get("first_seen"), last_seen=r.get("last_seen"),
                status=status,
            )
            # Publish filters `resolved` out of the open tab itself, but it counts the tail for the
            # "N fixed" figure — so both lists are handed over exactly as a live run would.
            (resolved if status == "resolved" else findings).append(f)

    scope = summary.get("audit_scope") or {}
    return {
        "pages_audited": int(summary.get("pages_audited") or 0),
        "findings": findings + resolved,
        "run": {"rollup": _Rollup(by_status), "resolved": resolved,
                "out_dir": str(d), "components": {}, "changed": summary.get("changed_components")},
        "css_status": summary.get("css_status", ""),
        "sitemap_partial": bool(summary.get("sitemap_partial")),
        "partial_sample": bool(summary.get("urls_file")) or not summary.get("history_written", True),
        "scope_total": int(scope.get("union") or summary.get("pages_enumerated") or 0),
    }


def export_brand(brand_code: str, report_dir: str | Path, *, dry_run: bool = True) -> str:
    """Publish one brand's latest report to the sheet. Returns publish's own digest line."""
    from auditor.publish import publish_result
    return publish_result(brand_code.lower(), result_from_report(report_dir), dry_run=dry_run)
