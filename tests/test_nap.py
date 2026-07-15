"""Tests for the NAP-sheet canonical-phone parser (auditor/nap.py).

TDD-first. Unit tests run on a hand-built grid (fast, offline). One integration
test runs on the 2026-07-02 .tmp_dd SNAPSHOT of jake_sites.xlsx — the live-sheet
read (D2) will swap only the grid source, not the parse logic these tests pin.
"""
from __future__ import annotations

import pytest

from auditor import nap
from auditor.checks.phone import normalize


def _row(d="", e="", h="", i="", bd=""):
    """Build one full-width NAP row (0-based cols: D=3, E=4, H=7, I=8, BD=55)."""
    r = [""] * 56
    r[3], r[4], r[7], r[8], r[55] = d, e, h, i, bd
    return r


SYNTHETIC = [
    _row(e="NAP Name", h="Nickname", i="NAP Phone Number"),          # header, E not a brand token
    _row(d="National", e="RR", h="SEO Target", i="866-330-9449", bd="844-917-4100"),
    _row(h="PPC Target", i="866-923-1867"),                          # PPC continuation (blank E)
    _row(e="Renaissance - FV", h="RR - Fountain Valley", i="657-571-6350"),
    _row(d="National", e="GL", h="SEO Target", i="844-576-0144", bd="800-994-2184"),
    _row(h="PPC Target", i="844-972-2859"),
    _row(e="GL LB", h="GL - Long Beach", i="562-620-5663", bd="800-692-9850"),
    _row(e="GL overlap loc", h="GL - Overlap", i="844-576-0144"),    # current == a stale value elsewhere
    _row(d="National", e="CAD", i="888-995-4208"),                   # single national, H blank
    _row(e="California Detox - OC", h="CAD - Orange County", i="949-694-8305"),
]


def test_national_seo_ppc_split():
    brands = nap.parse_nap_grid(SYNTHETIC)
    rr = brands["RR"]
    assert [(n.number, n.channel) for n in rr.national] == [
        (normalize("866-330-9449"), "SEO"),
        (normalize("866-923-1867"), "PPC"),
    ]
    gl = brands["GL"]
    assert [(n.number, n.channel) for n in gl.national] == [
        (normalize("844-576-0144"), "SEO"),
        (normalize("844-972-2859"), "PPC"),
    ]


def test_single_national_no_channel():
    cad = nap.parse_nap_grid(SYNTHETIC)["CAD"]
    assert [(n.number, n.channel) for n in cad.national] == [(normalize("888-995-4208"), None)]


def test_per_location():
    gl = nap.parse_nap_grid(SYNTHETIC)["GL"]
    assert gl.per_location["GL - Long Beach"] == normalize("562-620-5663")
    cad = nap.parse_nap_grid(SYNTHETIC)["CAD"]
    assert cad.per_location["CAD - Orange County"] == normalize("949-694-8305")


def test_stale_retired_and_current_wins():
    gl = nap.parse_nap_grid(SYNTHETIC)["GL"]
    # 800-994-2184 and 800-692-9850 are OLD; 844-576-0144 is current so must NOT be stale
    assert normalize("800-692-9850") in gl.stale_retired
    assert normalize("800-994-2184") in gl.stale_retired
    assert normalize("844-576-0144") not in gl.stale_retired


def test_unicode_whitespace_tolerance():
    # Forward-looking for the live read; the 2026-07-02 snapshot was 100% ASCII in I/BD.
    # NBSP inside a location name -> single ASCII space; number with NBSP -> still parses.
    grid = [
        _row(d="National", e="GL", h="SEO Target", i="844-576-0144"),
        _row(e="loc", h="GL - Long Beach", i="562 620-5663"),
    ]
    gl = nap.parse_nap_grid(grid)["GL"]
    assert "GL - Long Beach" in gl.per_location
    assert gl.per_location["GL - Long Beach"] == normalize("562-620-5663")
    # a brand token carrying a zero-width char (U+FEFF) must still match, or the whole
    # brand silently vanishes — the Check #2 trap in a whitespace costume.
    grid2 = [_row(d="National", e="﻿GL​", h="SEO Target", i="844-576-0144")]
    assert "GL" in nap.parse_nap_grid(grid2)


def test_zero_canonical_is_loud_failure():
    """The GeoData Check #2 trap: a brand yielding no canonical number must fail
    loudly, never pass as a silent clean parse."""
    broken = [_row(d="National", e="COC", i="")]  # brand header, no number
    brands = nap.parse_nap_grid(broken)
    problems = nap.validate_canonical(brands)
    assert any(p.brand == "COC" for p in problems)


# --- integration against the real 2026-07-02 snapshot ---

def test_snapshot_known_values():
    if not nap.NAP_SNAPSHOT.exists():
        pytest.skip(f"snapshot not present at {nap.NAP_SNAPSHOT}")
    brands = nap.load_canonical_from_snapshot()

    gl = brands["GL"]
    nums = {(n.number, n.channel) for n in gl.national}
    assert (normalize("844-576-0144"), "SEO") in nums
    assert (normalize("844-972-2859"), "PPC") in nums
    assert normalize("800-692-9850") in gl.stale_retired          # the retired Newport Beach number
    assert normalize("562-620-5663") in gl.per_location.values()  # current Long Beach local

    rr = brands["RR"]
    rr_nums = {(n.number, n.channel) for n in rr.national}
    assert (normalize("866-330-9449"), "SEO") in rr_nums
    assert (normalize("866-923-1867"), "PPC") in rr_nums

    assert normalize("888-995-4208") in {n.number for n in brands["CAD"].national}
    assert normalize("888-707-6073") in {n.number for n in brands["DBH"].national}

    # every LIVE brand must yield a canonical number (Check #2 trap); SLN is not live.
    assert nap.validate_canonical(brands, ignore=nap.NOT_LIVE_BRANDS) == []
    # ...and the check still fires on a genuine zero (SLN, not ignored here).
    assert any(p.brand == "SLN" for p in nap.validate_canonical(brands))


if __name__ == "__main__":
    import sys
    raise SystemExit(pytest.main([__file__, "-q"]))
