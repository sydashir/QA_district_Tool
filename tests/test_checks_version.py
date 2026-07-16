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


def test_canon_value_change_invalidates():
    # Post-P1 the phone ruler is the NAP value-set. Changing it moves the version.
    c = load_brand("gl")
    assert c.canon is not None  # GL classifies against the snapshot
    v1 = cv.version(c)
    c.canon.stale_retired = [*c.canon.stale_retired, "+18885550000"]
    assert cv.version(c) != v1


def test_legacy_canonical_change_invalidates_without_canon():
    # With no NAP canon, the flat canonical_phones list IS the ruler and still invalidates.
    c = load_brand("gl")
    c.canon = None
    v1 = cv.version(c)
    c.canonical_phones = [*c.canonical_phones, "800-000-0000"]
    assert cv.version(c) != v1


def test_identical_numbers_different_source_is_noop():
    # The durability guarantee: an identical-numbers snapshot->live swap hashes the VALUES,
    # not the source, so the version does NOT move (no baseline churn). Proven by version
    # equality between the canon path and a flat list of the same numbers.
    c = load_brand("gl")
    v_canon = cv.version(c)
    same_numbers = sorted(c.canon.current_set() | set(c.canon.stale_retired))
    c.canon = None
    c.canonical_phones = same_numbers  # "live" source, identical values
    assert cv.version(c) == v_canon


def test_title_bounds_change_invalidates_only_meta():
    # P4: a per-brand title-bound tune moves ONLY the title_bounds config component (scoped
    # to meta in diff.py), not any check source -> no cross-brand, no cross-check churn.
    c = load_brand("gl")
    comp1 = cv.components(c)
    v1 = cv.version(c)
    c.thresholds.title_max = 100
    assert cv.version(c) != v1
    assert cv.changed_components(comp1, cv.components(c)) == ["title_bounds"]


def test_gl_config_carries_approved_title_max():
    assert load_brand("gl").thresholds.title_max == 88  # P4 approved GL ruler


def test_components_are_debuggable():
    c = load_brand("gl")
    comp = cv.components(c)
    assert any(k.startswith("src:") for k in comp) and "canonical_phones" in comp
    other = dict(comp)
    other["src:meta.py"] = "deadbeef0000"
    assert cv.changed_components(comp, other) == ["src:meta.py"]
