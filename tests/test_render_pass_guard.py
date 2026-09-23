"""`render_pass` must SAY what its network guard did, not leave it implied.

Syed's standing requirement, 2026-09-15: "canary-proven third-party block before any client page
loads, and report what was blocked vs allowed." `scripts/shot_pass.py` printed those totals;
`render_pass.py` printed none — and it is the pass that needs them most, because it loads client
pages FIRST-PARTY with their stylesheets and images, where the markup pass aborts every request.

The block itself was never in doubt: `prove_attached()` runs on every page and raises
`SafetyNotArmed` if the canary gets through, so a completed pass is proof it held. What was missing
was the evidence being printed, which is what a human reads before believing it.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from render.safety import SafetyLedger

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def rp():
    """Import the script by path — `scripts/` is not a package."""
    spec = importlib.util.spec_from_file_location("render_pass_mod", ROOT / "scripts" / "render_pass.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["render_pass_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


def _ledger(blocked: dict[str, int], allowed: int,
            unrecognised: dict[str, int] | None = None) -> SafetyLedger:
    led = SafetyLedger()
    led.blocked = sum(blocked.values())
    led.blocked_hosts = dict(blocked)
    led.allowed = allowed
    led.unblocked_third_parties = dict(unrecognised or {})
    return led


def test_the_line_names_what_was_blocked_and_what_got_through(rp):
    guard = rp.new_guard()
    guard["canary_pages"] = 2
    rp.record(guard, _ledger({"www.googletagmanager.com": 3}, 40, {"cdn.jotfor.ms": 1}))
    rp.record(guard, _ledger({"418804.tctm.xyz": 2}, 35, {}))

    line = rp.guard_line(guard, 2)
    assert "canary blocked on 2 of 2" in line
    assert "blocked 5 tracker request(s)" in line
    assert "www.googletagmanager.com x3" in line and "418804.tctm.xyz x2" in line
    assert "allowed 75" in line
    assert "cdn.jotfor.ms x1" in line


def test_a_page_that_loaded_nothing_third_party_says_none_not_nothing(rp):
    """CAD and DBH legitimately request no trackers (consent-gated / headless). A blank where a
    host list should be reads as a broken report; 'none' reads as a measurement."""
    guard = rp.new_guard()
    guard["canary_pages"] = 1
    rp.record(guard, _ledger({}, 12, {}))
    line = rp.guard_line(guard, 1)
    assert "blocked 0 tracker request(s) (none)" in line
    assert "unrecognised third parties allowed: none" in line


def test_a_page_the_canary_never_reached_shows_in_the_denominator(rp):
    """A page that failed before `prove_attached` still opened a browser page. If the count of
    canary-proven loads silently equalled the count of pages, a page that skipped the guard would
    be invisible — which is the whole thing the canary exists to catch."""
    guard = rp.new_guard()
    guard["canary_pages"] = 1                  # one proved
    rp.record(guard, _ledger({"x.com": 1}, 1))  # ...but two pages were opened
    rp.record(guard, _ledger({}, 0, {}))
    assert "canary blocked on 1 of 2" in rp.guard_line(guard, guard["pages"])


def test_both_page_opening_sites_feed_one_tally(rp):
    """`run_brand` opens a page per URL and `gate_contrast` opens another per colour pair. Counting
    only the first would under-report the guard on exactly the pages it photographs."""
    src = (ROOT / "scripts" / "render_pass.py").read_text(encoding="utf-8")
    assert src.count("record(guard, led)") == 2, "both page loops must record into the tally"
    assert src.count('guard["canary_pages"] += 1') == 2
