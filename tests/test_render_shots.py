"""Element screenshots — making a finding legible without changing what it claims.

Design: docs/plans/2026-08-29-element-screenshots-design.md, approved 2026-08-29.
The measured decisions this pins:
  * 48 CSS px of padding — 0px hides the outline entirely (an outline draws OUTSIDE the border box,
    so a tight clip cuts it off), 16px shows no context, 96px costs +72% bytes for nothing.
  * PNG, never JPEG. JPEG q60 is 26% smaller and looked identical, but a contrast finding is ABOUT
    COLOUR and a lossy codec rewrites the hex the finding asserts.
  * The outline goes on AFTER measurement and comes off after the shot. Injecting it before axe runs
    would feed our own outline into the contrast and tap-target results — D14's fifth instance.
"""
from __future__ import annotations

import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "render"


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture()
def page(browser):
    p = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    p.goto((FIXTURES / "a11y.html").as_uri(), wait_until="domcontentloaded")
    yield p
    p.close()


def test_a_shot_is_a_png_data_uri_ready_to_embed(page):
    from render.shots import capture

    shot = capture(page, "#crowded")
    assert shot is not None
    assert shot["data_uri"].startswith("data:image/png;base64,")
    assert shot["bytes"] > 0


def test_the_clip_includes_context_around_the_element(page):
    """An element alone is unrecognisable. The measured answer was 48px of padding — CAPPED at the
    viewport, since a clip that runs off the page throws in Playwright. #crowded is 374px wide in a
    390px viewport, so it exercises the cap; the link exercises the padding."""
    from render.shots import PADDING_PX, capture

    link = page.evaluate("() => {const r=document.querySelectorAll('a')[0].getBoundingClientRect();"
                         "return {w: r.width};}")
    shot = capture(page, "a:nth-of-type(1)", require_unique=False)
    if shot:                                    # a narrow element gets the full padding
        assert shot["width"] > link["w"] * 2, "no context was added around the element"

    wide = capture(page, "#crowded")             # a near-full-width element is capped, not refused
    assert wide is not None
    assert wide["width"] <= 390 * 2, "the clip must never exceed the viewport"
    assert wide["width"] >= 374 * 2, "the element itself must still be fully in frame"


def test_the_outline_is_removed_after_the_shot(page):
    """It must not survive into any later measurement on the same page."""
    from render.shots import capture

    capture(page, "#crowded")
    assert page.evaluate("() => document.querySelectorAll('[data-qa-flag]').length") == 0
    assert page.evaluate("() => !document.getElementById('qa-shot-style')")


def test_a_selector_that_matches_nothing_returns_none_rather_than_a_blank_image(page):
    """A blank image under a finding looks like the defect. No image is honest; a wrong one is not."""
    from render.shots import capture

    assert capture(page, "#definitely-not-here") is None


def test_a_selector_matching_many_elements_is_refused(page):
    """The locator gate. A screenshot of the WRONG element is worse than no screenshot — it is
    confident-looking evidence for something we did not find."""
    from render.shots import capture

    assert page.evaluate("() => document.querySelectorAll('a').length") > 1
    assert capture(page, "a", require_unique=True) is None


def test_the_gate_counts_its_own_misses(page):
    """Syed, 2026-08-29: a gate that silently omits looks identical to a class with nothing to show.
    A class resolving below ~70% does not ship, and that cannot be known without the tally."""
    from render.shots import ShotTally, capture

    tally = ShotTally()
    capture(page, "#crowded", tally=tally, cls="tap_target")
    capture(page, "#definitely-not-here", tally=tally, cls="tap_target")
    capture(page, "a", tally=tally, cls="contrast", require_unique=True)

    assert tally.per_class["tap_target"]["attempted"] == 2
    assert tally.per_class["tap_target"]["one"] == 1
    assert tally.per_class["tap_target"]["none"] == 1
    assert tally.per_class["contrast"]["many"] == 1
    assert tally.hit_rate("tap_target") == pytest.approx(0.5)


def test_the_tally_reports_a_class_that_would_not_ship(page):
    from render.shots import SHIP_FLOOR, ShotTally

    t = ShotTally()
    for _ in range(7):
        t.record("good", "one")
    for _ in range(3):
        t.record("good", "none")
    for _ in range(9):
        t.record("bad", "none")
    t.record("bad", "one")
    assert t.hit_rate("good") >= SHIP_FLOOR
    assert t.hit_rate("bad") < SHIP_FLOOR
    assert "bad" in t.below_floor()
    assert "good" not in t.below_floor()


# --- attaching shots to findings ------------------------------------------------------------------

def _contrast_finding(fg, bg, sel, url="https://x/a/", ratio=3.1):
    from auditor.report import Finding, Severity
    return Finding(url=url, check="contrast", severity=Severity.ERROR, fingerprint=sel,
                   issue="", location="mobile", snippet="", suggestion="",
                   details={"class": "color-contrast", "fgColor": fg, "bgColor": bg,
                            "contrastRatio": ratio, "expectedContrastRatio": "4.5:1",
                            "selector": sel, "viewport": "mobile"})


def test_only_one_shot_is_taken_per_colour_pair(page):
    """The whole point of collapsing. 671 contrast nodes across 8 brands were 15 colour decisions —
    photographing every node would be 671 near-identical images of the same theme colour."""
    from render.shots import attach_shots

    # Three findings sharing ONE colour pair, each on a different element, plus one with a
    # different pair. Selectors must resolve uniquely or the locator gate refuses them — which is
    # correct behaviour and would hide what this test is actually checking.
    findings = [_contrast_finding("#777", "#fff", sel)
                for sel in ("#crowded", "p:nth-of-type(1)", "p:nth-of-type(2)")]
    findings.append(_contrast_finding("#111", "#fff", "#crowded"))
    attach_shots(page, findings)
    with_shots = [f for f in findings if f.details.get("shot")]
    assert len(with_shots) == 2, "expected one per distinct colour pair, not one per element"


def test_a_finding_whose_element_cannot_be_located_simply_has_no_image(page):
    from render.shots import ShotTally, attach_shots

    f = _contrast_finding("#777", "#fff", "#not-on-this-page")
    tally = ShotTally()
    attach_shots(page, [f], tally=tally)
    assert "shot" not in f.details
    assert tally.per_class["contrast"]["none"] == 1


def test_shots_are_attached_after_measurement_not_before(page):
    """attach_shots takes findings that ALREADY exist — it cannot run before the measurement that
    produced them. That ordering is the D14 guard, expressed in the signature itself."""
    import inspect

    from render.shots import attach_shots
    params = list(inspect.signature(attach_shots).parameters)
    assert params[:2] == ["page", "findings"]


def test_the_tally_is_reported_per_check_not_globally(page):
    from render.shots import ShotTally, attach_shots
    from auditor.report import Finding, Severity

    tap = Finding(url="https://x/", check="tap_target", severity=Severity.WARNING,
                  fingerprint="t", issue="", location="mobile", snippet="", suggestion="",
                  details={"class": "target-size", "selector": "#crowded"})
    tally = ShotTally()
    attach_shots(page, [tap, _contrast_finding("#777", "#fff", "#nope")], tally=tally)
    assert set(tally.per_class) == {"tap_target", "contrast"}
