"""A brand whose audit covers part of its site must say so, in the report, unprompted.

AR is the case. Five consecutive runs enumerated 4 sitemap URLs and reached 474 pages only because
WP-REST supplied 470; its sitemap advertises ~10,746 more under `/city-data/`. Those are duplicate
doorway pages — every URL carries the WordPress `-2` collision suffix and every sampled one redirects
to the homepage — so auditing 11,000 copies of one page adds nothing (Syed, 2026-09-02).

But a report that silently covers 4% of a site reads as a report on the site. The scope has to be
stated, and the excluded population has to be a stated finding rather than silence.
"""
from __future__ import annotations

from datetime import datetime, timezone

from scripts.client_report import SCOPE_NOTES, render


def _run(pages=474):
    return (1, datetime(2026, 9, 2, tzinfo=timezone.utc), pages, False, "Alliance Recovery",
            "https://alliancerecovery.com")


def _finding():
    return ("phone", "unknown", "warning", "unknown phone number", "https://alliancerecovery.com/p/",
            "snippet", "fix it", 1, None, None)


def test_ar_has_a_scope_note():
    assert "AR" in SCOPE_NOTES


def test_the_report_states_the_partial_scope():
    out = render("AR", _run(), [_finding()])
    assert "474" in out and "11,000" in out.replace("11,220", "11,000")


def test_the_excluded_population_is_described_not_hidden():
    out = render("AR", _run(), [_finding()])
    low = out.lower()
    assert "city-data" in low
    assert "duplicate" in low or "doorway" in low


def test_the_note_carries_its_evidence():
    """A scope claim without evidence is just an assertion that will rot. The -2 suffix and the
    redirect-to-homepage are what make it checkable."""
    note = SCOPE_NOTES["AR"]
    assert "-2" in note
    assert "redirect" in note.lower()
    assert "2026" in note          # dated, so a reader knows when it was true


def test_a_brand_without_a_note_renders_normally():
    out = render("TDRC", (1, datetime(2026, 9, 2, tzinfo=timezone.utc), 19, False,
                          "The District", "https://x/"), [_finding()])
    assert "city-data" not in out.lower()


def test_the_note_appears_with_the_limits_not_among_the_findings():
    """It is a statement about coverage, not a defect on a page. Putting it in the findings list
    would make it look like something to fix."""
    out = render("AR", _run(), [_finding()])
    limits = out.split("What this audit cannot see")[1]
    assert "city-data" in limits.lower()
