"""Re-publish every brand's LAST saved report to the sheet, without re-crawling.

The sheet's column order and wording changed after the acceptance run. Re-auditing to pick that up
would cost another 8+ hours, and the `scope` fix has since moved the check-version so the archived
resume caches no longer load. The findings themselves are unchanged and already on disk in
`reports/<brand>/<timestamp>/findings.jsonl`, so publish from those.

This is presentation-only: same findings, same fingerprints, same first_seen dates — only the
formatting differs. Client triage is preserved because publish_open_tab reads it first, as always.

Usage: python3 -m spike.republish_from_reports [--dry-run]
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

from auditor.publish import publish_brand
from auditor.report import Finding, Severity

BRANDS = ["tdrc", "ah", "ar", "dbh", "cad", "coc", "gl", "rr", "mhd"]
# MHD's published row is a SAMPLE of a much larger site and must keep saying so.
PARTIAL = {"mhd": (353, 15640)}


def latest_report(brand: str) -> pathlib.Path | None:
    runs = sorted(pathlib.Path("reports", brand).glob("*/findings.jsonl"))
    return runs[-1] if runs else None


def load(path: pathlib.Path) -> list[Finding]:
    out = []
    for line in path.open(encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("status") == "resolved":
            continue
        # The JSONL stores the severity VALUE ("error"), not the member NAME ("ERROR").
        # Severity["error"] raises, and silently defaulting downgraded every finding to WARNING —
        # the sheet then reported "nothing untriaged" across 47,000 real findings.
        raw = d.get("severity")
        sev = Severity(raw) if isinstance(raw, str) else raw
        f = Finding(url=d.get("url", ""), check=d.get("check", ""), severity=sev,
                    fingerprint=d.get("fingerprint", ""), issue=d.get("issue", ""),
                    location=d.get("location", ""), snippet=d.get("snippet", ""),
                    suggestion=d.get("suggestion", ""), details=d.get("details") or {})
        f.first_seen = d.get("first_seen")
        f.status = d.get("status")
        out.append(f)
    return out


def main(dry_run: bool = False) -> None:
    from auditor.publish import _client
    run_id = "republish-" + time.strftime("%Y%m%dT%H%M%S")
    client = _client(dry_run)
    today = time.strftime("%Y-%m-%d")
    for brand in BRANDS:
        path = latest_report(brand)
        if not path:
            print(f"{brand.upper():5} no saved report — skipped")
            continue
        findings = load(path)
        if not findings:
            print(f"{brand.upper():5} report has no open findings — skipped")
            continue
        first_seen = {f.fingerprint: (f.first_seen or today)[:10] for f in findings}
        counts: dict[str, int] = {}
        for f in findings:
            k = getattr(f.severity, "name", str(f.severity))
            counts[k] = counts.get(k, 0) + 1
        detail = ""
        if brand in PARTIAL:
            done, total = PARTIAL[brand]
            detail = (f"PARTIAL SAMPLE — {done} pages audited out of {total} live on this brand. "
                      f"This is a SAMPLE, not a complete audit: findings here are real, but absence "
                      f"of a finding does NOT mean the rest of the site is clean.")
        line = publish_brand(
            client, brand=brand.upper(), run_id=run_id, findings=findings,
            delta={"new": 0, "resolved": 0, "open": len(findings), "new_fingerprints": set()},
            changed_checks=set(), first_seen=first_seen, run_date=today,
            started=time.strftime("%Y-%m-%dT%H:%M:%S"), finished=time.strftime("%Y-%m-%dT%H:%M:%S"),
            pages=0, counts=counts, detail=detail)
        print(f"{brand.upper():5} {len(findings):>6} findings republished   {line}")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
