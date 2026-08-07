"""A sister brand named in another brand's body copy — B6.

Connor reported it twice; the clearest is "Gratitude Lodge" on the Connections site.

**Every negative test below is a real sentence from a live page**, taken from the 95-page
measurement across 8 brands described in `checks/brands.py`. That measurement found ZERO true
positives and four distinct classes of legitimate cross-brand mention, which is why this check is
a WARNING worded as a question and why the rarity collapse in `audit.py` does the deciding.
"""
from __future__ import annotations

import pytest

from auditor.audit import _collapse_brands
from auditor.checks import brands
from auditor.parse import Block, ParsedPage
from auditor.report import Finding, Severity


class _Cfg:
    def __init__(self, brand="COC"):
        self.brand = brand
        self.base_url = "https://connectionsoc.com"


def _page(body="", boilerplate="", url="https://connectionsoc.com/a/"):
    blocks = []
    if body:
        blocks.append(Block(tag="p", text=body, region="body", group=0))
    if boilerplate:
        blocks.append(Block(tag="p", text=boilerplate, region="footer", group=1))
    return ParsedPage(url=url, blocks=blocks)


def _run(body="", boilerplate="", brand="COC"):
    return brands.run(_page(body, boilerplate), _Cfg(brand))


# --- the defect the client reported ---

def test_a_sister_brand_in_body_copy_is_reported():
    fs = _run("Detox and residential care at Gratitude Lodge is available to every client.")
    assert len(fs) == 1
    assert fs[0].details["other_brand"] == "GL"
    assert fs[0].severity is Severity.WARNING          # a question, never a hard call


def test_the_wording_is_a_question_not_an_accusation():
    fs = _run("Our team at Renaissance Recovery can help.")
    assert len(fs) == 1
    low = fs[0].suggestion.lower()
    assert "question" in low and "not a confirmed fault" in low


# --- "Connections" is an ordinary English word: 100% false positive rate when measured ---

@pytest.mark.parametrize("sentence", [
    "these therapeutic structures while allowing clients to preserve vital connections to their "
    "everyday obligations",
    "reducing conflicts and enabling more meaningful connections with loved ones",
    "Through years dedicated to service and building connections with others in recovery",
    "integrating government-approved medications with counseling sessions, peer connections, and "
    "whole-person wellness practices",
])
def test_the_english_word_connections_is_never_a_brand_mention(sentence):
    """Verbatim from live RR, CAD, AR and GL pages. Every occurrence of the bare word measured
    across 95 pages was ordinary English — which is why the brand is keyed on its real name,
    "Connections Mental Health"."""
    assert _run(sentence, brand="RR") == []


def test_the_real_connections_name_is_still_matched():
    fs = _run("Referrals are handled by Connections Mental Health.", brand="RR")
    assert len(fs) == 1 and fs[0].details["other_brand"] == "COC"


# --- case carries the signal: a name is capitalised, a phrase is not ---

def test_a_generic_phrase_in_lower_case_is_not_a_brand():
    assert _run("If you need support right now, call an addiction hotline.", brand="RR") == []


def test_the_same_words_capitalised_as_a_name_are_matched():
    fs = _run("Calls are routed through Addiction Hotline.", brand="RR")
    assert len(fs) == 1 and fs[0].details["other_brand"] == "AH"


# --- region: footer network lists are the dominant legitimate pattern ---

def test_a_sister_brand_in_the_footer_is_ignored():
    """RR, CAD and TDRC all carry sister names in footer boilerplate and none in body copy."""
    assert _run(body="Welcome to our treatment centre.",
                boilerplate="Renaissance Recovery · Gratitude Lodge · California Detox") == []


# --- a brand naming ITSELF, or its parent ---

def test_a_brand_naming_itself_is_not_a_leak():
    assert _run("Gratitude Lodge offers detox in Long Beach.", brand="GL") == []


def test_naming_the_parent_brand_is_normal_attribution():
    """Every site attributes itself to District Behavioral Health; RR does it on 8 of 12 pages.
    The parent is deliberately absent from the searched name table."""
    assert _run("connected with the District Behavioral Health system", brand="RR") == []


def test_tdrcs_own_nap_name_is_a_dbh_one():
    """TDRC's NAP Name is literally "District Behavioral Health-Addiction Rehab … Huntington
    Beach", so DBH wording on a TDRC page is self-reference."""
    assert _run("District Behavioral Health - Huntington Beach", brand="TDRC") == []


# --- the rarity collapse: the gate that actually decides ---

def _finding(other, url):
    return Finding(url=url, check="brands", severity=Severity.WARNING,
                   fingerprint=f"brands:sister_brand:{url}:{other}", issue="x",
                   location="page body", snippet="x", suggestion="x",
                   details={"class": "sister_brand", "other_brand": other})


def test_a_site_wide_mention_is_deliberate_and_dropped():
    """AH names every sister brand on 10 of 11 pages — required disclosure copy. DBH lists every
    facility on 11 of 12. Neither is a defect."""
    fs = [_finding("GL", f"https://addictionhotline.com/p{i}/") for i in range(90)]
    assert _collapse_brands(fs, audited_pages=100) == []


def test_a_rare_mention_survives():
    fs = [_finding("GL", "https://connectionsoc.com/p1/"),
          _finding("GL", "https://connectionsoc.com/p2/")]
    assert len(_collapse_brands(fs, audited_pages=500)) == 2


def test_nothing_is_claimed_without_a_baseline():
    """On a small run there is no way to judge whether a mention is rare, so the check reports
    nothing rather than reporting it wrongly."""
    fs = [_finding("GL", "https://connectionsoc.com/p1/")]
    assert _collapse_brands(fs, audited_pages=12) == []


def test_the_collapse_leaves_other_checks_alone():
    other = Finding(url="u", check="phone", severity=Severity.ERROR, fingerprint="phone:x",
                    issue="i", location="l", snippet="s", suggestion="g", details={})
    assert _collapse_brands([other], audited_pages=5) == [other]


# --- guards added after the first live run, each from a verbatim live sentence ---

@pytest.mark.parametrize("sentence", [
    "Through our partnership with Renaissance Recovery within the District Behavioral Health "
    "system, clients keep the same care team.",
    "Within the District Behavioral Health network, individuals can advance to Renaissance "
    "Recovery, where pet-welcoming housing is available.",
])
def test_copy_that_explains_the_relationship_is_deliberate(sentence):
    """Live GL and AR sentences. Wording that spells out the partnership is wording somebody
    wrote on purpose."""
    assert _run(sentence, brand="GL") == []


def test_a_page_listing_several_sister_brands_is_a_directory():
    """RR's /about-us/sober-living-gallery names Alliance Recovery AND District Recovery
    Community; its /map lists the network. Those are directories, not leaks."""
    body = ("Sober Living Orange County Alliance Recovery. "
            "Sober Living Southern California The District Recovery Community.")
    assert _run(body, brand="RR") == []


def test_one_wrong_brand_on_a_page_still_reports():
    """The real defect found live: connectionsoc.com serves an archived page design whose copy is
    California Detox's. Exactly one foreign brand, no relationship wording."""
    fs = _run("Ocean views during treatment. California Detox Features. Personalized care.",
              brand="COC")
    assert len(fs) == 1 and fs[0].details["other_brand"] == "CAD"


def test_a_page_about_a_sister_facility_is_not_a_leak():
    """DBH, the parent, publishes a page per facility:
    /our_locations/gratitude-lodge-long-beach-ca titled "Gratitude Lodge - Long Beach, CA".
    The brand is the page's SUBJECT."""
    page = ParsedPage(
        url="https://districtbehavioralhealth.com/our_locations/gratitude-lodge-long-beach-ca",
        title="Gratitude Lodge - Long Beach, CA - District Behavioral Health",
        blocks=[Block(tag="p", text="Gratitude Lodge offers detox and residential care.",
                      region="body", group=0)])
    assert brands.run(page, _Cfg("DBH")) == []


def test_the_subject_guard_does_not_swallow_a_real_leak():
    page = ParsedPage(
        url="https://connectionsoc.com/archived-page-designs/home-v2",
        title="Home v2 - Connections Mental Health",
        blocks=[Block(tag="p", text="California Detox Features. Personalized care.",
                      region="body", group=0)])
    fs = brands.run(page, _Cfg("COC"))
    assert len(fs) == 1 and fs[0].details["other_brand"] == "CAD"
