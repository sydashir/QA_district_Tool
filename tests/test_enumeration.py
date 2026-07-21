"""Enumeration reconciliation ('the 845'): per-page findings for live-but-unsitemapped pages,
D1 severity (LOCKED), cruft regex gates the ERROR tier. Under union scope these are DERIVED from
the one content fetch (``from_audit``): noindex is the projection's is_noindex (robots META OR
X-Robots-Tag header, both captured on the fetch), so there is no separate HEAD refine. Network
faked.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from auditor.checks import enumeration as E
from auditor.crawl import FetchResult
from auditor.report import Severity

BRAND = "GL"
CRAWL = SimpleNamespace(max_concurrency=3, delay_seconds=0.0, max_retries=1, exclude=[])
CFG = SimpleNamespace(brand=BRAND, crawl=CRAWL)


def _cls(url, ok=True, status=200, noindex=False):
    return E.classify(url, BRAND, ok=ok, status=status, noindex=noindex)


def test_fingerprint_shape():
    f = _cls("https://x/p/")
    assert f.fingerprint == "enumeration:missing_from_sitemap:GL:https://x/p/"
    assert f.check == "enumeration"


def test_indexable_unsitemapped_is_warning():
    f = _cls("https://x/live-page/", noindex=False)
    assert f.severity is Severity.WARNING and f.details["class"] == "indexable_unsitemapped"


def test_noindex_unsitemapped_is_info():
    f = _cls("https://x/live-page/", noindex=True)
    assert f.severity is Severity.INFO and f.details["class"] == "noindex_unsitemapped"


def test_cruft_indexable_is_error():
    f = _cls("https://x/landing-copy/", noindex=False)
    assert f.severity is Severity.ERROR and f.details["class"] == "cruft_indexable"


def test_cruft_noindex_is_warning():
    f = _cls("https://x/landing-copy/", noindex=True)
    assert f.severity is Severity.WARNING and f.details["class"] == "cruft_noindex"


def test_rest_404_is_separate_warning():
    f = _cls("https://x/ghost/", ok=False, status=404)
    assert f.severity is Severity.WARNING and f.details["class"] == "rest_404"
    assert f.details["status"] == 404


def test_rest_none_is_unreachable_not_404():
    # a transport failure (status=None) is "couldn't fetch", NOT a public 404 (wrong claim)
    f = _cls("https://x/timeout/", ok=False, status=None)
    assert f.details["class"] == "rest_unreachable" and "could not be fetched" in f.issue


def test_sitemap_unreachable_findings():
    results = [
        FetchResult(url="https://x/dead/", status=404, final_url="https://x/dead/", text="", error=None),
        FetchResult(url="https://x/timeout/", status=None, final_url=None, text="", error="Timeout"),
    ]
    by = {f.url: f for f in E.sitemap_unreachable(results, "GL")}
    assert by["https://x/dead"].severity is Severity.ERROR
    assert by["https://x/dead"].details["class"] == "sitemap_dead"
    assert by["https://x/dead"].fingerprint == "enumeration:sitemap_unreachable:GL:https://x/dead"
    assert by["https://x/timeout"].severity is Severity.WARNING
    assert by["https://x/timeout"].details["class"] == "sitemap_unreachable"


@pytest.mark.parametrize("url,cruft", [
    ("https://x/page-copy/", True),
    ("https://x/page-delete/", True),
    ("https://x/thing-old", True),
    ("https://x/landing-copy-2/", True),   # WP dupe -N suffix
    ("https://x/goldman-rehab/", False),   # 'old' inside a word -> NOT cruft
    ("https://x/bold-choices/", False),
    ("https://x/normal-page/", False),
])
def test_cruft_regex(url, cruft):
    assert E.is_cruft(url) is cruft


# --- from_audit(): derive enumeration findings from the ONE union fetch (no second crawl) ---

def _proj(url, is_noindex=False, status=200):
    """Duck-typed PageProjection: from_audit only reads url/status/is_noindex."""
    return SimpleNamespace(url=url, status=status, is_noindex=is_noindex)


def test_from_audit_derives_enumeration_from_projections():
    # /header-noindex is indexable by META but noindex by HEADER; under union scope that header
    # noindex is folded into is_noindex on the ONE fetch, so it classifies noindex_unsitemapped
    # (INFO) with no separate HEAD probe. A page already in the sitemap is not an enumeration
    # finding; a REST-only page that FAILED to fetch is the rest_404 integrity bucket.
    projections = [
        _proj("https://x/header-noindex", is_noindex=True),    # noindex (meta OR header) -> INFO
        _proj("https://x/truly-indexable", is_noindex=False),  # indexable -> WARNING
        _proj("https://x/in-sitemap", is_noindex=False),       # in sitemap -> not an enum finding
    ]
    failed = [FetchResult(url="https://x/ghost/", status=404, final_url=None, text="", error=None)]
    sitemap_set = {"https://x/in-sitemap"}
    findings, stats = E.from_audit(projections, failed, sitemap_set, BRAND)

    by_url = {f.url: f for f in findings}
    assert "https://x/in-sitemap" not in by_url  # sitemapped -> handled by the content audit, not here
    assert by_url["https://x/header-noindex"].severity is Severity.INFO
    assert by_url["https://x/header-noindex"].details["class"] == "noindex_unsitemapped"
    assert by_url["https://x/truly-indexable"].severity is Severity.WARNING
    assert by_url["https://x/truly-indexable"].details["class"] == "indexable_unsitemapped"
    assert by_url["https://x/ghost"].details["class"] == "rest_404"
    assert stats["missing_total"] == 3
    assert stats["indexable_unsitemapped"] == 1 and stats["noindex_unsitemapped"] == 1


def test_from_audit_skips_sitemapped_failures():
    # a page that IS in the sitemap but failed to fetch is NOT an enumeration finding here — it's
    # sitemap_dead (handled by sitemap_unreachable). from_audit must ignore it to avoid double-flag.
    failed = [FetchResult(url="https://x/dead/", status=404, final_url=None, text="", error=None)]
    findings, stats = E.from_audit([], failed, {"https://x/dead"}, BRAND)
    assert findings == [] and stats["missing_total"] == 0


def test_reconcile_missing_is_rest_minus_sitemap(monkeypatch):
    async def fake_rest(client, cfg, max_retries=1):
        return (["https://x/a/", "https://x/b/", "https://x/c/"], True)

    monkeypatch.setattr(E.C, "enumerate_wp_rest", fake_rest)
    monkeypatch.setattr(E.C, "_apply_exclude", lambda urls, ex: urls)
    cfg = SimpleNamespace(brand=BRAND, crawl=CRAWL,
                          wp_rest=SimpleNamespace(model_copy=lambda update: None))
    recon = asyncio.run(E.reconcile(object(), cfg, ["https://x/a/"]))
    # reconcile normalizes with rstrip('/') before diffing
    assert recon["pages_missing_from_sitemap"] == ["https://x/b", "https://x/c"]
    assert recon["wp_rest_pages_total"] == 3
