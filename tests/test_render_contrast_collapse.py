"""Contrast has to be reported as a COLOUR DECISION, not as one finding per element.

Measured on 63 real pages across 8 brands: **1,036 raw violations collapse to 8 distinct colour
pairs**, and a single pair — `#7a7a7a` on white at 4.29:1, against a required 4.5:1 — accounts for
531 of them. Shipping one finding per node would put roughly 15 rows per page in front of the QA
team (CAD: 57/page), all of them restating the same theme colour. Nobody reads 465,000 rows.

Every one of the 671 nodes with colour data was independently recomputed against the WCAG formula
and all 671 agreed with axe, so the underlying finding is sound — the problem is purely how many
times it is said.
"""
from __future__ import annotations

from render.a11y import collapse_contrast
from auditor.report import Finding, Severity


def _f(fg, bg, ratio, url, sel, required="4.5:1"):
    return Finding(
        url=url, check="contrast", severity=Severity.ERROR,
        fingerprint=f"x{sel}", issue="text fails the minimum contrast ratio",
        location=sel, snippet="<a>x</a>", suggestion="...",
        details={"class": "color-contrast", "fgColor": fg, "bgColor": bg,
                 "contrastRatio": ratio, "expectedContrastRatio": required,
                 "selector": sel, "viewport": "mobile"})


def test_one_colour_pair_becomes_one_finding_however_many_elements_use_it():
    findings = [_f("#7a7a7a", "#ffffff", 4.29, f"https://x/p{i}/", f"sel{i}") for i in range(40)]
    out = collapse_contrast(findings)
    assert len(out) == 1
    assert out[0].details["element_count"] == 40


def test_the_collapsed_finding_says_how_many_pages_and_elements_it_covers():
    findings = ([_f("#7a7a7a", "#ffffff", 4.29, "https://x/a/", f"s{i}") for i in range(3)]
                + [_f("#7a7a7a", "#ffffff", 4.29, "https://x/b/", f"t{i}") for i in range(2)])
    out = collapse_contrast(findings)
    assert out[0].details["element_count"] == 5
    assert out[0].details["page_count"] == 2


def test_different_colour_pairs_stay_separate():
    """They are different decisions with different fixes — merging them would hide one."""
    out = collapse_contrast([_f("#7a7a7a", "#ffffff", 4.29, "https://x/a/", "s1"),
                             _f("#1989ff", "#ffffff", 3.46, "https://x/a/", "s2")])
    assert len(out) == 2


def test_the_same_pair_at_a_different_required_ratio_is_not_merged():
    """Large text is held to 3:1 and body text to 4.5:1. A pair that passes one and fails the other
    is two different problems, and the fix for each is different."""
    out = collapse_contrast([_f("#6ec1e4", "#ffffff", 2.02, "https://x/a/", "s1", "4.5:1"),
                             _f("#6ec1e4", "#ffffff", 2.02, "https://x/a/", "s2", "3:1")])
    assert len(out) == 2


def test_the_suggestion_names_the_colour_and_what_it_needs_to_be():
    out = collapse_contrast([_f("#7a7a7a", "#ffffff", 4.29, "https://x/a/", "s1")])
    s = out[0].suggestion
    assert "#7a7a7a" in s and "#ffffff" in s and "4.29" in s and "4.5" in s


def test_an_example_url_is_kept_so_the_finding_can_be_checked():
    out = collapse_contrast([_f("#7a7a7a", "#ffffff", 4.29, "https://x/only/", "s1")])
    assert out[0].details["examples"][0].startswith("https://x/only/")


def test_findings_that_are_not_contrast_pass_through_untouched():
    other = Finding(url="https://x/", check="tap_target", severity=Severity.ERROR,
                    fingerprint="t1", issue="small", location="a", snippet="", suggestion="",
                    details={"class": "target-size"})
    out = collapse_contrast([_f("#7a7a7a", "#ffffff", 4.29, "https://x/a/", "s1"), other])
    assert other in out and len(out) == 2
