"""Derived check-version: hashes CHECK-relevant config only, and is debuggable.

Two conditions (Syed): crawl delay/concurrency changing must NOT invalidate findings;
and on invalidation we can log WHICH component changed (no mystery recomputes).
"""
from __future__ import annotations

from auditor import checks_version as cv
from auditor.checks import meta
from auditor.config import load_brand


def test_version_stable():
    c = load_brand("gl")
    assert cv.version(c) == cv.version(c)


def test_crawl_timing_does_not_invalidate():
    c = load_brand("gl")
    v1 = cv.version(c)
    c.crawl.delay_seconds = 99.0
    c.crawl.max_concurrency = 1
    c.crawl.timeout_seconds = 999.0
    assert cv.version(c) == v1  # crawl timing is not check-relevant


def test_threshold_change_invalidates(monkeypatch):
    c = load_brand("gl")
    v1 = cv.version(c)
    monkeypatch.setattr(meta, "TITLE_MAX", 88)  # e.g. the coming P4 change
    assert cv.version(c) != v1


def test_components_are_debuggable():
    c = load_brand("gl")
    comp = cv.components(c)
    assert "meta.title_bounds" in comp
    other = dict(comp)
    other["meta.title_bounds"] = [15, 88]
    assert cv.changed_components(comp, other) == ["meta.title_bounds"]
