"""Client-confirmed misspellings + the county/country scope typo.

Positive fixtures are the client's own reported strings (Connor Bringas -> Jake, ClickUp
86baawd2a). Negative fixtures guard the two places these patterns could plausibly misfire:
correctly-spelled words that CONTAIN a wrong string ("Tennessee" contains "Tennesse"), and
legitimate uses of "county" on county-scoped pages.
"""
from __future__ import annotations

import pytest

from auditor.checks import misspelling, scope
from auditor.parse import Heading, ParsedPage
from auditor.report import Severity


def _mis(url="https://x/p/", text="", headings=None, title=None):
    return misspelling.run(
        ParsedPage(url=url, visible_text=text, headings=headings or [], title=title), None)


def _scope(url="https://x/p/", text="", headings=None, title=None):
    return scope.run(
        ParsedPage(url=url, visible_text=text, headings=headings or [], title=title), None)


# --- misspellings: Connor's confirmed set ---

@pytest.mark.parametrize("wrong,right", [
    ("Inpateint", "inpatient"), ("Residental", "residential"), ("Tennesse", "Tennessee"),
    ("Rennaisance", "Renaissance"), ("Alchohol", "alcohol"), ("programing", "programming"),
])
def test_confirmed_misspellings_in_body(wrong, right):
    fs = [f for f in _mis(text=f"We offer {wrong} treatment today.")
          if f.details["class"] == "body"]
    assert len(fs) == 1 and fs[0].severity is Severity.ERROR
    assert fs[0].details["correct"] == right


def test_lowercase_only_heath():
    assert [f for f in _mis(text="bad for your heath") if f.details["class"] == "body"]
    # "Heath" is a surname and "Heathrow"/"heather" are real words — must stay quiet
    assert _mis(text="Heath Ledger flew via Heathrow past the heather.") == []


@pytest.mark.parametrize("text", [
    "We treat clients across Tennessee and California.",   # contains 'Tennesse' — must NOT fire
    "Renaissance Recovery offers residential inpatient alcohol programming.",  # all correct
    "Our residential program includes inpatient detox.",
])
def test_correct_spellings_never_fire(text):
    assert _mis(text=text) == [], f"false positive on correct text: {text!r}"


def test_slug_misspelling_is_its_own_higher_stakes_class():
    # 3 of Connor's 7 were in URLs; fixing one needs a redirect, so it's a distinct finding type
    fs = _mis(url="https://www.renaissancerecovery.com/mental-health/inpateint-ptsd/")
    slugs = [f for f in fs if f.details["class"] == "slug"]
    assert len(slugs) == 1
    assert slugs[0].location == "url" and slugs[0].severity is Severity.ERROR
    assert "redirect" in slugs[0].suggestion.lower()


def test_slug_and_body_are_separate_findings():
    fs = _mis(url="https://x/drug/rehab/residental/", text="Our Residental program is great.")
    assert {f.details["class"] for f in fs} == {"slug", "body"}
    assert len({f.fingerprint for f in fs}) == 2


def test_correct_slug_does_not_fire():
    assert _mis(url="https://x/drug/rehab/residential/") == []


# --- scope: county-for-country ---

@pytest.mark.parametrize("h1", [
    "PTSD TREATMENT CENTERS ACROSS THE COUNTY",
    "BEST DEPRESSION TREATMENT CENTERS IN THE COUNTY",
    "BEST ANXIETY TREATMENT CENTERS IN THE COUNTY",
])
def test_confirmed_county_for_country_in_h1(h1):
    fs = _scope(url="https://www.renaissancerecovery.com/mental-health/ptsd-treatment/",
                headings=[Heading(1, h1)])
    assert len(fs) == 1 and fs[0].severity is Severity.ERROR
    assert fs[0].details["class"] == "county_for_country"


def test_county_scoped_page_is_exempt():
    # a real county page may legitimately say "the county"
    assert _scope(url="https://x/drug/rehab/california/orange-county/",
                  text="Programs across the county serve local families.") == []
    assert _scope(url="https://x/p/", headings=[Heading(1, "Rehab in Orange County")],
                  text="Programs across the county serve local families.") == []


@pytest.mark.parametrize("text", [
    "Serving families in Orange County and Los Angeles County.",  # names the county — fine
    "DBT THERAPY ACROSS THE COUNTRY (HIGHLY REVIEWED)",           # DBH's correct wording
    "Programs in the county of Orange are listed below.",         # formal but valid
    "We operate treatment centers across the country.",
])
def test_correct_geography_never_fires(text):
    assert _scope(text=text) == [], f"false positive: {text!r}"


def test_same_phrase_differing_only_in_whitespace_keeps_distinct_fingerprints():
    """Live RR: the body carries 'Across\\nThe County' (visible_text preserves block boundaries)
    while the H2 carries 'Across The County'. Those are DIFFERENT keys to the occurrence counter,
    so both were numbered 0 — but make_fingerprint whitespace-collapses its parts, so the two
    collapsed to ONE fingerprint. Ten collisions were logged across a full network run. The
    occurrence key must be normalised the same way the fingerprint is."""
    from auditor.checks import scope
    from auditor.parse import Heading, ParsedPage
    p = ParsedPage(url="https://x/national/",
                   visible_text="PTSD TREATMENT CENTERS ACROSS\nTHE COUNTY and more text here",
                   headings=[Heading(level=2, text="PTSD TREATMENT CENTERS ACROSS THE COUNTY")],
                   title="PTSD Treatment")
    fs = scope.run(p, None)
    assert len(fs) == 2, f"expected both surfaces to report, got {len(fs)}"
    assert len({f.fingerprint for f in fs}) == 2, (
        f"fingerprints collided: {[f.fingerprint for f in fs]}")
