"""Failure semantics for the per-brand stylesheet cache.

A partial CSS read must never masquerade as a clean one: without every stylesheet we cannot know
what a page hides, so visible_text may still carry text no reader sees. Same discipline as
withholding coverage findings on a partial sitemap read.
"""
from __future__ import annotations

import asyncio

import pytest

from auditor.css_cache import BrandCSS

PAGE = ('<html><head>'
        '<link rel="stylesheet" href="/a.css">'
        '<link rel="stylesheet" href="/b.css">'
        '<link rel="stylesheet" href="https://cdn.example.com/x.css">'   # other host: ignored
        '<link rel="preload" href="/c.css">'                             # not a stylesheet
        '</head><body>hi</body></html>')
URL = "https://brand.test/page/"


class _Client:
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls = []


async def _req(client, url, max_retries=1):
    client.calls.append(url)
    if url in client.fail:
        return 404, url, "", None, {}
    return 200, url, f"/* {url} */ .x{{display:none}}", None, {}


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    from auditor import crawl as C
    monkeypatch.setattr(C, "_request", _req)


def _load(fail=()):
    c, cl = BrandCSS(), _Client(fail)
    asyncio.run(c.load(cl, PAGE, URL))
    return c, cl


def test_same_host_only_and_stylesheet_rel_only():
    c, cl = _load()
    assert cl.calls == ["https://brand.test/a.css", "https://brand.test/b.css"]
    assert c.status == "ok" and c.trustworthy


def test_one_failed_sheet_is_partial_not_ok():
    c, _ = _load(fail={"https://brand.test/b.css"})
    assert c.status == "partial" and not c.trustworthy
    assert c.sheets_fetched == 1 and c.sheets_found == 2
    assert c.css, "the sheet that DID load must still be used"


def test_all_failed_is_unavailable():
    c, _ = _load(fail={"https://brand.test/a.css", "https://brand.test/b.css"})
    assert c.status == "unavailable" and not c.trustworthy


def test_no_stylesheets_is_none_and_trustworthy():
    # Gratitude Lodge links zero stylesheets — all its CSS is inlined. Nothing is missing, so this
    # must NOT be treated as a degraded read.
    c, cl = BrandCSS(), _Client()
    asyncio.run(c.load(cl, "<html><head></head><body>hi</body></html>", URL))
    assert c.status == "none" and c.trustworthy and cl.calls == []


def test_load_is_idempotent():
    c, cl = _load()
    asyncio.run(c.load(cl, PAGE, URL))
    assert len(cl.calls) == 2, "a second load must not re-fetch — one fetch per brand, not per page"


def test_truncation_downgrades_to_partial(monkeypatch):
    monkeypatch.setattr("auditor.css_cache.MAX_SHEETS", 1)
    c, _ = _load()
    assert c.status == "partial", "a silently truncated read must not report ok"
