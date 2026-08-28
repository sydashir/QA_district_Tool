"""Contrast, tap targets and broken images — proven against LOCAL FIXTURES ONLY.

No client page is loaded here, and none will be until the client has been told the rendering layer
exists (design doc §10.4). The fixtures carry known defects so each check can be held to an exact
expected result rather than "it found some things".
"""
from __future__ import annotations

import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "render"


@pytest.fixture(scope="module")
def browser():
    pw = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"chromium unavailable: {e}")
        yield b
        b.close()


@pytest.fixture()
def mobile(browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    yield page
    page.close()


def _open(page, name: str):
    page.goto((FIXTURES / name).resolve().as_uri(), wait_until="load")


# --------------------------------------------------------------------------- contrast
def test_unreadable_text_is_reported_with_its_measured_ratio(mobile):
    """Connor's "the button text is unreadable against its background", reproduced."""
    from render.a11y import run_axe, to_findings

    _open(mobile, "a11y.html")
    findings = to_findings(run_axe(mobile), "https://x/p/", viewport="mobile")
    contrast = [f for f in findings if f.check == "contrast"]
    assert len(contrast) == 1
    assert ".bad-contrast" in contrast[0].location
    assert "1.52" in contrast[0].suggestion, "the measured ratio must be in the finding"


def test_readable_text_is_not_reported(mobile):
    from render.a11y import run_axe

    _open(mobile, "a11y.html")
    flagged = {n["target"][0] for v in run_axe(mobile)["violations"]
               if v["id"] == "color-contrast" for n in v["nodes"]}
    assert ".good-contrast" not in flagged


def test_text_over_a_background_image_is_undecidable_and_never_reported(mobile):
    """The false positive this check was expected to need a veto for. axe already refuses to rule
    on it, returning `incomplete` with reason `bgImage`, so we report nothing — the same discipline
    the text layer applies to a partial CSS read."""
    from render.a11y import run_axe, to_findings

    _open(mobile, "a11y.html")
    result = run_axe(mobile)
    reasons = {n["reason"] for v in result["incomplete"] if v["id"] == "color-contrast"
               for n in v["nodes"]}
    assert "bgImage" in reasons, "axe must mark the background-image case incomplete"
    flagged = {f.location for f in to_findings(result, "https://x/p/", viewport="mobile")}
    assert not any(".on-image" in loc for loc in flagged), "an undecidable case must not be reported"


def test_the_finding_says_how_much_could_not_be_assessed(mobile):
    """"12 elements could not be assessed" is honest; counting them as failures would not be."""
    from render.a11y import run_axe, to_findings

    _open(mobile, "a11y.html")
    contrast = [f for f in to_findings(run_axe(mobile), "https://x/p/", viewport="mobile")
                if f.check == "contrast"][0]
    assert contrast.details["incomplete_on_page"] >= 1
    assert "NOT counted as failures" in contrast.suggestion


# --------------------------------------------------------------------------- tap targets
def test_crowded_small_targets_fail_but_an_isolated_one_does_not(mobile):
    """WCAG 2.5.8 exempts a small target with enough clear space, so "under 24px" is NOT the test.
    A naive size check would have reported the isolated 12x12 target — axe correctly passes it."""
    from render.a11y import run_axe

    _open(mobile, "a11y.html")
    flagged = {n["target"][0] for v in run_axe(mobile)["violations"]
               if v["id"] == "target-size" for n in v["nodes"]}
    assert flagged == {'a[href="/s1"]', 'a[href="/s2"]', 'a[href="/s3"]', 'a[href="/s4"]'}
    assert not any("isolated" in f for f in flagged)


def test_tap_target_findings_cite_the_standard_they_test(mobile):
    from render.a11y import run_axe, to_findings

    _open(mobile, "a11y.html")
    tap = [f for f in to_findings(run_axe(mobile), "https://x/p/", viewport="mobile")
           if f.check == "tap_target"]
    assert tap and all("2.5.8 (AA)" in f.suggestion for f in tap)
    # WARNING, not ERROR — changed deliberately after the first per-brand measurement. 18 findings
    # across 63 pages on 2 of 8 brands, every one in a footer or in leftover WordPress boilerplate
    # (a `hello-world` sample post, a wordpress.org credit link). Genuine WCAG 2.5.8 failures, but
    # ERROR in this project means a defect that costs a phone call.
    assert all(f.severity.value == "warning" for f in tap)


def test_the_44px_rule_is_advisory_off_by_default_and_never_an_error(mobile):
    """44x44 is WCAG 2.5.5 level AAA / Apple HIG — a standard this client never adopted. It ships
    as INFO, and nothing calls it unless asked."""
    from render.a11y import enhanced_target_findings

    _open(mobile, "a11y.html")
    advisory = enhanced_target_findings(mobile, "https://x/p/", viewport="mobile")
    assert advisory, "the 30x30 target should be picked up as a recommendation"
    assert all(f.severity.value == "info" for f in advisory)
    assert all(f.details["advisory"] is True for f in advisory)
    assert all("RECOMMENDATION, not a failure" in f.suggestion for f in advisory)


# --------------------------------------------------------------------------- broken images
def test_a_missing_image_that_takes_up_space_is_reported(mobile):
    from render.images import find_broken, scroll_to_load_everything, to_findings

    _open(mobile, "images.html")
    scroll_to_load_everything(mobile, settle_ms=300)
    findings = to_findings(find_broken(mobile), "https://x/p/", viewport="mobile")
    assert len(findings) == 1
    assert findings[0].details["src"].endswith("does-not-exist.png")


@pytest.mark.parametrize("src_fragment", ["also-missing", "spacer-missing"])
def test_images_nobody_can_see_are_not_reported(mobile, src_fragment):
    """A hidden or zero-box image is a theme's spacer, not a defect a visitor experiences."""
    from render.images import find_broken, scroll_to_load_everything

    _open(mobile, "images.html")
    scroll_to_load_everything(mobile, settle_ms=300)
    assert not any(src_fragment in c["src"] for c in find_broken(mobile))


def test_an_image_we_blocked_ourselves_is_never_reported(mobile):
    """Never report a defect your own tooling created — the `space_before_punct` lesson."""
    from render.images import find_broken, scroll_to_load_everything

    _open(mobile, "images.html")
    scroll_to_load_everything(mobile, settle_ms=300)
    real = find_broken(mobile)
    assert real, "sanity: the fixture has one genuinely broken image"
    suppressed = find_broken(mobile, blocked_urls={c["src"] for c in real})
    assert suppressed == []


def test_a_smooth_scrolling_page_is_still_scrolled_to_the_bottom(mobile):
    """The bug that cost this check its first two production findings.

    Under `scroll-behavior: smooth` each scrollTo starts an animation and the next call restarts
    it, so the loop crawls. Measured on TDRC's live homepage: 1181px reached of 6862px."""
    from render.images import scroll_to_load_everything

    _open(mobile, "lazy_smooth.html")
    result = scroll_to_load_everything(mobile, settle_ms=300)
    assert result["reached_bottom"], (
        f"only reached {result['max_scroll']}px of {result['doc_height']}px — the smooth-scroll "
        f"override is not working, and every lazy image below that will be called broken")


def test_a_lazy_image_that_exists_is_never_called_broken(mobile):
    """`naturalWidth == 0` means "not decoded", which includes "never requested". TDRC reported two
    of these; both returned HTTP 200 with real PNG bytes. Only a real fetch separates the cases."""
    from render.images import find_broken, scroll_to_load_everything

    _open(mobile, "lazy_smooth.html")
    scroll_to_load_everything(mobile, settle_ms=300)
    assert not any("real-image" in c["src"] for c in find_broken(mobile))


def test_the_confirmation_step_does_not_silence_genuinely_missing_images(mobile):
    """A fix that makes the check never fire is not a fix. Same page, same lazy loading, one file
    that is really absent."""
    from render.images import find_broken, scroll_to_load_everything

    _open(mobile, "lazy_smooth.html")
    scroll_to_load_everything(mobile, settle_ms=300)
    broken = find_broken(mobile)
    assert [c["src"].rsplit("/", 1)[-1] for c in broken] == ["lazy-but-truly-missing.png"]


def test_findings_say_the_template_was_sampled_not_every_page(mobile):
    """One page per template is rendered, so a finding means "this template is broken and is used
    on N pages" — never "N pages were checked"."""
    from render.images import find_broken, scroll_to_load_everything, to_findings

    _open(mobile, "images.html")
    scroll_to_load_everything(mobile, settle_ms=300)
    f = to_findings(find_broken(mobile), "https://x/p/", viewport="mobile", page_count=57)[0]
    assert f.details["template_sampled"] is True and f.details["page_count"] == 57
