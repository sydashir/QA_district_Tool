"""Per-brand publishing: idempotent, and never half-done.

A nightly run publishes nine brands one after another. Two things must hold when it dies on brand
four, because nobody is awake at 3am to reconcile the sheet by hand:

1. **Re-running is safe.** Every tab write is a full replacement keyed by tab name, so redoing a
   brand converges on the same content. The one append — the ``Summary`` history row — is made
   idempotent by ``(run_id, brand)``: a retry UPDATES that row instead of adding a second.
2. **A brand is visibly unfinished rather than silently wrong.** ``Summary`` gets ``running``
   BEFORE the tabs are touched and is only flipped to ``ok`` after every tab has been written. A
   crash therefore leaves ``running`` on the board — the thing a human can actually notice.

Ordering is deliberate: ``open`` (the tab carrying the client's triage) is written first, then
``new``, then the Summary row is closed. If the process dies midway, the worst case is a brand whose
``new`` tab is one run stale while ``Summary`` still says ``running`` — never a brand that claims to
be finished when it is not.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .sheets import SUMMARY_HEADER, digest_line, publish_open_tab, summary_row
from .triage import untriaged_error_summary

_log = logging.getLogger(__name__)

SUMMARY_TAB = "Summary"
SUMMARY_HEADER_WITH_RUN = ["run_id"] + SUMMARY_HEADER
NEW_HEADER = ["url", "check", "severity", "issue", "suggestion", "fingerprint", "location",
              "snippet", "change"]


def _find_summary_row(client, run_id: str, brand: str) -> int | None:
    """Index of this (run_id, brand)'s existing Summary row, if the run already started."""
    existing = client.read_tab(SUMMARY_TAB)
    if not existing:
        return None
    header, rows = existing
    try:
        i_run, i_brand = header.index("run_id"), header.index("brand")
    except ValueError:
        return None
    for n, r in enumerate(rows):
        if len(r) > max(i_run, i_brand) and r[i_run] == run_id and r[i_brand] == brand:
            return n
    return None


def publish_brand(client, *, brand: str, run_id: str, findings, delta: dict,
                  changed_checks: set[str], first_seen: dict, run_date: str,
                  started: str, pages: int, counts: dict | None = None,
                  css_status: str = "", sitemap_partial: bool = False,
                  finished: str = "", duration_s: int = 0, detail: str = "") -> str:
    """Publish one brand's tabs + Summary row. Returns the digest line for the email."""
    client.ensure_tab(SUMMARY_TAB, header=SUMMARY_HEADER_WITH_RUN)
    row_idx = _find_summary_row(client, run_id, brand)

    start_row = summary_row(brand=brand, status="running", started=started, pages=pages,
                            counts=counts, delta=delta, css_status=css_status,
                            sitemap_partial=sitemap_partial)
    start_row = [run_id] + start_row
    if row_idx is None:
        client.append_row(SUMMARY_TAB, start_row)
        row_idx = _find_summary_row(client, run_id, brand)
        if row_idx is None:                       # tolerate a fake/back-end that cannot re-read
            row_idx = 0
    else:
        client.update_row(SUMMARY_TAB, row_idx, start_row)

    open_tab, new_tab = f"{brand} — open", f"{brand} — new"
    client.ensure_tab(open_tab)
    client.ensure_tab(new_tab)

    # open FIRST — it is the tab carrying the client's triage, and publish_open_tab refuses to
    # write it at all if the existing triage cannot be read.
    rows = publish_open_tab(client, "", open_tab, findings, changed_checks=changed_checks,
                            today=run_date, first_seen=first_seen, run_date=run_date)

    changed = [f for f in findings if f.fingerprint in (delta.get("new_fingerprints") or set())]
    from .humanize import check_label, plain_issue, suggestion_for
    client.replace_tab(new_tab, NEW_HEADER, [[
        f.url, check_label(f.check),
        getattr(f.severity, "name", str(f.severity)),
        plain_issue(f.check, f.issue),
        suggestion_for(f.check, f.issue, f.suggestion or "")[:500],
        f.fingerprint, f.location, (f.snippet or "")[:500], "new",
    ] for f in changed])

    untriaged = untriaged_error_summary(rows)
    done_row = summary_row(brand=brand, status="ok", started=started,
                           finished=finished or run_date, pages=pages, counts=counts, delta=delta,
                           untriaged=untriaged, css_status=css_status,
                           sitemap_partial=sitemap_partial, duration_s=duration_s, detail=detail)
    client.update_row(SUMMARY_TAB, row_idx, [run_id] + done_row)
    return digest_line(brand, delta, untriaged)


# The client's output spreadsheet. Owned by meetashirr@gmail.com; the service account
# app-service-account@lexical-sol-454719-s2.iam.gserviceaccount.com has Editor (verified 2026-08-04).
# A service account can never CREATE a sheet (no Drive storage quota), so this is a human-made file.
SHEET_ID = "1QnKHZBnEoxW2gIcOdDz6Ac2WjUbAa94Te_KR-7r2m-E"
# WHERE THE SERVICE-ACCOUNT KEY LIVES. This was a single hardcoded laptop path, which meant the
# CLIENT-FACING output path — the only thing that puts findings in front of the QA team — returned
# 503 from any machine that was not Syed's Mac. Containerising made that immediate rather than
# theoretical.
#
# Resolved at CALL time, not import time: the module must import cleanly on a box with no key at
# all (the tests do exactly that), and an env var set by compose must be able to win.
CREDENTIALS_ENV = ("SHEETS_CREDENTIALS", "GOOGLE_APPLICATION_CREDENTIALS")
CREDENTIALS_FALLBACKS = (
    "/etc/auditor/service-account.json",                 # deploy target (deploy/README.md)
    str(Path(__file__).resolve().parent.parent / "credentials" / "service-account.json"),
    # The original laptop path, kept LAST so nothing breaks for the machine this was written on.
    "/Users/ashir/Documents/workk/district/credentials/service-account.json",
)


class CredentialsMissing(RuntimeError):
    """No service-account key could be found. Says where it looked, so it is actionable."""


def resolve_credentials() -> str:
    """First existing key from the env vars, then the fallbacks. Raises with the full search path.

    An error that just says "credentials not found" costs someone an hour. This one names the env
    var to set and every path it tried, in order.
    """
    tried: list[str] = []
    for var in CREDENTIALS_ENV:
        val = os.getenv(var)
        if val:
            if Path(val).is_file():
                return val
            # An env var that is SET but points at nothing is an error, not a reason to look
            # elsewhere. Falling through would quietly publish with a different key than the
            # operator named — the failure would be invisible and would reach the client's sheet.
            raise CredentialsMissing(
                f"${var} is set to {val!r} but no such file exists. Fix the path or unset the "
                f"variable to fall back to {CREDENTIALS_FALLBACKS[0]}.")
    for cand in CREDENTIALS_FALLBACKS:
        if Path(cand).is_file():
            return cand
        tried.append(cand)
    raise CredentialsMissing(
        "no Google service-account key found, so the sheet cannot be written. Set "
        "$SHEETS_CREDENTIALS to the key file (or mount it at /etc/auditor/service-account.json). "
        "Looked in order: " + "; ".join(tried))


def _client(dry_run: bool, out=None):
    import sys

    from .sheets import DryRunSheets, SheetsClient
    real = SheetsClient(spreadsheet_id=SHEET_ID, credentials_path=resolve_credentials())
    if not dry_run:
        return real
    # A dry run still READS, so the preview reflects the triage the client has actually written.
    return DryRunSheets(out or sys.stdout, reader=real)


class EmptyAuditRefused(RuntimeError):
    """Raised when an audit produced no pages, so there is nothing honest to publish."""


def _detail(result: dict, delta: dict, all_findings: list) -> str:
    """The prose a human reads on the Summary row. Sentences, in order of how much they change the
    reading of the numbers beside them."""
    parts = []
    if result.get("partial_sample"):
        parts.append(
            "PARTIAL SAMPLE — %d pages audited out of %d live on this brand. This is a SAMPLE, "
            "not a complete audit: findings here are real, but absence of a finding does NOT "
            "mean the rest of the site is clean." % (result.get("pages_audited", 0),
                                                     result.get("scope_total", 0)))
    # Said only when it happened. A "0 pages removed" note on every brand every run trains people
    # to skip this column, and PARTIAL SAMPLE also lives here.
    gone = delta.get("pages_removed", 0)
    if gone:
        # "no longer listed", NOT "deleted". The diff knows only that these pages left the site's
        # own page list; it never probes them. Measured 2026-09-24: GL's 224 return 404 (genuinely
        # gone) while MHD's 134 return 301 (still there, redirected). A sentence claiming deletion
        # would have been false on one of the two brands it first shipped to.
        parts.append(
            f"{gone} page{'s' if gone != 1 else ''} covered by the previous audit "
            f"{'are' if gone != 1 else 'is'} no longer listed on the site, so earlier findings on "
            f"{'them' if gone != 1 else 'it'} are not counted as open work — and are not reported "
            f"as fixed either. They may have been removed or redirected; the audit does not probe "
            f"them to tell which.")
    unsitemapped = len({f.url for f in all_findings
                        if getattr(f, "status", None) == "page_unsitemapped"})
    if unsitemapped:
        parts.append(
            f"{unsitemapped} page{'s' if unsitemapped != 1 else ''} left the audit scope but may "
            f"still be live; their earlier findings are not counted as open work.")
    return " ".join(parts)


def publish_brand_from_result(brand: str, result: dict, *, run_id: str, dry_run: bool = False,
                              out=None, started: str | None = None,
                              duration_s: int = 0, client=None) -> str:
    """Publish one brand straight from ``run_audit``'s return value."""
    import time
    from collections import Counter

    # AN EMPTY AUDIT IS NOT A SUCCESSFUL ONE. run_audit already refuses to write a report when it
    # audited nothing; publishing a Summary row saying `ok` with zero findings would present that
    # same emptiness to the client as a clean brand. Fail loudly instead — the brand keeps whatever
    # it had, and the failure is listed at the end of the run.
    if not result.get("pages_audited"):
        raise EmptyAuditRefused(
            f"{brand.upper()}: 0 pages audited, so there is nothing to publish. The sheet is "
            f"unchanged. Check host availability and whether the resume cache was invalidated by "
            f"a check-version change.")

    # OPEN WORK = exactly what `scripts/client_report.py::fetch` shows (`status IN
    # ('new','persisting')`). The two client-facing surfaces must not disagree about the same run.
    # Excluding `resolved` alone left the carried states in: a finding on a page that now 404s
    # (`page_removed`) is not something anybody can fix, `rule_changed` was un-flagged by a moved
    # ruler, and `page_unsitemapped` left the audit scope. Measured on GL run 142 (2026-09-24): the
    # sheet said 9,173 open / 800 errors against the report's 8,077 / 567, a 1,096 gap that stayed
    # invisible until GL deleted 224 pages at once. The removals are REPORTED below, not dropped.
    all_findings = result.get("findings", [])
    findings = [f for f in all_findings if getattr(f, "status", None) in ("new", "persisting")]
    counts = Counter(getattr(f.severity, "name", str(f.severity)) for f in findings)
    run = result.get("run") or {}
    # `rollup` is a writers.Rollup object, not a dict — its counters live on attributes.
    rollup = run.get("rollup")
    by_status = dict(getattr(rollup, "by_status", None) or {})
    delta = {
        "new": by_status.get("new", 0),
        # ONLY genuine resolutions. The tail also carries page_removed / page_unsitemapped /
        # rule_changed — states diff.py exists precisely to keep APART from "fixed" — so counting
        # its length told the client a defect was fixed when the page had merely not been audited.
        "resolved": sum(1 for f in (run.get("resolved") or [])
                        if getattr(f, "status", None) == "resolved"),
        "rule_changed": by_status.get("rule_changed", 0),
        "open": len(findings),
        "new_fingerprints": {f.fingerprint for f in findings
                             if getattr(f, "status", None) == "new"},
        # PAGES, not findings: two defects on one deleted page is one page gone.
        "pages_removed": len({f.url for f in all_findings
                              if getattr(f, "status", None) == "page_removed"}),
    }
    first_seen = {f.fingerprint: (f.first_seen or "")[:10] for f in findings if f.first_seen}
    return publish_brand(
        client or _client(dry_run, out), brand=brand.upper(), run_id=run_id, findings=findings,
        delta=delta, changed_checks=set(run.get("changed") or []), first_seen=first_seen,
        run_date=time.strftime("%Y-%m-%d"),
        started=started or time.strftime("%Y-%m-%dT%H:%M:%S"),
        finished=time.strftime("%Y-%m-%dT%H:%M:%S"),
        pages=result.get("pages_audited", 0), counts=dict(counts),
        css_status=result.get("css_status", ""), duration_s=duration_s,
        sitemap_partial=bool(result.get("sitemap_partial")),
        detail=_detail(result, delta, all_findings))


def publish_result(brand: str, result: dict, *, dry_run: bool = False) -> str:
    """Single-brand publish for `audit --brand X --publish`."""
    import time
    return publish_brand_from_result(brand, result,
                                     run_id=time.strftime("%Y%m%dT%H%M%S"), dry_run=dry_run)
