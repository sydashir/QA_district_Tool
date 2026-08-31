"""Three hashed-file changes landed together, so the resume-cache invalidation is paid once.

1. `broken_links:redirected_internal` — an internal link that 301s instead of pointing at the live
   page. The data was already in hand and thrown away: `links.py` counted redirects in `stats` and
   never made a finding, so the client's task about exactly this (ClickUp 86bbbrpey) had no
   coverage at all.
2. `-2` slug detection — WordPress appends `-2` when a slug collides, so it is a reliable
   fingerprint of an accidental duplicate page (ClickUp 86b7xbvb9).
3. A SELECTOR on findings, so a screenshot can find the element the finding is about. Measured:
   without it the `display_dial_mismatch` locator resolved 50%; the check knows the anchor exactly
   and simply discarded it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from auditor.parse import ParsedPage


# --- 1. redirected internal links -----------------------------------------------------------------

def _classes(findings):
    return [(f.details or {}).get("class") for f in findings]


def test_an_internal_link_that_redirects_is_reported():
    from auditor.checks.links import _finding, _redirect_finding

    f = _redirect_finding("https://x/old/", ["https://x/p/"], "https://x/new/", 301)
    assert (f.details or {}).get("class") == "redirected_internal"
    assert "https://x/new/" in f.suggestion
    assert f.severity.value in ("info", "warning")


def test_the_redirect_finding_says_where_the_link_actually_lands():
    from auditor.checks.links import _redirect_finding

    f = _redirect_finding("https://x/old/", ["https://x/p/"], "https://x/new/", 301)
    assert f.details["final_url"] == "https://x/new/"
    assert f.details["target"] == "https://x/old/"


def test_a_redirect_is_never_an_error():
    """It works for the visitor. It costs a hop and some link equity — that is not an ERROR, and
    calling it one would drown the classes that do cost a phone call."""
    from auditor.checks.links import _redirect_finding

    f = _redirect_finding("https://x/old/", ["https://x/p/"], "https://x/new/", 301)
    assert f.severity.value != "error"


# --- 2. the WordPress -2 slug ---------------------------------------------------------------------

@pytest.mark.parametrize("url,flagged", [
    ("https://x/drug-rehab-2/", True),
    ("https://x/a/b/womens-rehab-2/", True),
    ("https://x/drug-rehab-2", True),
    ("https://x/drug-rehab/", False),
    ("https://x/top-2-rehabs/", False),      # -2 in the middle is ordinary wording
    ("https://x/step-12-program/", False),
    ("https://x/covid-19/", False),
])
def test_a_collision_suffix_slug_is_recognised(url, flagged):
    from auditor.checks.meta import _is_collision_slug

    assert _is_collision_slug(url) is flagged


def test_the_duplicate_slug_finding_explains_what_wordpress_did():
    from auditor.checks import meta

    page = ParsedPage(url="https://x/womens-rehab-2/", title="Women's Rehab",
                      meta_description="d" * 80, raw_html="<html><body>x</body></html>")
    cfg = SimpleNamespace(brand="gl", base_url="https://x", thresholds=None)
    out = [f for f in meta.run(page, cfg) if (f.details or {}).get("class") == "collision_slug"]
    assert out, "a -2 slug should be reported"
    assert "-2" in out[0].suggestion


# --- 3. the selector, so a finding can be photographed ---------------------------------------------

def test_a_dead_cta_records_the_element_it_flagged():
    # Through `parse_html`, not a hand-built ParsedPage: `actions` reads `parsed.actionables`, which
    # only the real parser fills. A hand-built page has none, so the check finds nothing and the
    # test passes for the wrong reason — a double kinder than the thing it stands in for.
    from auditor.checks import actions
    from auditor.parse import parse_html

    html = ('<html><body><a href="#" class="btn">Verify Your Insurance</a>'
            '<p>' + "x" * 400 + '</p></body></html>')
    page = parse_html(html, page_url="https://x/p/")
    cfg = SimpleNamespace(brand="gl", base_url="https://x")
    dead = [f for f in actions.run(page, cfg) if (f.details or {}).get("class") == "dead_cta"]
    assert dead, "sanity: the fixture is a dead CTA"
    assert dead[0].details.get("selector"), "no selector recorded — a screenshot cannot find it"


def test_the_recorded_selector_actually_matches_the_element():
    """A selector that does not resolve is worse than none: it sends the screenshot pass looking
    for something that is not there, and the miss is indistinguishable from a page that changed."""
    from bs4 import BeautifulSoup

    from auditor.checks import actions
    from auditor.parse import parse_html

    html = ('<html><body><div class="wrap"><a href="#" id="cta1">Verify Your Insurance</a></div>'
            '<p>' + "x" * 400 + '</p></body></html>')
    page = parse_html(html, page_url="https://x/p/")
    cfg = SimpleNamespace(brand="gl", base_url="https://x")
    dead = [f for f in actions.run(page, cfg) if (f.details or {}).get("class") == "dead_cta"]
    sel = dead[0].details["selector"]
    assert BeautifulSoup(html, "lxml").select(sel), f"selector {sel!r} matches nothing"
