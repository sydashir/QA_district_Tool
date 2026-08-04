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

from .sheets import SUMMARY_HEADER, digest_line, publish_open_tab, summary_row
from .triage import untriaged_error_summary

_log = logging.getLogger(__name__)

SUMMARY_TAB = "Summary"
NEW_HEADER = ["url", "check", "severity", "issue", "location", "snippet", "suggestion",
              "fingerprint", "change"]


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
                  css_status: str = "", sitemap_partial: bool = False) -> str:
    """Publish one brand's tabs + Summary row. Returns the digest line for the email."""
    client.ensure_tab(SUMMARY_TAB)
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
    client.replace_tab(new_tab, NEW_HEADER, [[
        f.url, f.check,
        getattr(f.severity, "name", str(f.severity)),
        f.issue, f.location, (f.snippet or "")[:500], (f.suggestion or "")[:500],
        f.fingerprint, "new",
    ] for f in changed])

    untriaged = untriaged_error_summary(rows)
    done_row = summary_row(brand=brand, status="ok", started=started, finished=run_date,
                           pages=pages, counts=counts, delta=delta, untriaged=untriaged,
                           css_status=css_status, sitemap_partial=sitemap_partial)
    client.update_row(SUMMARY_TAB, row_idx, [run_id] + done_row)
    return digest_line(brand, delta, untriaged)


SUMMARY_HEADER_WITH_RUN = ["run_id"] + SUMMARY_HEADER
