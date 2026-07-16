"""Derived check-version: fully derived from check source + check-relevant config.

Conditions (Syed): crawl timing must NOT invalidate; any check LOGIC edit must (no
manual bump — the Check #2 trap); and invalidation is debuggable (which component moved).
"""
from __future__ import annotations

from auditor import checks_version as cv
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


def test_check_source_edit_invalidates(tmp_path):
    # any logic/threshold/comment edit to a check module moves the version, no manual bump
    (tmp_path / "a.py").write_text("X = 1\n")
    c = load_brand("gl")
    v1 = cv.version(c, checks_dir=tmp_path)
    (tmp_path / "a.py").write_text("X = 2  # logic changed\n")
    assert cv.version(c, checks_dir=tmp_path) != v1


def test_config_canonical_change_invalidates():
    c = load_brand("gl")
    v1 = cv.version(c)
    c.canonical_phones = [*c.canonical_phones, "800-000-0000"]
    assert cv.version(c) != v1


def test_components_are_debuggable():
    c = load_brand("gl")
    comp = cv.components(c)
    assert any(k.startswith("src:") for k in comp) and "canonical_phones" in comp
    other = dict(comp)
    other["src:meta.py"] = "deadbeef0000"
    assert cv.changed_components(comp, other) == ["src:meta.py"]
