"""Ground-truth gate for `parse.py`'s visible-text extraction.

`parse.py` has shipped TWO content-fabricating bugs — text that is not on the page:

  1. ``get_text(" ")`` welded an <h2> onto the <p> below it, inventing the sentence
     "Residential Rehab Options Because emergency workers…".
  2. Emitting a space after every inline text node turned the correct markup
     ``<strong>phone rings</strong>.`` into "phone rings .", and the Phase-2 AI layer dutifully
     reported a punctuation defect that does not exist.

Both changed real findings. Both were caught by hand, late, after numbers had already been quoted.
Careful review is not a control for this — a mechanical gate is.

WHAT THESE FIXTURES ARE
Each ``<name>.html`` is a REAL fragment saved from a live brand page (``<name>.source.txt`` records
the brand and URL). Each ``<name>.expected.txt`` is the text a BROWSER renders for that fragment,
captured via Playwright ``innerText`` — ground truth produced by something other than the code under
test — then normalised to this module's one-newline-per-block-boundary convention.

The single exception is ``genuine_stray_space``, marked CONSTRUCTED in its source file: a 50-page
sample across GL and CAD contained ZERO client-typed stray spaces before punctuation. That absence
is exactly why every " ," finding the AI layer produced was a parser artifact. The case is still
pinned, because the fix must not paper over a real defect if the client ever does type one.

ADDING A CASE
Save the real fragment, render it in a browser, paste the innerText, normalise whitespace. Do NOT
generate the expected file from ``parse_html`` — that would make this test assert only that the code
still does whatever it currently does, which is precisely the failure mode it exists to prevent.
"""
from __future__ import annotations

import pathlib

import pytest

from auditor.parse import parse_html

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "parse"
CASES = sorted(p.stem for p in FIXTURES.glob("*.html"))

# Every failure mode that has actually bitten us must stay covered; a fixture going missing is a
# silent loss of protection, so the roster is asserted, not merely globbed.
REQUIRED = {
    "inline_before_period",   # <strong>x</strong>.  -> no manufactured space
    "inline_before_comma",    # <strong>x</strong>,  -> no manufactured space
    "heading_then_para",      # block boundary preserved
    "adjacent_links",         # sibling <a> not welded ("addictionDetox")
    "inline_midsentence",     # inline markup does not split a sentence
    "list_items",             # consecutive <li> stay separate
    "genuine_stray_space",    # a real client-typed " ," still surfaces
}


def test_every_known_failure_mode_still_has_a_fixture():
    assert REQUIRED <= set(CASES), f"lost coverage for: {sorted(REQUIRED - set(CASES))}"


@pytest.mark.parametrize("name", CASES)
def test_visible_text_matches_the_browser(name):
    html = (FIXTURES / f"{name}.html").read_text()
    expected = (FIXTURES / f"{name}.expected.txt").read_text().strip()
    got = parse_html(html, page_url="https://x/p/", base_url="https://x/p/").visible_text
    assert got == expected, (
        f"\n{name}: visible_text drifted from the browser-verified expectation."
        f"\n  expected: {expected!r}"
        f"\n  got:      {got!r}"
        f"\nIf the new output is genuinely correct, re-render the fixture in a browser and update "
        f"{name}.expected.txt from its innerText — never from parse_html's own output."
    )


@pytest.mark.parametrize("name", CASES)
def test_fixture_records_its_provenance(name):
    src = (FIXTURES / f"{name}.source.txt").read_text()
    assert "brand:" in src and "url:" in src, f"{name} has no recorded source page"
