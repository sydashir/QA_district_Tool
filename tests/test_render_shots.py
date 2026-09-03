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
    assert tally.per_class["tap_target"]["captured"] == 1
    assert tally.per_class["tap_target"]["none"] == 1
    assert tally.per_class["contrast"]["many"] == 1
    assert tally.hit_rate("tap_target") == pytest.approx(0.5)
    assert tally.located_rate("tap_target") == pytest.approx(0.5)


def test_the_tally_reports_a_class_that_would_not_ship(page):
    from render.shots import SHIP_FLOOR, ShotTally

    t = ShotTally()
    for _ in range(7):
        t.record("good", "captured")
    for _ in range(3):
        t.record("good", "none")
    for _ in range(9):
        t.record("bad", "none")
    t.record("bad", "captured")
    assert t.located_rate("good") >= SHIP_FLOOR
    assert t.located_rate("bad") < SHIP_FLOOR
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


# --- locating a TEXT finding's element ------------------------------------------------------------
# Text findings carry no CSS selector — only attributes and text — so they need a locator derived
# from what was stored. `display_dial_mismatch` is the one worth doing first: 574 pages, the
# flagship defect, and "shows Call Now! 844-759-0999 but dials 888-707-6073" is far more damning as
# a picture of the actual button than as a quoted string.

@pytest.fixture()
def tel_page(browser):
    p = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    p.goto((FIXTURES / "tel.html").as_uri(), wait_until="domcontentloaded")
    yield p
    p.close()


def test_the_mismatched_button_is_found_by_its_number_pair(tel_page):
    from render.shots import mark_tel_mismatch

    assert mark_tel_mismatch(tel_page, "+18887076073", "+18447590999") == 1
    assert tel_page.evaluate("() => document.querySelector('[data-qa-target]').id") == "bad"


def test_a_link_whose_text_and_target_agree_is_not_the_defect(tel_page):
    from render.shots import mark_tel_mismatch

    assert mark_tel_mismatch(tel_page, "+18447590999", "+18447590999") == 0


def test_matching_on_the_dial_target_alone_would_be_ambiguous(tel_page):
    """#bad and #other dial the same number with different text. The displayed number is what
    separates them — without it the locator would have two candidates and refuse both."""
    from render.shots import mark_tel_mismatch

    same_target = tel_page.evaluate(
        "() => Array.from(document.querySelectorAll('a')).filter("
        "a => (a.getAttribute('href')||'').includes('8887076073')).length")
    assert same_target == 2
    # Still exactly one, because the DISPLAYED number separates them. Matching on the dial target
    # alone would have two candidates that are genuinely different links.
    assert mark_tel_mismatch(tel_page, "+18887076073", "+18447590999") == 1


def test_capturing_a_text_finding_goes_through_the_same_gate_and_tally(tel_page):
    from render.shots import ShotTally, capture_tel_mismatch

    tally = ShotTally()
    shot = capture_tel_mismatch(tel_page, "+18887076073", "+18447590999", tally=tally)
    assert shot and shot["data_uri"].startswith("data:image/png;base64,")
    assert tally.per_class["phone"]["captured"] == 1
    # and the marker must not survive
    assert tel_page.evaluate("() => document.querySelectorAll('[data-qa-target]').length") == 0


def test_a_number_pair_that_is_not_on_the_page_is_counted_as_a_miss(tel_page):
    from render.shots import ShotTally, capture_tel_mismatch

    tally = ShotTally()
    assert capture_tel_mismatch(tel_page, "+15551234567", "+15559999999", tally=tally) is None
    assert tally.per_class["phone"]["none"] == 1


# --- the tally distinguishes the locator from the feature ------------------------------------------
# Measured on AH: `dead_cta` resolved 21 of 24 selectors (88%) but produced 20 shots — one element
# resolved uniquely and could not be photographed. Counting that as a locator success is right and
# counting it as a feature success is wrong, so they are separate numbers.
#
# The uncapturable case turned out NOT to be a defect. AH's `<a>Call Now! 844-759-0999</a>` is 0x0
# with visibility:visible — but `checkVisibility()` says false and `offsetParent` is null: it sits
# inside a hidden ancestor, the desktop variant of a responsive header at mobile width. Measured
# across 24 pages of six brands, "visible-but-zero-box" looked like 224 per page until ancestors
# were accounted for, at which point it collapsed to a handful of mega-menu parents. Not a check.

def test_the_tally_separates_captured_from_merely_located(page):
    from render.shots import ShotTally

    t = ShotTally()
    t.record("dead_cta", "captured")
    t.record("dead_cta", "uncapturable")
    t.record("dead_cta", "none")
    assert t.per_class["dead_cta"]["captured"] == 1
    assert t.per_class["dead_cta"]["uncapturable"] == 1
    assert t.located_rate("dead_cta") == pytest.approx(2 / 3)   # the selector worked twice
    assert t.hit_rate("dead_cta") == pytest.approx(1 / 3)       # a picture exists once


def test_an_element_with_no_box_counts_as_located_not_lost(page):
    """It is in the DOM and the selector found it. That the browser lays it out to nothing is a fact
    about the page at this width, not a failure of the locator."""
    from render.shots import ShotTally, capture

    page.evaluate("""() => {
        const a = document.createElement('a');
        a.id = 'zero'; a.textContent = 'invisible';
        a.style.cssText = 'display:inline-block;width:0;height:0;overflow:hidden';
        document.body.appendChild(a);
    }""")
    t = ShotTally()
    assert capture(page, "#zero", tally=t, cls="dead_cta") is None
    assert t.per_class["dead_cta"]["uncapturable"] == 1
    assert t.per_class["dead_cta"]["none"] == 0


def test_the_summary_reports_both_numbers(page):
    from render.shots import ShotTally

    t = ShotTally()
    for _ in range(8):
        t.record("dead_cta", "captured")
    t.record("dead_cta", "uncapturable")
    t.record("dead_cta", "none")
    s = t.summary()
    assert "located" in s and "captured" in s


def test_the_ship_floor_is_judged_on_the_locator(page):
    """The floor asks 'can we find the element' — an element that has no box is not the locator's
    fault, and excluding it would penalise the wrong thing."""
    from render.shots import SHIP_FLOOR, ShotTally

    t = ShotTally()
    for _ in range(7):
        t.record("x", "captured")
    t.record("x", "uncapturable")
    for _ in range(2):
        t.record("x", "none")
    assert t.located_rate("x") == pytest.approx(0.8)
    assert "x" not in t.below_floor()
    assert SHIP_FLOOR <= 0.8


# --------------------------------------------------------------------------- identical duplicates
# Measured on GL run 128 (2026-09-03): `display_dial_mismatch` located 27% of 22 selectors against a
# 70% floor, and 15 of the 22 misses were `many`. Every one of those had distinct href = 1 and
# distinct text = 1 — the mobile and desktop copies of a single anchor in a responsive layout. The
# gate was refusing two copies of the same defect, not choosing between different elements.

def _twin_page(browser, html: str):
    p = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    p.set_content(html, wait_until="domcontentloaded")
    return p


def test_identical_copies_of_one_element_are_photographed_not_refused(browser):
    """Two byte-identical anchors are one defect rendered twice. There is no wrong element to pick."""
    from render.shots import ShotTally, capture

    page = _twin_page(browser, """
        <div><a href="tel:+18445760144">800-692-9850</a></div>
        <div><a href="tel:+18445760144">800-692-9850</a></div>""")
    try:
        assert page.evaluate("() => document.querySelectorAll('div > a').length") == 2
        tally = ShotTally()
        shot = capture(page, "div > a", tally=tally, cls="display_dial_mismatch")
        assert shot is not None, "identical copies should resolve, not be refused as ambiguous"
        assert tally.per_class["display_dial_mismatch"]["captured"] == 1
        assert tally.per_class["display_dial_mismatch"]["many"] == 0
    finally:
        page.close()


def test_elements_that_merely_share_a_tag_are_still_refused(browser):
    """The original guard has to survive: a picture of the WRONG element is worse than none."""
    from render.shots import ShotTally, capture

    page = _twin_page(browser, """
        <div><a href="tel:+18445760144">800-692-9850</a></div>
        <div><a href="tel:+18665551212">866-555-1212</a></div>""")
    try:
        tally = ShotTally()
        assert capture(page, "div > a", tally=tally, cls="display_dial_mismatch") is None
        assert tally.per_class["display_dial_mismatch"]["many"] == 1
    finally:
        page.close()


def test_the_duplicate_path_is_counted_separately_from_the_rates(browser):
    """`via_duplicates` must not quietly inflate located_rate — it explains HOW it resolved.

    If this ever becomes most of a class, the CSS path has stopped discriminating and the
    equivalence check is carrying the feature. That has to be visible, not folded into a pass.
    """
    from render.shots import ShotTally, capture

    page = _twin_page(browser, """
        <div><a href="tel:+18445760144">800-692-9850</a></div>
        <div><a href="tel:+18445760144">800-692-9850</a></div>
        <p><span id="solo">only one of me</span></p>""")
    try:
        tally = ShotTally()
        capture(page, "div > a", tally=tally, cls="display_dial_mismatch")
        capture(page, "#solo", tally=tally, cls="display_dial_mismatch")
        d = tally.per_class["display_dial_mismatch"]
        assert d["attempted"] == 2 and d["captured"] == 2
        assert d["via_duplicates"] == 1, "the duplicate resolution should be visible"
        assert tally.located_rate("display_dial_mismatch") == pytest.approx(1.0)
    finally:
        page.close()


def test_among_identical_copies_the_photographable_one_is_chosen(browser):
    """A responsive layout hides one copy. Picking the hidden one would report `uncapturable` for a
    defect that is plainly visible on the page."""
    from render.shots import ShotTally, capture

    page = _twin_page(browser, """
        <div style="display:none"><a href="tel:+18445760144">800-692-9850</a></div>
        <div><a href="tel:+18445760144">800-692-9850</a></div>""")
    try:
        tally = ShotTally()
        assert capture(page, "div > a", tally=tally, cls="display_dial_mismatch") is not None
        assert tally.per_class["display_dial_mismatch"]["captured"] == 1
        assert tally.per_class["display_dial_mismatch"]["uncapturable"] == 0
    finally:
        page.close()


# --------------------------------------------------------------- off-canvas duplicates (reviewed)
# Found by adversarial review of 44a1735 and REPRODUCED against real Chromium: `boxed` tested only
# size and display/visibility, so an off-canvas copy passed it. Off-canvas drawer markup usually
# precedes the desktop header in the DOM, so `find` picked the wrong twin; scrollIntoView cannot pull
# a position:fixed element into view; and the clip is clamped to the viewport. The result was a valid
# PNG of an unrelated region, scored `captured` — confident-looking evidence for something we never
# photographed. That is strictly worse than refusing.

@pytest.mark.parametrize("hider", [
    "position:fixed;top:100px;left:0;transform:translateX(-100%)",
    "position:absolute;left:-9999px;top:0",
    "position:absolute;width:1px;height:1px;clip:rect(0,0,0,0);overflow:hidden",
])
def test_an_off_canvas_twin_is_never_the_one_photographed(browser, hider):
    """The visible copy must win, whatever CSS parked the other one off-screen.

    Asserted on WHICH element the marker flagged, not merely that a shot came back — the first
    version of this test checked `shot is not None` and passed against the buggy code, because the
    bug returns a perfectly good PNG of the wrong place.
    """
    from render.shots import _FLAG_ATTR, _MARK, _STYLE_ID, _UNMARK, OUTLINE_CSS, PADDING_PX

    page = _twin_page(browser, f"""
        <div style="{hider}"><a href="tel:+18445760144">800-692-9850</a></div>
        <div style="margin-top:40px"><a href="tel:+18445760144">800-692-9850</a></div>""")
    try:
        box = page.evaluate(_MARK, ["div > a", PADDING_PX, OUTLINE_CSS, _STYLE_ID, _FLAG_ATTR])
        assert box.get("count") == 1 and box.get("duplicates") == 2
        flagged = page.evaluate(
            f"() => [...document.querySelectorAll('div > a')]"
            f".findIndex(e => e.hasAttribute('{_FLAG_ATTR}'))")
        assert flagged == 1, "the OFF-CANVAS twin was photographed instead of the visible one"
        assert box.get("covered", 0) >= 0.5, "the clip does not actually contain the element"
    finally:
        page.evaluate(_UNMARK, [_STYLE_ID, _FLAG_ATTR])
        page.close()


def test_an_element_outside_the_clip_is_not_reported_as_photographed(browser):
    """If the only copy cannot be brought into the clip, say `uncapturable` — never `captured`.

    The clip is clamped to the viewport and scrollIntoView cannot move a position:fixed element, so
    without the coverage check capture() would hand back a PNG of whatever happened to be at those
    coordinates and the tally would count it as a hit.
    """
    from render.shots import ShotTally, capture

    page = _twin_page(browser, """
        <a id="gone" style="position:fixed;top:50px;left:0;transform:translateX(-300%)"
           href="tel:+18445760144">800-692-9850</a>""")
    try:
        tally = ShotTally()
        assert capture(page, "#gone", tally=tally, cls="display_dial_mismatch") is None
        d = tally.per_class["display_dial_mismatch"]
        assert d["captured"] == 0, "a PNG of an unrelated region is not a capture"
        assert d["uncapturable"] == 1, "the selector worked; the element just cannot be shown"
    finally:
        page.close()
