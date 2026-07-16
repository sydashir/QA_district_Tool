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

Noindex is read from the robots META tag, then the INDEXABLE bucket is HEAD-probed for an
``X-Robots-Tag`` header (FetchResult drops headers): a header-only noindex would otherwise
falsely inflate the one bucket we tell the client is worth attention.
"""
from __future__ import annotations

import asyncio
import re

import httpx
from bs4 import BeautifulSoup

from .. import crawl as C
from ..report import Finding, Severity, make_fingerprint

CHECK = "enumeration"

# Cruft = WP cleanup/duplicate slug markers. A trailing path segment ENDING in delete/copy/old
# (+ optional -N dupe suffix): /page-copy/, /thing-old, /x-delete-2/. Conservative — anchored to
# a leading -/ and the end — to keep the cruft+indexable ERROR precise (no 'goldman' false hits).
CRUFT_RE = re.compile(r"[-/](?:delete|copy|old)(?:-\d+)?/?$", re.IGNORECASE)
_NOINDEX_RE = re.compile(r"noindex", re.IGNORECASE)


def is_cruft(url: str) -> bool:
    return bool(CRUFT_RE.search(url))


def _meta_noindex(html: str) -> bool:
    if not html:
        return False
    soup = BeautifulSoup(html, "lxml")
    m = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
    return bool(m and _NOINDEX_RE.search(m.get("content") or ""))


def _finding(url: str, brand: str, sev: Severity, issue: str, cls: str, suggestion: str,
             **details) -> Finding:
    return Finding(
        url=url, check=CHECK, severity=sev,
        fingerprint=make_fingerprint(CHECK, "missing_from_sitemap", brand, url),
        issue=issue, location=url, snippet=url, suggestion=suggestion,
        details={"class": cls, **details})


def classify(url: str, brand: str, *, ok: bool, status, html: str) -> Finding:
    """One finding for a page that's live in WP-REST but missing from the sitemap. Severity by
    the LOCKED D1 rules; noindex here is META-only (the indexable set is HEAD-refined in run())."""
    cruft = is_cruft(url)
    if not ok:  # in WP-REST but not reachable publicly -> integrity bug, called out separately
        return _finding(
            url, brand, Severity.WARNING, f"in WP-REST but returns HTTP {status} publicly",
            "rest_404", "Integrity bug: indexed in WP-REST but not publicly reachable — "
            "cross-ref the broken-link rest_published set.", status=status, cruft=cruft)
    noindex = _meta_noindex(html)
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


async def _head_noindex(client, url: str) -> bool:
    """HEAD a URL and report whether its X-Robots-Tag header carries noindex."""
    try:
        r = await client.request("HEAD", url)
        return bool(_NOINDEX_RE.search(r.headers.get("x-robots-tag", "") or ""))
    except (httpx.TimeoutException, httpx.TransportError):
        return False


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


async def run(client, config, missing_urls: list[str], probe_cap: int | None = None):
    """Fetch the missing pages (robots-only; NO audit checks — they're out of audit scope),
    classify each, then HEAD-refine the indexable set for header noindex. Returns
    (findings, stats). ``probe_cap`` limits the fetch for smokes; None = all (baseline)."""
    urls = missing_urls if probe_cap is None else missing_urls[:probe_cap]
    results = await C.fetch_pages(client, urls, config.crawl)
    findings = [classify(r.url, config.brand, ok=r.ok, status=r.status, html=r.text)
                for r in results]

    # HEAD-refine ONLY the indexable+unsitemapped bucket: the meta tag misses header-only
    # noindex, which would otherwise falsely inflate the exact WARNING set we flag to the client.
    idx = [f for f in findings if f.details.get("class") == "indexable_unsitemapped"]
    reclassified = 0
    if idx:
        sem = asyncio.Semaphore(config.crawl.max_concurrency)

        async def refine(f: Finding) -> None:
            nonlocal reclassified
            async with sem:
                await asyncio.sleep(config.crawl.delay_seconds)
                if await _head_noindex(client, f.url):
                    f.severity = Severity.INFO
                    f.issue = "noindex page missing from sitemap (X-Robots-Tag header)"
                    f.suggestion = ("Noindexed via X-Robots-Tag header (not the meta tag) and "
                                    "unsitemapped — likely intentional; inventory to confirm.")
                    f.details.update(class_="noindex_unsitemapped", noindex="header")
                    f.details["class"] = "noindex_unsitemapped"
                    reclassified += 1

        await asyncio.gather(*(refine(f) for f in idx))

    stats: dict = {"missing_total": len(missing_urls), "probed": len(urls),
                   "head_refined": len(idx), "head_reclassified_noindex": reclassified}
    for f in findings:
        stats[f.details["class"]] = stats.get(f.details["class"], 0) + 1
    return findings, stats
