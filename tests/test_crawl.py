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
