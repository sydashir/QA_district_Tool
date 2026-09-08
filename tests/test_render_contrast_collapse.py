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

import pytest

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


# ------------------------------------------------------------ darken_to_pass direction (2026-09-08)
# Found by adversarial review: `toward_white = _rel_luminance(bg) < 0.5` chose the direction from
# the BACKGROUND alone. On a mid-tone background that sent WHITE text "toward white" — a no-op — so
# the function reported that no shade could pass and the report told the designer to change the
# background. Measured: 8 of 36 client-facing rows carried that instruction, and every one of them
# passes by going darker. A confidently wrong instruction is worse than none, and it would have
# been the first impression of a newly published check.

@pytest.mark.parametrize("fg,bg", [
    ("#ffffff", "#8a8a8a"),
    ("#ffffff", "#7a7a7a"),
    ("#ffffff", "#949494"),
    ("#ffffff", "#6f8fae"),
])
def test_white_on_a_mid_tone_background_gets_a_colour_not_a_shrug(fg, bg):
    from render.a11y import _contrast_ratio, _hex_to_rgb, darken_to_pass

    got = darken_to_pass(fg, bg, 4.5)
    assert got is not None, "reported impossible when darkening reaches the target"
    assert _contrast_ratio(_hex_to_rgb(got), _hex_to_rgb(bg)) >= 4.5


def test_the_direction_is_whichever_moves_less():
    """Black text on a dark background must still go LIGHTER — the fix must not simply always
    darken, or it would break the case the original one-way scan got right."""
    from render.a11y import _hex_to_rgb, _rel_luminance, darken_to_pass

    got = darken_to_pass("#000000", "#3a3a3a", 4.5)
    assert got is not None
    assert _rel_luminance(_hex_to_rgb(got)) > _rel_luminance(_hex_to_rgb("#000000"))


def test_a_pair_that_truly_cannot_pass_still_says_so():
    """The honest None must survive: inventing an answer sends a designer in a circle."""
    from render.a11y import darken_to_pass
    # Mid grey on mid grey: no lightness of an achromatic fg reaches 7:1 here.
    assert darken_to_pass("#808080", "#8a8a8a", 7.0) is None


def test_an_inversion_is_not_described_as_keeping_the_same_colour():
    """White -> #212121 is a lightness inversion. Calling it 'keeps the same colour' is the same
    class of confident falsehood the direction bug produced."""
    from render.a11y import collapse_contrast, to_findings

    axe = {"violations": [{"id": "color-contrast", "nodes": [{
        "target": ["a"], "html": "<a>x</a>",
        "data": {"fgColor": "#ffffff", "bgColor": "#8a8a8a",
                 "contrastRatio": 3.45, "expectedContrastRatio": "4.5:1"}}]}],
        "incomplete": [], "url": "https://x.invalid/p"}
    out = collapse_contrast(to_findings(axe, "https://x.invalid/p", viewport="mobile"))
    text = " ".join(f.suggestion or "" for f in out)
    assert "keeps the same colour" not in text
    assert "substantial change" in text
