"""Static URL list as the enumeration source of last resort.

DBH was replatformed to headless WordPress behind Next.js on 2026-08-08. Sitemap, robots.txt and
WP-REST all return 404, so without this the parent brand is simply un-auditable.

The limitation is permanent and deliberate: a static list CANNOT DISCOVER PAGES ADDED LATER.
"""
from __future__ import annotations

import asyncio

import pytest

from auditor import crawl as C


class _Crawl:
    max_concurrency = 2
    delay_seconds = 0.0
    timeout_seconds = 5.0
    max_retries = 1
    exclude = ["/wp-admin", "?"]
    user_agent = "test"


class _Cfg:
    def __init__(self, urls_file=None, wp=True):
        self.brand = "DBH"
        self.base_url = "https://districtbehavioralhealth.com"
        self.sitemap_url = "https://districtbehavioralhealth.com/sitemap_index.xml"
        self.crawl = _Crawl()
        self.urls_file = urls_file
        self.wp_rest = type("W", (), {"enabled": wp, "post_types": ["pages"]})() if wp else None


@pytest.fixture
def _dead_sources(monkeypatch):
    """Sitemap 404s and WP-REST returns nothing — the real post-replatform state."""
    async def no_sitemap(*a, **k):
        return [], False, 0, [{"url": "x", "status": 404}]

    async def no_rest(*a, **k):
        return [], False

    monkeypatch.setattr(C, "enumerate_sitemap", no_sitemap)
    monkeypatch.setattr(C, "enumerate_wp_rest", no_rest)


def test_the_static_list_is_used_when_nothing_else_works(tmp_path, _dead_sources):
    f = tmp_path / "dbh.txt"
    f.write_text("# a comment line is ignored\n"
                 "https://districtbehavioralhealth.com/a\n"
                 "\n"
                 "https://districtbehavioralhealth.com/b\n")
    urls, method, meta = asyncio.run(C.enumerate_pages(None, _Cfg(urls_file=str(f))))
    assert method == "urls-file"
    assert urls == ["https://districtbehavioralhealth.com/a",
                    "https://districtbehavioralhealth.com/b"]
    # the report must carry the limitation, not just the URLs
    assert meta["cannot_discover_new_pages"] is True


def test_an_empty_wp_rest_does_not_block_the_fallback(tmp_path, _dead_sources):
    """The bug this was written against: WP-REST returned an empty set and RETURNED, making the
    fallback unreachable for exactly the brand it exists for."""
    f = tmp_path / "dbh.txt"
    f.write_text("https://districtbehavioralhealth.com/a\n")
    urls, method, _ = asyncio.run(C.enumerate_pages(None, _Cfg(urls_file=str(f), wp=True)))
    assert method == "urls-file" and len(urls) == 1


def test_brands_without_a_list_are_unchanged(_dead_sources):
    urls, method, _ = asyncio.run(C.enumerate_pages(None, _Cfg(urls_file=None)))
    assert urls == [] and method == "wp-rest"


def test_exclude_rules_still_apply_to_the_list(tmp_path, _dead_sources):
    f = tmp_path / "dbh.txt"
    f.write_text("https://districtbehavioralhealth.com/ok\n"
                 "https://districtbehavioralhealth.com/wp-admin/x\n")
    urls, _m, _meta = asyncio.run(C.enumerate_pages(None, _Cfg(urls_file=str(f))))
    assert urls == ["https://districtbehavioralhealth.com/ok"]


def test_a_missing_list_fails_loudly_rather_than_silently_empty(tmp_path, _dead_sources):
    urls, method, meta = asyncio.run(C.enumerate_pages(None, _Cfg(urls_file=str(tmp_path / "nope.txt"))))
    assert urls == [] and method == "none" and "unreadable" in meta["reason"]
