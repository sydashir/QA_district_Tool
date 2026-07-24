"""P5: the page cache hash must be stable across Cloudflare cfemail rotation.

Raw-HTML hashing marks every page "changed" every run because Cloudflare rotates
data-cfemail / /cdn-cgi/l/email-protection hex per request (the M0 finding). The
cache/diff must hash the normalized markup instead.
"""
from __future__ import annotations

from auditor import crawl


# Same page, two fetches — differ ONLY in Cloudflare's rotating email obfuscation.
_A = ('<html><body><h1>Hi</h1><p>real body</p>'
      '<a href="/x" data-cfemail="111111">e</a>'
      '<a href="/cdn-cgi/l/email-protection#aaaaaa">m</a></body></html>')
_B = ('<html><body><h1>Hi</h1><p>real body</p>'
      '<a href="/x" data-cfemail="222222">e</a>'
      '<a href="/cdn-cgi/l/email-protection#bbbbbb">m</a></body></html>')
# A genuine content change.
_C = ('<html><body><h1>Hi</h1><p>DIFFERENT body</p>'
      '<a href="/x" data-cfemail="111111">e</a>'
      '<a href="/cdn-cgi/l/email-protection#aaaaaa">m</a></body></html>')


def test_page_hash_ignores_cfemail_rotation():
    assert crawl.page_hash(_A) == crawl.page_hash(_B)
    # and the raw hash would NOT be stable (proves the fix is doing work)
    assert crawl.content_hash(_A) != crawl.content_hash(_B)


def test_page_hash_detects_real_change():
    assert crawl.page_hash(_A) != crawl.page_hash(_C)


def test_enumerate_sitemap_records_failed_children_no_silent_drop():
    # A throttled / 5xx child sitemap must be RECORDED in failed_sitemaps, not silently dropped —
    # the silent drop is exactly what made MHD's 15,635-URL sitemap read as 96 and produced a bogus
    # "~1% coverage" finding. A bare 500 (not a CF-branded 429/503) is the pointed case: it leaves
    # blocked=False, so before this fix it vanished with zero trace.
    import asyncio
    INDEX = "https://x/sitemap_index.xml"
    C1, C2 = "https://x/page-sitemap1.xml", "https://x/page-sitemap2.xml"
    bodies = {
        INDEX: (200, f"<sitemapindex><sitemap><loc>{C1}</loc></sitemap>"
                     f"<sitemap><loc>{C2}</loc></sitemap></sitemapindex>"),
        C1: (200, "<urlset><url><loc>https://x/a/</loc></url>"
                  "<url><loc>https://x/b/</loc></url></urlset>"),
        C2: (500, "boom"),  # a bare 500 -> previously dropped SILENTLY, blocked stays False
    }

    class _Resp:
        def __init__(self, code, url, text):
            self.status_code, self.url, self.text, self.headers = code, url, text, {}

    class _Client:
        async def request(self, method, url, headers=None):
            code, text = bodies[url]
            return _Resp(code, url, text)

    urls, blocked, child_count, failed = asyncio.run(
        crawl.enumerate_sitemap(_Client(), INDEX, max_retries=0))
    assert urls == ["https://x/a/", "https://x/b/"]                     # the good child's URLs survive
    assert blocked is False                                             # a bare 500 is not a CF block
    assert len(failed) == 1                                             # ...but the failure is RECORDED
    assert failed[0]["url"] == C2 and failed[0]["status"] == 500


def test_fetch_pages_progress_callback_fires_per_fetch():
    # long-run progress: on_done(completed, total) fires once per fetch, ending at (N, N).
    import asyncio
    from types import SimpleNamespace
    from auditor.crawl import fetch_pages

    class _Resp:
        def __init__(self, u):
            self.status_code, self.url, self.text, self.headers = 200, u, "<html></html>", {}

    class _Client:
        async def request(self, method, url, headers=None):
            return _Resp(url)

    crawl_cfg = SimpleNamespace(max_concurrency=3, delay_seconds=0.0, max_retries=0)
    urls = [f"https://x/{i}" for i in range(5)]
    calls = []
    asyncio.run(fetch_pages(_Client(), urls, crawl_cfg, on_done=lambda d, t: calls.append((d, t))))
    assert len(calls) == 5
    assert calls[-1] == (5, 5)
    assert {t for _, t in calls} == {5}
