"""A duplicate title across 102 pages must not read as a single-page finding.

`_cross_page_duplicates` emitted `details={"count": N, "pages": [...]}`. The importer reads
`sources`, not `pages`, and falls back to `page_count=1` when it is absent — so **591 findings
spanning thousands of page-instances were stored as affecting one page each** (`meta` 347 of them,
`heading_structure` 244). A duplicate meta description across 102 pages would be ranked on one
page's traffic, and the collapse discipline the rest of the tool applies was invisible here.

The key name was the whole bug. `count` was right and simply never read.
"""
from __future__ import annotations

from auditor.audit import _cross_page_duplicates, PageProjection


def _pages(n, title="Same Title"):
    return [PageProjection(url=f"https://x.com/p{i}", title=title) for i in range(n)]


def test_a_duplicate_across_many_pages_reports_them_all():
    out = _cross_page_duplicates(_pages(102))
    dup = [f for f in out if "duplicate title" in f.issue][0]
    assert dup.details["page_count"] == 102


def test_the_sources_key_is_the_one_the_importer_reads():
    """`pages` was never read by anything. The importer looks for `sources`."""
    dup = [f for f in _cross_page_duplicates(_pages(102)) if "duplicate" in f.issue][0]
    assert "sources" in dup.details
    assert isinstance(dup.details["sources"], list) and dup.details["sources"]


def test_the_stored_sample_stays_capped_and_says_so():
    dup = [f for f in _cross_page_duplicates(_pages(102)) if "duplicate" in f.issue][0]
    assert len(dup.details["sources"]) <= 8
    assert dup.details["sources_truncated"] is True


def test_a_small_duplicate_keeps_every_page_and_is_not_marked_truncated():
    dup = [f for f in _cross_page_duplicates(_pages(3)) if "duplicate" in f.issue][0]
    assert dup.details["page_count"] == 3
    assert len(dup.details["sources"]) == 3
    assert dup.details["sources_truncated"] is False


def test_a_unique_title_produces_nothing():
    pages = [PageProjection(url=f"https://x.com/p{i}", title=f"Title {i}") for i in range(5)]
    assert not [f for f in _cross_page_duplicates(pages) if "duplicate title" in f.issue]
