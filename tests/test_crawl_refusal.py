"""A run that reached almost none of the site must refuse, and must say whose fault it is.

The incident: RR run 119 finished `ok` with `fetched: 8029, fetched_ok: 77` — it attempted every
page, 77 responded, and the product recorded a successful full census because the only refusal test
was `pages_audited == 0`. It then produced 7,777 `sitemap_unreachable` findings, i.e. a report
telling the client 97% of their site was down. It was us, throttled.

The floor is measured, not chosen: across **106 historical runs** the median success rate is 99.7%,
the 10th percentile is 90.5%, and exactly ONE run falls below 50% — the throttled one. Legitimate
lows sit well clear: COC 58.7% (its sitemap is ~38% dead), MHD 69% (degraded origin, capped sample),
DBH 75%.
"""
from __future__ import annotations

import pytest

from server.jobs import crawl_verdict


def _r(fetched, ok, **kw):
    d = {"fetched": fetched, "fetched_ok": ok, "pages_audited": ok}
    d.update(kw)
    return d


# --- the incident itself --------------------------------------------------------------------

def test_the_run_that_reached_77_of_8029_pages_refuses():
    v = crawl_verdict(_r(8029, 77))
    assert v.refuse
    assert "77" in v.reason and "8,029" in v.reason


def test_the_refusal_blames_the_crawl_not_the_website():
    """The old text said 'The website was unreachable'. It was not."""
    v = crawl_verdict(_r(8029, 77))
    low = v.reason.lower()
    assert "our" in low or "we were" in low or "throttl" in low
    assert "your website was unreachable" not in low


def test_a_refused_run_says_the_brand_was_not_audited():
    v = crawl_verdict(_r(8029, 77))
    assert "not" in v.reason.lower() and "audited" in v.reason.lower()


# --- the legitimate lows must NOT refuse ------------------------------------------------------

@pytest.mark.parametrize("fetched,ok,who", [
    (1510, 886, "COC at 58.7% — its sitemap really is ~38% dead"),
    (900, 621, "MHD at 69% — degraded origin, capped sample"),
    (574, 431, "DBH at 75%"),
    (19, 19, "TDRC, tiny but complete"),
])
def test_real_runs_with_genuinely_dead_pages_still_pass(fetched, ok, who):
    assert not crawl_verdict(_r(fetched, ok)).refuse, who


def test_a_perfect_run_passes():
    assert not crawl_verdict(_r(7995, 7995)).refuse


# --- zero is still zero, and still refuses ----------------------------------------------------

def test_zero_pages_still_refuses():
    v = crawl_verdict(_r(0, 0))
    assert v.refuse


def test_enumeration_returning_nothing_is_distinguished_from_being_blocked():
    """`sitemap_blocked` already carries this. Inferring it from a zero count is what made three
    brands read as 'the website was unreachable' when we had been rate limited."""
    blocked = crawl_verdict(_r(0, 0, sitemap_blocked=True))
    empty = crawl_verdict(_r(0, 0, sitemap_blocked=False))
    assert blocked.refuse and empty.refuse
    assert "block" in blocked.reason.lower()
    assert blocked.reason != empty.reason


# --- the shape test: a collapse from the brand's own norm -------------------------------------

def test_a_relative_collapse_test_was_measured_and_is_deliberately_absent():
    """The shape test — "did this collapse from the brand's own norm" — is a good idea the data
    does not support, and that is recorded rather than quietly dropped.

    Measured on the same 106 runs, refusing at `rate < ratio x brand_median` costs:
        0.50 -> 0 legitimate runs refused
        0.60 -> 1: COC at 58.7%, a REAL run (its sitemap is ~38% dead)
        0.70 -> 2: COC at 62.4% and 58.7%
    So 0.50 is the tightest safe ratio, and at 0.50 the threshold for a 99.7%-median brand is
    ~49.9% — below the hard floor, for every brand we have. It could never fire.

    A guard that cannot trigger is worse than no guard: it reads as protection. This test exists so
    that anyone re-adding one has to confront the numbers first."""
    # 55% against a 99% norm: a human might call it a collapse, but COC has legitimately run at
    # 58.7%, so no safe ratio catches it. The hard floor is the rule.
    assert not crawl_verdict(_r(1000, 550), history=[0.99, 0.995, 0.99]).refuse


def test_a_brand_that_is_always_low_is_not_refused_for_being_itself():
    """MHD has never been healthy. 69% is its normal, and the 50% floor clears it comfortably —
    which is exactly what the relative test was wanted for, achieved without it."""
    assert not crawl_verdict(_r(900, 621), history=[0.69, 0.70, 0.88]).refuse


# --- the cooldown ------------------------------------------------------------------------------
# At 05:58 the queue started three brands' enumeration in the 8 seconds after COC finished a
# 400-request link-probe burst. All three were rate limited, all three failed in 2-3 seconds, and
# all three were recorded as "the website was unreachable". Ninety seconds of patience prevents it.

def test_a_run_starting_straight_after_another_waits():
    from server.jobs import cooldown_seconds

    assert cooldown_seconds(seconds_since_last_run=0) > 0
    assert cooldown_seconds(seconds_since_last_run=5) > 0


def test_a_run_starting_long_after_the_last_one_does_not_wait():
    from server.jobs import cooldown_seconds

    assert cooldown_seconds(seconds_since_last_run=3600) == 0


def test_the_first_run_ever_does_not_wait():
    from server.jobs import cooldown_seconds

    assert cooldown_seconds(seconds_since_last_run=None) == 0


def test_the_wait_shrinks_as_time_already_passed():
    from server.jobs import COOLDOWN_SECONDS, cooldown_seconds

    assert cooldown_seconds(seconds_since_last_run=0) == COOLDOWN_SECONDS
    part = cooldown_seconds(seconds_since_last_run=COOLDOWN_SECONDS / 2)
    assert 0 < part < COOLDOWN_SECONDS
