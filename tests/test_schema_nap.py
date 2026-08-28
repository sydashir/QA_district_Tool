"""Business NAME and ADDRESS in the machine-readable block, checked against the client's own NAP.

The name finding is the headline from the NAP investigation: **37 of 38 GL pages carry TWO business
nodes** — a correct `MedicalBusiness` "Gratitude Lodge" AND a `LocalBusiness` whose name is an
internal CMS label, e.g. `[NoIndexed] Kratom (Plant) (DrugInfo Blog)`. It is SCHEMA-ONLY: the
`<title>`, `og:title` and `og:site_name` are all correct, so nothing a human sees is wrong and only
a machine reading the page is misinformed. **RR is clean on all 40 pages sampled** — the in-house
correct example, and the control this check must not fire on.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from auditor.checks import schema
from auditor.nap import NapLocation
from auditor.parse import parse_html


def _page(nodes, url="https://www.gratitudelodge.com/x/"):
    ld = json.dumps(nodes if isinstance(nodes, list) else [nodes])
    html = f'<html><head><script type="application/ld+json">{ld}</script></head><body>x</body></html>'
    return parse_html(html, page_url=url)


GL_LOCS = [NapLocation(brand="gl", name="Gratitude Lodge-Drug & Alcohol Addiction Rehab Center",
                       address="3849 Chatwin Ave, Long Beach, CA 90808")]


def _cfg(brand="gl", base="https://www.gratitudelodge.com", locs=None):
    return SimpleNamespace(brand=brand, base_url=base, canonical_phones=[],
                           canon=None, brand_numbers={},
                           nap_locations=GL_LOCS if locs is None else locs)


def _classes(findings):
    return [f.details.get("class") for f in findings]


def test_an_internal_cms_label_as_the_business_name_is_reported():
    """The real GL string. A machine reading this page is told the business is called
    "[NoIndexed] Kratom (Plant) (DrugInfo Blog)"."""
    page = _page({"@type": "LocalBusiness", "name": "[NoIndexed] Kratom (Plant) (DrugInfo Blog)",
                  "telephone": "844-576-0144", "address": "3849 Chatwin Ave, Long Beach, CA 90808"})
    out = schema.run(page, _cfg())
    assert "business_name_internal" in _classes(out)


def test_the_brands_own_name_is_never_reported():
    """RR is clean on all 40 pages sampled — the control. A check that fires here is unusable."""
    page = _page({"@type": "MedicalBusiness", "name": "Gratitude Lodge",
                  "telephone": "844-576-0144", "address": "3849 Chatwin Ave, Long Beach, CA 90808"})
    assert "business_name_internal" not in _classes(schema.run(page, _cfg()))


def test_a_longer_name_that_still_contains_the_brand_is_fine():
    """The NAP names themselves are long — "Gratitude Lodge-Drug & Alcohol Addiction Rehab Center
    Long Beach, CA". Requiring an exact match would flag the client's own official names."""
    page = _page({"@type": "LocalBusiness",
                  "name": "Gratitude Lodge-Drug & Alcohol Addiction Rehab Center Long Beach, CA",
                  "telephone": "844-576-0144", "address": "3849 Chatwin Ave, Long Beach, CA 90808"})
    assert "business_name_internal" not in _classes(schema.run(page, _cfg()))


def test_a_brands_own_parent_alias_is_not_an_internal_label():
    """TDRC's NAP name is literally a DBH-prefixed one. `brands.SELF_ALIASES` already settled this
    distinction; the name check must honour it rather than re-deciding it."""
    page = _page({"@type": "LocalBusiness",
                  "name": "District Behavioral Health-Addiction Rehab Recovery & Sober Living",
                  "telephone": "888-871-2088", "address": "19671 Beach Blvd suite 442, Huntington Beach, CA 92648"},
                 url="https://thedistrictrecoverycommunity.com/x/")
    cfg = _cfg(brand="tdrc", base="https://thedistrictrecoverycommunity.com",
               locs=[NapLocation(brand="tdrc",
                                 name="District Behavioral Health-Addiction Rehab Recovery & Sober Living",
                                 address="19671 Beach Blvd suite 442, Huntington Beach, CA 92648")])
    assert "business_name_internal" not in _classes(schema.run(page, cfg))


# --- address ------------------------------------------------------------------------------------

def test_an_address_matching_the_nap_is_not_reported():
    page = _page({"@type": "LocalBusiness", "name": "Gratitude Lodge", "telephone": "844-576-0144",
                  "address": {"@type": "PostalAddress", "streetAddress": "3849 Chatwin Ave",
                              "addressLocality": "Long Beach", "addressRegion": "CA",
                              "postalCode": "90808"}})
    assert "address_not_in_nap" not in _classes(schema.run(page, _cfg()))


def test_punctuation_and_abbreviation_differences_do_not_make_a_mismatch():
    """GL publishes 2 addresses and both match the NAP exactly after normalisation. A checker that
    demanded byte equality would report both as wrong."""
    page = _page({"@type": "LocalBusiness", "name": "Gratitude Lodge", "telephone": "844-576-0144",
                  "address": "3849 Chatwin Avenue, Long Beach, California 90808"})
    assert "address_not_in_nap" not in _classes(schema.run(page, _cfg()))


def test_an_address_that_is_in_no_nap_entry_is_reported():
    page = _page({"@type": "LocalBusiness", "name": "Gratitude Lodge", "telephone": "844-576-0144",
                  "address": "1 Nowhere Rd, Springfield, CA 99999"})
    assert "address_not_in_nap" in _classes(schema.run(page, _cfg()))


def test_no_address_findings_at_all_when_the_brand_has_no_nap_locations():
    """DBH has zero locations in the tab. Checking against an empty set would report every address
    on the site as unknown — a check that fires everywhere because it knows nothing."""
    page = _page({"@type": "LocalBusiness", "name": "District Behavioral Health",
                  "telephone": "888-707-6073", "address": "1 Nowhere Rd, Springfield, CA 99999"},
                 url="https://districtbehavioralhealth.com/x/")
    cfg = _cfg(brand="dbh", base="https://districtbehavioralhealth.com", locs=[])
    assert "address_not_in_nap" not in _classes(schema.run(page, cfg))
