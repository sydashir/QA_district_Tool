"""Describing WHICH pages a template finding is on, without storing every URL.

A footer defect on 3,353 pages does not need 3,353 stored URLs — but the eight we do store are not
enough to compute traffic reach, and they are misleading on their own: GL's `dead_cta "View All"`
claims 1,343 pages and its eight stored sources all sit under one path, so a reader would conclude
the defect is confined there.

A pattern is only worth storing if it does not OVER-claim. `host/*` covering 1,343 of 3,353 pages
would hand reach 2,010 pages that do not have the defect — and inventing traffic is worse than
having none. So the pattern is recorded ONLY when every audited page matching it really does carry
the finding; otherwise the honest output is the true count plus a capped sample.
"""
from __future__ import annotations

from auditor.audit import _source_pattern


def test_a_site_wide_defect_is_described_as_the_whole_site():
    audited = [f"https://x.com/p{i}" for i in range(50)]
    pat = _source_pattern(audited, audited)
    assert pat == "https://x.com/*"


def test_a_defect_confined_to_one_section_names_that_section():
    audited = ([f"https://x.com/drug-rehab/a{i}" for i in range(20)]
               + [f"https://x.com/blog/b{i}" for i in range(20)])
    sources = [u for u in audited if "/drug-rehab/" in u]
    assert _source_pattern(sources, audited) == "https://x.com/drug-rehab/*"


def test_a_pattern_that_would_over_claim_is_refused():
    """The important one. Sources spread across two sections with no common prefix would collapse to
    `host/*`, which claims pages that do not have the defect. Better no pattern than a wrong one."""
    audited = ([f"https://x.com/a/p{i}" for i in range(20)]
               + [f"https://x.com/b/p{i}" for i in range(20)])
    sources = ([f"https://x.com/a/p{i}" for i in range(20)]
               + [f"https://x.com/b/p{i}" for i in range(5)])     # only 5 of b/
    assert _source_pattern(sources, audited) is None


def test_a_partial_section_is_refused_too():
    audited = [f"https://x.com/drug-rehab/a{i}" for i in range(20)]
    assert _source_pattern(audited[:10], audited) is None


def test_one_source_gets_no_pattern():
    audited = [f"https://x.com/p{i}" for i in range(10)]
    assert _source_pattern(audited[:1], audited) is None


def test_an_empty_audited_set_is_handled():
    assert _source_pattern(["https://x.com/a"], []) is None
