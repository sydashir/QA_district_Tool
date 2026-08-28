"""A sitemap URL that 301s onto ANOTHER BRAND'S domain.

TDRC's `/review-us/{code}` URLs are deliberate redirects to GL, RR and CAD review pages — 7 of 19
sampled TDRC pages. `parsed.url` is always the URL we REQUESTED, so the page that came back belonged
to another brand while every brand-scoped check judged it as this brand's: `phone` compared it to
the wrong canonical numbers, `brands` read the owner's own name as an intruder, `schema` produced 6
false `phone_cross_brand` on TDRC, and `misspelling` fed another brand's vocabulary into this
brand's typo ledger.

The fix is one change at the audit level, not five in the checks. These tests pin both halves of it:
brand-scoped checks must not run on such a page, AND the page must still be REPORTED — a sitemap
advertising pages the brand does not own is itself a defect, and skipping quietly would trade a
false finding for a hidden one.
"""
from __future__ import annotations

from auditor import audit
from auditor.config import load_brand
from auditor.crawl import FetchResult
from auditor.parse import parse_html

_HTML = ("<html><head><title>Gratitude Lodge Reviews</title></head><body><h1>Reviews</h1>"
         "<p>Call Gratitude Lodge at 844-576-0144 for treatment options today.</p>"
         "<a href='tel:8445760144'>844-576-0144</a></body></html>")


def _project_for(requested: str, landed: str, brand: str = "tdrc"):
    cfg = load_brand(brand)
    parsed = parse_html(_HTML, page_url=requested, base_url=cfg.base_url)
    r = FetchResult(url=requested, status=200, final_url=landed, text=_HTML, error=None)
    return audit._project(parsed, r, cfg), cfg


def test_a_page_that_lands_on_another_brand_runs_no_brand_scoped_checks():
    """The whole point. GL's phone number on a GL page is correct; judged as TDRC it is a
    cross-brand defect that does not exist."""
    proj, _ = _project_for("https://thedistrictrecoverycommunity.com/review-us/gl-long-beach",
                           "https://www.gratitudelodge.com/review-us/long-beach-ca/")
    checks = {f.check for f in proj.intrinsic_findings}
    assert checks == {"enumeration"}, f"brand-scoped checks still ran: {sorted(checks - {'enumeration'})}"


def test_the_skipped_page_is_reported_not_silently_dropped():
    """Skipping quietly swaps a false finding for a hidden one. The sitemap is advertising a page
    this brand does not own, and that is worth telling the client."""
    proj, _ = _project_for("https://thedistrictrecoverycommunity.com/review-us/gl-long-beach",
                           "https://www.gratitudelodge.com/review-us/long-beach-ca/")
    assert len(proj.intrinsic_findings) == 1
    f = proj.intrinsic_findings[0]
    assert f.details["class"] == "redirects_off_brand"
    assert "gratitudelodge.com" in f.suggestion          # says where it actually landed
    assert "review-us/gl-long-beach" in f.url            # and which sitemap URL did it


def test_an_ordinary_redirect_inside_the_brand_is_left_alone():
    """http->https and /x -> /x/ are the common case and must stay fully audited, or the fix
    silences the entire corpus."""
    proj, _ = _project_for("http://thedistrictrecoverycommunity.com/about-us",
                           "https://thedistrictrecoverycommunity.com/about-us/")
    assert not any(f.details.get("class") == "redirects_off_brand" for f in proj.intrinsic_findings)
    assert {f.check for f in proj.intrinsic_findings} != {"enumeration"}


def test_a_subdomain_of_the_same_brand_is_not_off_brand():
    """help.renaissancerecovery.com is still RR. Registrable domain, not hostname."""
    proj, _ = _project_for("https://www.renaissancerecovery.com/x",
                           "https://help.renaissancerecovery.com/x", brand="rr")
    assert not any(f.details.get("class") == "redirects_off_brand" for f in proj.intrinsic_findings)


def test_another_brands_page_cannot_pollute_cross_page_duplicate_detection():
    """The projection feeds duplicate-title/h1 detection across the brand. Carrying the other
    brand's title would report a duplicate between two brands' pages."""
    proj, _ = _project_for("https://thedistrictrecoverycommunity.com/review-us/gl-long-beach",
                           "https://www.gratitudelodge.com/review-us/long-beach-ca/")
    assert proj.title is None and proj.h1_text is None and proj.meta_description is None
    assert proj.link_urls == []


def test_the_diff_knows_this_finding_is_minted_in_audit_py():
    """`enumeration` was scoped to enumeration.py + crawl.py. This class is minted in audit.py, so
    without adding it a change to the redirect logic makes these findings read as `resolved` —
    "someone fixed it" — when nothing on the site changed. Same trap as the collapse list."""
    from auditor.diff import _CHECK_COMPONENT
    assert "src:audit.py" in _CHECK_COMPONENT["enumeration"]
