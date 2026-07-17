"""Enumeration reconciliation ('the 845'): per-page findings for live-but-unsitemapped pages,
D1 severity (LOCKED), cruft regex gates the ERROR tier, and the indexable bucket is HEAD-refined
for X-Robots-Tag header noindex. Network faked.
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

INDEXABLE = "<html><head></head><body>x</body></html>"
NOINDEX = '<html><head><meta name="robots" content="noindex, follow"></head><body>x</body></html>'


def _cls(url, ok=True, status=200, html=INDEXABLE):
    return E.classify(url, BRAND, ok=ok, status=status, html=html)


def test_fingerprint_shape():
    f = _cls("https://x/p/")
    assert f.fingerprint == "enumeration:missing_from_sitemap:GL:https://x/p/"
    assert f.check == "enumeration"


def test_indexable_unsitemapped_is_warning():
    f = _cls("https://x/live-page/", html=INDEXABLE)
    assert f.severity is Severity.WARNING and f.details["class"] == "indexable_unsitemapped"


def test_noindex_unsitemapped_is_info():
    f = _cls("https://x/live-page/", html=NOINDEX)
    assert f.severity is Severity.INFO and f.details["class"] == "noindex_unsitemapped"


def test_cruft_indexable_is_error():
    f = _cls("https://x/landing-copy/", html=INDEXABLE)
    assert f.severity is Severity.ERROR and f.details["class"] == "cruft_indexable"


def test_cruft_noindex_is_warning():
    f = _cls("https://x/landing-copy/", html=NOINDEX)
    assert f.severity is Severity.WARNING and f.details["class"] == "cruft_noindex"


def test_rest_404_is_separate_warning():
    f = _cls("https://x/ghost/", ok=False, status=404, html="")
    assert f.severity is Severity.WARNING and f.details["class"] == "rest_404"
    assert f.details["status"] == 404


def test_rest_none_is_unreachable_not_404():
    # a transport failure (status=None) is "couldn't fetch", NOT a public 404 (wrong claim)
    f = _cls("https://x/timeout/", ok=False, status=None, html="")
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


# --- run(): fetch (faked) -> classify -> HEAD-refine the indexable bucket ---

class _Resp:
    def __init__(self, headers):
        self.headers = headers


class _Client:
    """HEAD returns the per-url X-Robots-Tag we program in."""
    def __init__(self, header_noindex: set[str]):
        self.header_noindex = header_noindex

    async def request(self, method, url, headers=None):
        xrt = "noindex" if url in self.header_noindex else ""
        return _Resp({"x-robots-tag": xrt})


def test_run_head_reclassifies_header_only_noindex(monkeypatch):
    # /header-noindex/ is indexable by META but noindex by HEADER -> must be reclassified to
    # noindex_unsitemapped (INFO), NOT left in the indexable WARNING bucket (Syed's catch).
    missing = ["https://x/header-noindex/", "https://x/truly-indexable/"]

    async def fake_fetch(client, urls, crawl):
        return [FetchResult(url=u, status=200, final_url=u, text=INDEXABLE, error=None)
                for u in urls]

    monkeypatch.setattr(E.C, "fetch_pages", fake_fetch)
    client = _Client(header_noindex={"https://x/header-noindex/"})
    findings, stats = asyncio.run(E.run(client, CFG, missing))

    by_url = {f.url: f for f in findings}
    assert by_url["https://x/header-noindex/"].severity is Severity.INFO
    assert by_url["https://x/header-noindex/"].details["class"] == "noindex_unsitemapped"
    assert by_url["https://x/truly-indexable/"].severity is Severity.WARNING
    assert stats["head_reclassified_noindex"] == 1
    assert stats["indexable_unsitemapped"] == 1  # only the truly-indexable one remains


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
