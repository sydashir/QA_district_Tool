"""The TRANSPOSED NAP tab — brands across columns, fields down rows.

Structurally different from the "NAP (Current)" tab the phone check reads: that one is brands down
rows. This one carries the per-location BUSINESS NAME and STREET ADDRESS, which the other does not.

UNION, NOT REPLACEMENT — and the reason is load-bearing enough to be a test. The new tab is a
STRICT SUBSET on phones: it is missing 10 per-location call-centre numbers (AH/CAD/MHD: San Jose,
Sacramento, Santa Cruz, Palm Desert) that "NAP (Current)" has. So phones keep coming from the old
tab and only name+address come from this one. Anyone who "simplifies" this to a single source
silently drops those ten numbers from the canonical set, and every page carrying one starts
reporting as an unknown number.
"""
from __future__ import annotations

import pathlib

import pytest

from auditor import nap

# A hand-built grid in the real shape: label in col 0, a bare brand code opening each block,
# SEO/PPC target columns carrying a phone but NO address, then physical locations.
GRID = [
    ["Section"],
    ["NAP Name", "RR", "", "Renaissance Recovery Fountain Valley", "RR Palm Beach",
     "", "GL", "", "Gratitude Lodge Long Beach"],
    ["NAP Address", "", "", "10175 Slater Ave Ste 200, Fountain Valley, CA 92708",
     "327 W Lantana Rd, Lantana, FL 33462", "", "", "", "3849 Chatwin Ave, Long Beach, CA 90808"],
    ["Nickname", "SEO Target", "PPC Target", "RR - Fountain Valley", "RR - Palm Beach",
     "", "SEO Target", "PPC Target", "GL - Long Beach"],
    ["NAP Phone Number \n(Hardcoded / Target Numbers)", "866-330-9449", "866-923-1867",
     "657-571-6350", "561-823-3230", "", "844-576-0144", "844-972-2859", "562-620-5663"],
]


def test_a_bare_brand_code_opens_that_brands_block():
    locs = nap.locations_from_grid(GRID)
    assert set(locs) == {"rr", "gl"}


def test_only_columns_with_a_real_address_become_locations():
    """The SEO/PPC target columns carry a phone but no address or business name — they are routing
    numbers, not places. Treating them as locations would invent two addressless businesses."""
    locs = nap.locations_from_grid(GRID)
    assert [l.name for l in locs["rr"]] == ["Renaissance Recovery Fountain Valley", "RR Palm Beach"]
    assert [l.name for l in locs["gl"]] == ["Gratitude Lodge Long Beach"]


def test_a_location_carries_its_address_nickname_and_phone():
    gl = nap.locations_from_grid(GRID)["gl"][0]
    assert gl.address == "3849 Chatwin Ave, Long Beach, CA 90808"
    assert gl.nickname == "GL - Long Beach"
    assert gl.phone == "+15626205663"


def test_rows_are_found_by_their_label_not_a_hardcoded_index():
    """Column and row positions drift between exports of these sheets — the Fetcher's own newer
    modules say so explicitly and resolve everything by header name. Insert a row and the parse
    must still work."""
    shifted = [["spacer"], ["another spacer"]] + GRID
    assert nap.locations_from_grid(shifted) == nap.locations_from_grid(GRID)


def test_a_grid_with_no_recognisable_labels_returns_nothing_rather_than_guessing():
    assert nap.locations_from_grid([["a", "b"], ["c", "d"]]) == {}


# --- integration against the real file ----------------------------------------------------------

def test_the_real_tab_parses_and_gl_matches_the_known_address():
    if not nap.NAP_LOCATIONS_CSV.exists():
        pytest.skip(f"not present at {nap.NAP_LOCATIONS_CSV}")
    locs = nap.load_nap_locations()
    assert {"rr", "gl", "cad", "coc", "ar", "tdrc", "ah", "mhd"} <= set(locs)
    gl = [l.address for l in locs["gl"]]
    assert "3849 Chatwin Ave, Long Beach, CA 90808" in gl


def test_the_new_tab_is_a_strict_subset_on_phones_so_it_must_not_replace_the_old_one():
    """The reason the union exists. If this ever stops being true, revisit the decision — but do it
    deliberately, with the numbers in front of you, not by simplifying the loader."""
    if not (nap.NAP_LOCATIONS_CSV.exists() and nap.NAP_SNAPSHOT.exists()):
        pytest.skip("both sources needed")
    new = {l.phone for ls in nap.load_nap_locations().values() for l in ls if l.phone}
    old = set()
    for c in nap.load_canonical_from_snapshot().values():
        old |= c.current_set()
    assert old - new, "the old tab no longer has numbers the new one lacks — re-examine the union"
