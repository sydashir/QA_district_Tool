"""7,777 pages did not stop existing at the same moment. That is one fact about our run.

RR run 119 attempted 8,029 pages, 77 responded, and `sitemap_unreachable` minted **7,777 findings** —
a report telling the client 97% of their site was unreachable. It was our throttling.

`crawl_verdict` now refuses such a run before it can be imported, so this is the second line: when
the failure RATE is high the honest output is ONE statement about the crawl, not one finding per
page. The same collapse discipline already applied to template defects and colour pairs.

The threshold is not a guess. Across 106 historical runs the median fetch success rate is 99.7% and
legitimate lows are COC 58.7%, MHD 69%, DBH 75% — brands with genuinely dead sitemap entries, whose
per-page findings are real and must survive.
"""
from __future__ import annotations

from types import SimpleNamespace

from auditor.checks.enumeration import sitemap_unreachable


def _results(n, status=None):
    return [SimpleNamespace(url=f"https://x.com/p{i}", status=status) for i in range(n)]


def test_a_handful_of_dead_pages_stays_per_page():
    """COC really does have ~182 dead sitemap entries out of 484. Those are the client's problem and
    each one is actionable, so they must not be collapsed away."""
    out = sitemap_unreachable(_results(20, status=404), "COC", attempted=484)
    assert len(out) == 20
    assert all(f.details["class"] == "sitemap_dead" for f in out)


def test_mass_failure_collapses_to_one_finding():
    out = sitemap_unreachable(_results(7777), "RR", attempted=8029)
    assert len(out) == 1
    assert out[0].details["class"] == "crawl_incomplete"


def test_the_collapsed_finding_blames_the_crawl_not_the_site():
    out = sitemap_unreachable(_results(7777), "RR", attempted=8029)
    text = f"{out[0].issue} {out[0].suggestion}".lower()
    assert "7,777" in f"{out[0].issue} {out[0].suggestion}"
    assert "our" in text or "we " in text
    assert "unreachable" not in out[0].issue.lower() or "pages are unreachable" not in text


def test_the_collapsed_finding_carries_the_real_numbers():
    out = sitemap_unreachable(_results(7777), "RR", attempted=8029)
    d = out[0].details
    assert d["failed"] == 7777 and d["attempted"] == 8029
    assert 0 < d["failure_rate"] < 1


def test_a_legitimately_low_brand_is_not_collapsed():
    """MHD's capped run fails ~31% and every one of those is a real dead page."""
    out = sitemap_unreachable(_results(279, status=404), "MHD", attempted=900)
    assert len(out) == 279


def test_without_an_attempted_count_nothing_is_collapsed():
    """No denominator means no rate, and guessing one would collapse real findings."""
    assert len(sitemap_unreachable(_results(50, status=404), "X")) == 50
