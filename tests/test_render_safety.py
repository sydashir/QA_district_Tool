"""The network guard — the precondition for the whole rendering layer.

These tests use no network and no browser: `classify` is pure, and that is the point. The guard has
to be provable without touching a client's site, because the thing it prevents is touching a
client's site wrongly.
"""
from __future__ import annotations

import pytest

from render.safety import SafetyLedger, SafetyNotArmed, classify

GL = "https://www.gratitudelodge.com/"


@pytest.mark.parametrize("url", [
    "https://www.googletagmanager.com/gtm.js?id=GTM-XXXX",
    "https://www.clarity.ms/tag/abc123",
    "https://connect.facebook.net/en_US/fbevents.js",
    "https://www.facebook.com/tr?id=1&ev=PageView",
    "https://dev.visualwebsiteoptimizer.com/j.php?a=123",
    "https://418804.tctm.xyz/t.js",
    "https://bat.bing.com/bat.js",
    "https://px.ads.linkedin.com/collect",
])
def test_real_trackers_on_these_sites_are_blocked(url):
    """Every URL here was found in the live HTML of GL/RR/COC/CAD, not invented."""
    assert classify(url, GL) == "block"


def test_call_tracking_is_matched_by_pattern_not_hostname():
    """CTM serves from an ACCOUNT-NUMBERED subdomain — `418804.tctm.xyz`, not
    `calltrackingmetrics.com`. A hostname list would have missed it, and CTM consumes a number-pool
    slot on page LOAD, so every unblocked render burns a tracking number."""
    assert classify("https://418804.tctm.xyz/t.js", GL) == "block"
    assert classify("https://999999.tctm.xyz/t.js", GL) == "block"


@pytest.mark.parametrize("url", [
    "https://fonts.gstatic.com/s/roboto/v30/font.woff2",
    "https://fonts.googleapis.com/css2?family=Roboto",
    "https://maps.googleapis.com/maps/api/js?key=x",
    "https://i.ytimg.com/vi/abc/hqdefault.jpg",
    "https://cdn.trustindex.io/loader.js",
    "https://form.jotform.com/jsform/12345",
])
def test_things_the_page_needs_are_never_blocked(url):
    """Blocking too much is the subtle failure. Without fonts every text metric shifts, and the
    contrast and overflow checks would report defects that exist only because we broke the page —
    the same mistake `space_before_punct` made in the text layer."""
    assert classify(url, GL) == "allow"


def test_google_fonts_beats_the_google_deny_patterns():
    """`fonts.googleapis.com` contains "google". Allow is checked BEFORE deny for exactly this."""
    assert classify("https://fonts.googleapis.com/css2?family=Inter", GL) == "allow"


def test_the_sites_own_assets_are_always_allowed():
    for u in ("https://www.gratitudelodge.com/style.css",
              "https://cdn.gratitudelodge.com/img/hero.jpg",
              "https://gratitudelodge.com/app.js"):
        assert classify(u, GL) == "allow"


def test_an_unknown_third_party_is_allowed_but_recorded():
    """Allowed so we do not break the page, recorded so a new tracker is discovered rather than
    silently leaked to. The client's marketing stack changes without telling us."""
    led = SafetyLedger()
    led.record("https://brand-new-tracker.example/beacon.js", classify(
        "https://brand-new-tracker.example/beacon.js", GL))
    assert led.unblocked_third_parties == {"brand-new-tracker.example": 1}


def test_a_run_that_blocked_nothing_refuses_to_report():
    """Configured is not the same as working. A guard that was never attached looks exactly like a
    set of pages with no trackers, and the difference is invisible in the output."""
    led = SafetyLedger(pages=50)
    with pytest.raises(SafetyNotArmed, match="blocked NOTHING"):
        led.assert_worked()


def test_a_run_that_rendered_nothing_refuses_too():
    led = SafetyLedger(pages=0)
    with pytest.raises(SafetyNotArmed):
        led.assert_worked()


def test_a_guarded_run_passes_its_own_assertion():
    led = SafetyLedger(pages=10)
    for _ in range(3):
        led.record("https://www.googletagmanager.com/gtm.js", "block")
    led.record("https://fonts.gstatic.com/f.woff2", "allow")
    led.assert_worked()
    assert led.blocked == 3 and led.allowed == 1
    assert "blocked 3 tracker requests" in led.summary()


# --- the same guarantee, proven in a REAL browser -----------------------------------------------
# Local fixture only. The thing this guard prevents is touching a client's site wrongly, so proving
# it must never involve touching a client's site.

FIXTURE = """<!doctype html><html><head>
<script src="https://www.googletagmanager.com/gtm.js?id=GTM-TEST"></script>
<script src="https://418804.tctm.xyz/t.js"></script>
<script src="https://dev.visualwebsiteoptimizer.com/j.php?a=1"></script>
<script src="https://www.clarity.ms/tag/x"></script>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter">
</head><body><h1>fixture</h1>
<img src="https://i.ytimg.com/vi/x/hq.jpg">
<img src="https://brand-new-tracker.example/beacon.gif">
</body></html>"""


@pytest.fixture(scope="module")
def chromium():
    pw = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:                      # browsers not downloaded on this machine
            pytest.skip(f"chromium unavailable: {e}")
        yield browser
        browser.close()


def test_a_real_browser_cannot_reach_a_tracker(chromium):
    from render.safety import install

    page = chromium.new_page()
    attempted: list[str] = []
    page.on("request", lambda r: attempted.append(r.url))
    led = SafetyLedger()
    install(page, GL, led)
    page.set_content(FIXTURE, wait_until="load")
    led.pages = 1
    page.close()

    assert set(led.blocked_hosts) == {
        "www.googletagmanager.com", "418804.tctm.xyz",
        "dev.visualwebsiteoptimizer.com", "www.clarity.ms"}
    # the browser TRIES: interception happens before the request leaves, which is the whole point
    tried = [u for u in attempted if any(k in u for k in
             ("googletagmanager", "tctm.xyz", "visualwebsiteoptimizer", "clarity.ms"))]
    assert led.blocked == len(tried) == 4, "every attempted tracker request must be aborted"
    assert "brand-new-tracker.example" in led.unblocked_third_parties
    led.assert_worked()
