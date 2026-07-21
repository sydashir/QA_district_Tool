"""Enumeration reconciliation — "the 845".

Live WP pages that are absent from the sitemap (``rest_set - sitemap_set``) are emitted as
PER-PAGE findings (``enumeration:missing_from_sitemap:{brand}:{url}``) so the run-diff tracks
each one as a system of record: a page silently dropping out of the sitemap becomes a NEW
finding; being re-added resolves it. This is the D1 anomaly seen through the diff.

The sitemap-vs-REST diff (``reconcile``) lives HERE, next to the finding logic, on purpose:
the check-version component ``src:enumeration.py`` then captures "how we diff sitemap vs REST",
and ``src:crawl.py`` (mapped enumeration-scoped in diff.py) captures the raw enumeration logic.
A change to either moves the component, so vanished enumeration findings classify rule_changed,
never silent-resolved.

D1 severity (LOCKED by Syed):
- noindex + unsitemapped + no cruft        -> INFO    (working as designed; inventory)
- indexable + unsitemapped                 -> WARNING  (real anomaly; Google may never find it)
- cruft (*-delete/*-copy/*-old) + noindex  -> WARNING  (cleanup; noindex mitigates)
- cruft + indexable                        -> ERROR    (live, findable, shouldn't exist)
- in WP-REST but 404s publicly             -> WARNING, separate (integrity bug; cross-ref links)

Noindex is the projection's ``is_noindex`` — robots META **or** the ``X-Robots-Tag`` response
header, both captured on the one union fetch (``_project``). There is no separate HEAD probe:
every live page is now content-fetched anyway (union scope), so the enumeration findings are
DERIVED from that same fetch (``from_audit``) rather than re-fetching the unsitemapped set.
"""
from __future__ import annotations

import re

from .. import crawl as C
from ..report import Finding, Severity, canonical_url, make_fingerprint

CHECK = "enumeration"

# Cruft = WP cleanup/duplicate slug markers. A trailing path segment ENDING in delete/copy/old
# (+ optional -N dupe suffix): /page-copy/, /thing-old, /x-delete-2/. Conservative — anchored to
# a leading -/ and the end — to keep the cruft+indexable ERROR precise (no 'goldman' false hits).
CRUFT_RE = re.compile(r"[-/](?:delete|copy|old)(?:-\d+)?/?$", re.IGNORECASE)


def is_cruft(url: str) -> bool:
    return bool(CRUFT_RE.search(url))


def _finding(url: str, brand: str, sev: Severity, issue: str, cls: str, suggestion: str,
             **details) -> Finding:
    return Finding(
        url=url, check=CHECK, severity=sev,
        fingerprint=make_fingerprint(CHECK, "missing_from_sitemap", brand, url),
        issue=issue, location=url, snippet=url, suggestion=suggestion,
        details={"class": cls, **details})


def classify(url: str, brand: str, *, ok: bool, status, noindex: bool) -> Finding:
    """One finding for a page that's live in WP-REST but missing from the sitemap. Severity by
    the LOCKED D1 rules; ``noindex`` is the page's is_noindex (robots META or X-Robots-Tag header,
    both captured on the union fetch), so no separate HEAD refine is needed."""
    cruft = is_cruft(url)
    if not ok:  # in WP-REST but not reachable publicly -> integrity bug, called out separately
        if status is None:  # transport failure / timeout — we COULDN'T fetch it, not a real 404
            return _finding(
                url, brand, Severity.WARNING, "in WP-REST but could not be fetched (timeout/transport)",
                "rest_unreachable", "Indexed in WP-REST but the fetch failed (timeout/transport) — "
                "not a confirmed 404; re-check.", status=None, cruft=cruft)
        return _finding(
            url, brand, Severity.WARNING, f"in WP-REST but returns HTTP {status} publicly",
            "rest_404", "Integrity bug: indexed in WP-REST but not publicly reachable — "
            "cross-ref the broken-link rest_published set.", status=status, cruft=cruft)
    if cruft and not noindex:
        return _finding(
            url, brand, Severity.ERROR, "cruft page live, indexable, missing from sitemap",
            "cruft_indexable", "A cleanup/duplicate slug (-delete/-copy/-old) is live and "
            "indexable — should not exist. Remove or noindex.", noindex=False, cruft=True)
    if cruft:
        return _finding(
            url, brand, Severity.WARNING, "cruft page missing from sitemap (noindex mitigates)",
            "cruft_noindex", "A cleanup/duplicate slug that is noindexed — clean it up.",
            noindex=True, cruft=True)
    if not noindex:
        return _finding(
            url, brand, Severity.WARNING, "indexable page missing from sitemap",
            "indexable_unsitemapped", "Live and indexable but not in the sitemap — Google may "
            "never discover it. Add to the sitemap or confirm it's intentional.",
            noindex=False, cruft=False)
    return _finding(
        url, brand, Severity.INFO, "noindex page missing from sitemap",
        "noindex_unsitemapped", "Noindexed and unsitemapped (likely intentional) — inventory "
        "to confirm intent.", noindex=True, cruft=False)


def sitemap_unreachable(results, brand: str) -> list[Finding]:
    """Findings for SITEMAP pages that failed to fetch — in audit scope but unauditable. A page
    listed in the sitemap that 404s or won't load is exactly what a QA tool should catch; today
    they're silently dropped by the ``ok`` filter. Identity is fixed per (brand, url) regardless
    of the transient status, so a page flapping 404<->timeout keeps one identity in the diff."""
    out: list[Finding] = []
    for r in results:
        url = canonical_url(r.url)
        if r.status is None:  # transport/timeout — couldn't fetch (maybe transient)
            sev, issue, cls = (Severity.WARNING,
                               "sitemap page could not be fetched (timeout/transport)",
                               "sitemap_unreachable")
        else:  # a real HTTP error on a sitemapped page -> a dead link the sitemap advertises
            sev, issue, cls = (Severity.ERROR,
                               f"sitemap page returns HTTP {r.status}", "sitemap_dead")
        out.append(Finding(
            url=url, check=CHECK, severity=sev,
            fingerprint=make_fingerprint(CHECK, "sitemap_unreachable", brand, url),
            issue=issue, location=url, snippet=url,
            suggestion="Listed in the sitemap but not reachable — fix the page or remove it "
                       "from the sitemap.",
            details={"class": cls, "status": r.status}))
    return out


def from_audit(projections, failed, sitemap_set: set[str], brand: str) -> tuple[list[Finding], dict]:
    """Derive the enumeration findings from the ONE union fetch — no second crawl.

    Union scope means every live page (sitemap ∪ WP-REST) is already content-fetched, so a page
    that is live but NOT in the sitemap is exactly one whose canonical identity is absent from
    ``sitemap_set``. For those we already hold the parsed ``is_noindex`` (META or X-Robots-Tag)
    on the projection, so ``classify`` needs no HEAD probe. Fetched-OK projections classify by
    (cruft, noindex); pages that failed to fetch classify as the rest_404/rest_unreachable
    integrity bucket. Pages IN the sitemap are handled elsewhere (content audit + sitemap_dead)."""
    findings: list[Finding] = []
    for p in projections:
        if canonical_url(p.url) in sitemap_set:
            continue
        findings.append(classify(p.url, brand, ok=True, status=p.status, noindex=p.is_noindex))
    for r in failed:
        if canonical_url(r.url) in sitemap_set:
            continue  # a sitemapped page that failed -> sitemap_dead, not an enumeration finding
        findings.append(classify(canonical_url(r.url), brand, ok=False, status=r.status, noindex=False))

    stats: dict = {"missing_total": len(findings)}
    for f in findings:
        stats[f.details["class"]] = stats.get(f.details["class"], 0) + 1
    return findings, stats


async def reconcile(client, config, sitemap_urls: list[str]) -> dict:
    """Sitemap URL set vs WP-REST /wp/v2/pages. ``pages_missing_from_sitemap`` = live WP pages
    absent from the sitemap (the 845). ``rest_urls`` is the full live set (the diff uses it to
    tell a removed page from an unsitemapped one)."""
    wp_pages_only = config.wp_rest.model_copy(update={"post_types": ["pages"]})
    rest_urls, authed = await C.enumerate_wp_rest(
        client, wp_pages_only, max_retries=config.crawl.max_retries)
    rest_urls = C._apply_exclude(rest_urls, config.crawl.exclude)

    sitemap_set = {u.rstrip("/") for u in sitemap_urls}
    rest_set = {u.rstrip("/") for u in rest_urls}
    missing = sorted(rest_set - sitemap_set)
    return {
        "sitemap_total": len(sitemap_set),
        "wp_rest_pages_total": len(rest_set),
        "authed": authed,
        "pages_missing_from_sitemap": missing,
        "sitemap_only_count": len(sitemap_set - rest_set),
        "rest_urls": sorted(rest_set),
    }
