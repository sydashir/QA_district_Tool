"""Links pointing at cleanup pages (B10) and at feed addresses (B11).

**B10 is confirmed live on five brands.** Scanning every link target in the crawl cache
(16,564 pages) found the client's own reported URL still linked:

    RR   rehab-admissions-old/            linked from 56 pages
    GL   adderall-detox-delete/           linked from 1,204 pages
    GL   testimonial-reviews-pillar-copy/ 2 pages
    AH   thank-you-old/                   1 page
    COC  archived-page-designs/home-v3-copy/  1 page
    CAD  home-old/                        1 page

**B11 measured ZERO across the same 16,564 pages.** No site links to a /feed/ address from page
content — WordPress declares feeds in `<head>` via `<link rel="alternate">`, which is how Google
found the 960 crawled-not-indexed URLs the client reported. That is a site-wide feed setting, not
a page defect, and flagging the `<head>` declaration would fire on every page of every brand for
stock WordPress behaviour. The check is kept because a genuine content link to a feed is a real
mistake and costs nothing to watch for; it simply does not fire today.
"""
from __future__ import annotations

import pytest

from auditor.checks.enumeration import CRUFT_RE
from auditor.checks.links import _FEED_RE, _is_internal


@pytest.mark.parametrize("path", [
    "/drug/rehab/rehab-admissions-old/",        # the client's reported URL, live on 56 RR pages
    "/adderall-detox-delete/",                  # linked from 1,204 GL pages
    "/testimonial-reviews-pillar-copy/",
    "/thank-you-old/",
    "/archived-page-designs/home-v3-copy/",
    "/home-old/",
    "/page-copy-2/",
])
def test_cleanup_slugs_are_recognised(path):
    assert CRUFT_RE.search(path), path


@pytest.mark.parametrize("path", [
    "/gold-coast-rehab/",       # "old" inside a word
    "/scoliosis-copywriting/",
    "/our-facilities/",
    "/oldsmar-fl/",             # a real Florida city
    "/copyright/",
])
def test_ordinary_paths_are_not_cleanup_slugs(path):
    assert not CRUFT_RE.search(path), path


@pytest.mark.parametrize("path", ["/feed/", "/blog/feed", "/comments/feed/", "/rss/", "/atom/"])
def test_feed_addresses_are_recognised(path):
    assert _FEED_RE.search(path), path


@pytest.mark.parametrize("path", ["/feedback/", "/feeding-tube-care/", "/rss-explained/"])
def test_words_containing_feed_are_not_feed_addresses(path):
    assert not _FEED_RE.search(path), path


def test_only_our_own_urls_count():
    """Another site's URL shape is not our defect — a partner blog's /feed/ or an external
    -old page is none of our business.

    A SUBDOMAIN counts as external here, because `_registrable` strips only "www." and the rest
    of links.py already draws its internal/external line the same way. That matters only for the
    PPC subdomains (help.rr / help.gl), whose hostnames are still unknown and which are not crawl
    targets — see CLAUDE.md Open questions.
    """
    assert _is_internal("https://www.gratitudelodge.com/a-old/", "gratitudelodge.com")
    assert not _is_internal("https://help.gratitudelodge.com/a-old/", "gratitudelodge.com")
    assert not _is_internal("https://someblog.com/feed/", "gratitudelodge.com")
