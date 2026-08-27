"""Structured data (schema.org JSON-LD) — markup no human can see.

Every fixture below is shaped from what the nine live sites actually publish (318 JSON-LD blocks
surveyed 2026-08-27), not invented: two brands ship no business schema at all, one ships a block
that is not valid JSON, four ship an Organization carrying only name and url, and one is complete.
"""
from __future__ import annotations

import pytest

from auditor.checks import schema
from auditor.parse import ParsedPage
from auditor.report import Severity


class _Cfg:
    """Mirrors the real BrandConfig surface the check reads."""

    def __init__(self, brand="GL", base_url="https://www.gratitudelodge.com",
                 canon=None, brand_numbers=None):
        self.brand = brand
        self.base_url = base_url
        self.canonical_phones = ["844-576-0144"]
        self.canon = canon
        self.brand_numbers = brand_numbers or {}


class _Canon:
    def __init__(self, numbers):
        self._n = set(numbers)

    def current_set(self):
        return self._n


def _page(html: str, url="https://www.gratitudelodge.com/x/"):
    return ParsedPage(url=url, raw_html=html)


def _ld(body: str) -> str:
    return f'<html><head><script type="application/ld+json">{body}</script></head><body></body></html>'


def _classes(html, cfg=None, url="https://www.gratitudelodge.com/x/"):
    return [f.details["class"] for f in schema.run(_page(html, url), cfg or _Cfg())]


# --------------------------------------------------------------------------- missing
def test_a_page_with_no_business_schema_is_reported():
    """DBH publishes none on any of 40 sampled pages; MHD none on any of 7."""
    assert "missing" in _classes("<html><head></head><body>hi</body></html>")


def test_a_page_whose_only_schema_is_an_article_is_still_missing_a_business():
    assert "missing" in _classes(_ld('{"@type":"Article","headline":"x"}'))


def test_a_complete_local_business_is_clean():
    """GL's shape — the in-house correct example."""
    assert _classes(_ld(
        '{"@type":"MedicalBusiness","name":"Gratitude Lodge","telephone":"+1-844-576-0144",'
        '"address":{"@type":"PostalAddress","streetAddress":"1 Main St"},"url":"https://x/"}'),
        _Cfg(canon=_Canon({"+18445760144"}))) == []


def test_a_business_inside_an_at_graph_is_found():
    """Rank Math nests everything under @graph; missing this would report every such page."""
    assert _classes(_ld(
        '{"@context":"https://schema.org","@graph":[{"@type":"WebPage"},'
        '{"@type":"Organization","name":"X","url":"https://x/"}]}')) == []


# --------------------------------------------------------------------------- invalid json
def test_a_block_that_is_not_valid_json_is_reported():
    """COC ships JavaScript inside a JSON-LD tag — the code that was meant to BUILD the schema was
    emitted AS the schema: `' + JSON.stringify(schemaData) + '<\\/script>');`"""
    html = _ld("' + JSON.stringify(schemaData) + '<\\/script>');")
    out = schema.run(_page(html), _Cfg())
    classes = [f.details["class"] for f in out]
    # BOTH are correct: the block is broken, AND the page is left with no business schema at all
    assert "invalid_json" in classes and "missing" in classes
    assert next(f for f in out if f.details["class"] == "invalid_json").severity is Severity.ERROR


# --------------------------------------------------------------------------- completeness
def test_a_local_business_with_no_contact_details_is_a_warning_not_an_error():
    """Google RECOMMENDS telephone and address on a local business; schema.org does not require
    them. We do not report a failure against a standard the client never adopted."""
    out = schema.run(_page(_ld('{"@type":"LocalBusiness","name":"X","url":"https://x/"}')), _Cfg())
    incomplete = [f for f in out if f.details["class"] == "incomplete"]
    assert incomplete and incomplete[0].severity is Severity.WARNING
    assert set(incomplete[0].details["missing"]) == {"telephone", "address"}


def test_a_bare_organization_is_not_held_to_the_local_business_bar():
    """AH, AR, CAD and COC use Organization with only name+url. That is valid markup, so it is not
    reported as incomplete — only a LOCAL business is expected to carry contact details."""
    assert _classes(_ld('{"@type":"Organization","name":"X","url":"https://x/"}')) == []


# --------------------------------------------------------------------------- phones
def test_a_sister_brands_number_in_the_markup_is_an_error():
    cfg = _Cfg(canon=_Canon({"+18445760144"}), brand_numbers={"+18663309449": ["RR"]})
    out = schema.run(_page(_ld(
        '{"@type":"MedicalBusiness","name":"X","telephone":"+1-866-330-9449",'
        '"address":{"@type":"PostalAddress"}}')), cfg)
    cross = [f for f in out if f.details["class"] == "phone_cross_brand"]
    assert cross and cross[0].severity is Severity.ERROR and cross[0].details["owner"] == "RR"


def test_a_local_facility_line_from_the_nap_sheet_is_not_flagged():
    """GL's per-location numbers are IN the NAP sheet. Using only the national list reported 16 of
    GL's own facility lines as unknown."""
    cfg = _Cfg(canon=_Canon({"+18445760144", "+15623301644"}))
    assert _classes(_ld(
        '{"@type":"MedicalBusiness","name":"X","telephone":"+1-562-330-1644",'
        '"address":{"@type":"PostalAddress"}}'), cfg) == []


def test_an_unrecognised_number_is_only_a_warning():
    # a REAL-format number that is in nobody's set. (555 numbers normalise to None — phonenumbers
    # rejects them as fictional, which is why the first draft of this test found nothing.)
    cfg = _Cfg(canon=_Canon({"+18445760144"}))
    out = schema.run(_page(_ld(
        '{"@type":"MedicalBusiness","name":"X","telephone":"+1-213-555-0199",'
        '"address":{"@type":"PostalAddress"}}')), cfg)
    unknown = [f for f in out if f.details["class"] == "phone_unknown"]
    assert unknown and unknown[0].severity is Severity.WARNING


# --------------------------------------------------------------------------- off-brand
@pytest.mark.parametrize("url", [
    "https://www.renaissancerecovery.com/review-us/orange-county-ca/",   # TDRC 301s land here
    "https://www.google.com/maps/place//data=!4m3",                      # in AH's page list
])
def test_a_page_that_redirected_onto_another_domain_is_not_judged(url):
    """Judging these manufactured 8 cross-brand ERRORs on TDRC and 1 missing on AH — findings about
    sites we were not auditing."""
    assert schema.run(_page("<html><head></head><body></body></html>", url), _Cfg()) == []
