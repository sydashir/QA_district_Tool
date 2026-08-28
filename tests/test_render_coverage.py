"""Three things the first per-brand measurement forced.

1. **Contrast coverage is wildly uneven and must be stated.** axe cannot compute a ratio when text
   sits on a gradient, a photo, or a partially-obscured element, so those go to its `incomplete`
   bucket and we never report them. Measured share of contrast-relevant elements we could actually
   judge, 8 pages per brand: TDRC 88.5%, AH 84.3%, CAD 70.3%, AR 55.2%, GL 29.6%, COC 19.5%,
   RR 12.2%, **DBH 2.1%**. Showing DBH's 7 findings without that number implies the site was checked
   and passed with 7 problems. It was not checked.

   DBH's cause is structural, not ours: 234 of its 331 withheld elements are `bgGradient` and 83 are
   `bgImage`, the same ~11 + ~32 on every page — one gradient-heavy template, repeated. Our guard
   blocked nothing it needed (1 blocked, 73 allowed) and its stylesheets loaded.

2. **A designer should not need a contrast tool.** Naming the failing pair is not enough if the fix
   still requires trial and error, so the finding carries a colour that would pass.

3. **Tap targets are WARNING, not ERROR** — real WCAG 2.5.8 failures, but 18 across 63 pages and
   concentrated in footers and leftover WordPress boilerplate.
"""
from __future__ import annotations

import pytest

from auditor.report import Finding, Severity
from render.a11y import (coverage_finding, darken_to_pass, collapse_contrast)


def _f(fg="#7a7a7a", bg="#ffffff", ratio=4.29, url="https://x/a/", sel="s1"):
    return Finding(url=url, check="contrast", severity=Severity.ERROR, fingerprint=sel,
                   issue="", location="mobile", snippet="", suggestion="",
                   details={"class": "color-contrast", "fgColor": fg, "bgColor": bg,
                            "contrastRatio": ratio, "expectedContrastRatio": "4.5:1",
                            "viewport": "mobile"})


# --- 1. coverage ---------------------------------------------------------------------------------

def test_a_site_we_could_barely_assess_says_so_instead_of_implying_coverage():
    f = coverage_finding("dbh", assessed=7, withheld=331,
                         reasons={"bgGradient": 234, "bgImage": 83})
    assert f is not None
    assert "2%" in f.suggestion or "2.1%" in f.suggestion
    assert "could not" in f"{f.issue} {f.suggestion}".lower()
    assert f.details["assessed_pct"] < 5


def test_the_coverage_finding_names_the_reason_so_it_is_not_a_shrug():
    f = coverage_finding("dbh", assessed=7, withheld=331,
                         reasons={"bgGradient": 234, "bgImage": 83})
    assert "gradient" in f.suggestion.lower()
    assert f.details["dominant_reason"] == "bgGradient"


def test_a_well_covered_site_gets_no_coverage_finding():
    """TDRC at 88.5% assessed does not need a caveat — adding one everywhere would train the reader
    to skip it, and then it would not be read on DBH where it matters."""
    assert coverage_finding("tdrc", assessed=116, withheld=15, reasons={}) is None


def test_coverage_is_reported_for_the_middling_sites_too():
    """RR 12.2% and COC 19.5% are not DBH, but 'we checked 1 element in 8' is still not coverage."""
    assert coverage_finding("rr", assessed=67, withheld=483, reasons={"bgImage": 400}) is not None
    assert coverage_finding("coc", assessed=121, withheld=499, reasons={"bgImage": 400}) is not None


def test_a_site_with_nothing_to_assess_does_not_produce_a_finding():
    assert coverage_finding("x", assessed=0, withheld=0, reasons={}) is None


# --- 2. a colour that would pass ------------------------------------------------------------------

@pytest.mark.parametrize("fg,bg,required", [
    ("#7a7a7a", "#ffffff", 4.5),
    ("#1989ff", "#ffffff", 4.5),
    ("#6ec1e4", "#ffffff", 4.5),
])
def test_the_suggested_colour_actually_passes(fg, bg, required):
    """Computed, not guessed. A suggestion that still fails is worse than no suggestion."""
    from render.a11y import _contrast_ratio, _hex_to_rgb
    out = darken_to_pass(fg, bg, required)
    assert out is not None
    assert _contrast_ratio(_hex_to_rgb(out), _hex_to_rgb(bg)) >= required


def test_the_suggested_colour_keeps_the_hue_it_started_with():
    """A designer will reject "use black". #1989ff must stay recognisably the same blue."""
    out = darken_to_pass("#1989ff", "#ffffff", 4.5)
    r, g, b = int(out[1:3], 16), int(out[3:5], 16), int(out[5:7], 16)
    assert b > r and b > g          # still blue-dominant


def test_light_text_on_a_dark_background_is_lightened_not_darkened():
    """AR's palette is light grey on near-black. Darkening there makes it worse."""
    from render.a11y import _contrast_ratio, _hex_to_rgb
    out = darken_to_pass("#5f5f5f", "#1a1a19", 4.5)
    assert out is not None
    assert _contrast_ratio(_hex_to_rgb(out), _hex_to_rgb("#1a1a19")) >= 4.5


def test_the_collapsed_finding_carries_the_suggested_colour():
    out = collapse_contrast([_f()])
    assert out[0].details.get("suggested_fg")
    assert out[0].details["suggested_fg"] in out[0].suggestion


# --- 3. tap targets --------------------------------------------------------------------------------

def test_tap_targets_are_a_warning_not_an_error():
    """Measured: 18 findings across 63 pages, on 2 of 8 brands, and every one sat in a footer or in
    leftover WordPress boilerplate (a `hello-world` sample post, a wordpress.org credit link).
    Real WCAG 2.5.8 failures, but nothing on the scale of "a visitor cannot reach you" — and ERROR
    is the level this project reserves for defects that cost a call."""
    from render.a11y import to_findings
    out = to_findings({"violations": [{"id": "target-size", "help": "h", "nodes": [
        {"target": ["footer a"], "html": "<a>x</a>", "message": "too small",
         "data": {"minSize": 24}, "region": "footer"}]}], "incomplete": []},
        "https://x/", viewport="mobile")
    assert out and out[0].severity is Severity.WARNING


def test_contrast_stays_an_error():
    """The split is deliberate: unreadable text is a different order of problem from a small link."""
    from render.a11y import to_findings
    out = to_findings({"violations": [{"id": "color-contrast", "help": "h", "nodes": [
        {"target": ["p"], "html": "<p>x</p>", "message": "faint",
         "data": {"contrastRatio": 3.1, "fgColor": "#aaa", "bgColor": "#fff"}}]}],
        "incomplete": []}, "https://x/", viewport="mobile")
    assert out and out[0].severity is Severity.ERROR


def test_a_tap_target_in_the_footer_says_so():
    """Without it the reader cannot triage: a cramped footer link and a cramped booking button read
    identically, and one of those matters far more than the other."""
    from render.a11y import to_findings
    out = to_findings({"violations": [{"id": "target-size", "help": "h", "nodes": [
        {"target": ["footer a"], "html": "<a>x</a>", "message": "too small",
         "data": {}, "region": "footer"}]}], "incomplete": []},
        "https://x/", viewport="mobile")
    assert "footer" in out[0].suggestion.lower()
    assert out[0].details["region"] == "footer"
