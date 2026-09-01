"""A broken link's page count must be the number of pages it is ON, not the number we stored.

The bug, measured on live data before the fix: **all 1,247 open `broken_links` findings had
`page_count = 5`**, and not one carried an explicit count. `links.py` truncated `sources` to five
for storage, `_finding` never set a count, and `server/importer.py:145` falls back to
`len(sources)`. So the count WAS the truncation.

GL's footer Facebook link is on all 3,353 pages of the site and was stored as affecting five.

The trap that makes this worse than a wrong number: because `page_count` equals the stored source
count by construction, any completeness test of the form `len(sources) >= page_count` reports
"complete". A reach figure built on it would be stamped `measured` — confidently wrong, on check #1
of the client mandate.
"""
from __future__ import annotations

from auditor.checks.links import _finding, SOURCE_SAMPLE
from auditor.report import Severity


def _f(n_sources: int):
    sources = [f"https://x/p{i}/" for i in range(n_sources)]
    return _finding("https://x/target", sources, None, Severity.ERROR, "broken", "fix it", "broken")


def test_the_count_is_the_real_number_of_pages_not_the_stored_sample():
    f = _f(3353)
    assert f.details["page_count"] == 3353


def test_the_stored_sample_stays_capped():
    """Storing 3,353 URLs per finding is a storage problem traded for a correctness one. The cap
    stays; the COUNT stops lying about it."""
    f = _f(3353)
    assert len(f.details["sources"]) == SOURCE_SAMPLE


def test_a_finding_says_its_source_list_is_incomplete():
    """Reach must be able to tell 'we know all the pages' from 'we sampled some'. Without this flag
    the two are indistinguishable, which is exactly how the old count passed as complete."""
    assert _f(3353).details["sources_truncated"] is True
    assert _f(2).details["sources_truncated"] is False


def test_a_small_finding_keeps_every_source_and_an_honest_count():
    f = _f(2)
    assert f.details["page_count"] == 2
    assert len(f.details["sources"]) == 2


def test_the_completeness_test_that_used_to_pass_now_fails_correctly():
    """`len(sources) >= page_count` was the trap: it read 'complete' on every truncated finding.
    It must now be false exactly when the list really is a sample."""
    big, small = _f(3353), _f(2)
    assert not (len(big.details["sources"]) >= big.details["page_count"])
    assert len(small.details["sources"]) >= small.details["page_count"]


def test_one_source_is_still_one_page():
    f = _f(1)
    assert f.details["page_count"] == 1 and f.details["sources_truncated"] is False
