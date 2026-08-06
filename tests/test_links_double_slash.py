"""A doubled slash inside a URL path — reported by the client, still live today.

Connor, 2026-06-05: *"The location links on https://www.gratitudelodge.com/locations/ have double
// at the end. The button such as 'Newport Beach location' has a double slash //"*. Verified still
live on 2026-08-06: `https://www.renaissancerecovery.com//facility/renaissance-recovery-nashville-tn/`.

Worth flagging even though it usually still resolves: a doubled slash is a different URL to Google,
so it splits link equity and can be indexed as a duplicate. It is also a template bug — a base URL
concatenated with a path that already had its leading slash.
"""
from __future__ import annotations

import pytest

from auditor.checks.links import has_double_slash
from auditor.report import Severity


@pytest.mark.parametrize("url", [
    "https://www.renaissancerecovery.com//facility/renaissance-recovery-nashville-tn/",
    "https://www.gratitudelodge.com/locations//newport-beach/",
    "http://example.com/a//b",
    "https://example.com/trailing//",
])
def test_a_doubled_slash_in_the_path_is_caught(url):
    assert has_double_slash(url), url


@pytest.mark.parametrize("url", [
    "https://www.gratitudelodge.com/locations/newport-beach/",
    "https://example.com/",
    "https://example.com",
    "//cdn.example.com/asset.js",                 # protocol-relative URL: legitimate
    "https://example.com/path?next=https://x.com/y",   # a URL inside a query string
    "mailto:someone@example.com",
    "tel:8887076073",
])
def test_ordinary_urls_are_not_flagged(url):
    assert not has_double_slash(url), url


def test_the_check_emits_a_finding_for_a_doubled_slash():
    import asyncio

    from auditor.checks.links import check_links
    from auditor.config import load_brand

    class _Page:
        url = "https://www.gratitudelodge.com/locations/"
        link_urls = ["https://www.renaissancerecovery.com//facility/nashville-tn/",
                     "https://www.gratitudelodge.com/locations/newport-beach/"]

    class _Client:
        async def request(self, *a, **k):
            raise AssertionError("a doubled slash is decidable without a network probe")

    findings, stats = asyncio.run(
        check_links([_Page()], _Client(), load_brand("gl"), max_links=0))
    ds = [f for f in findings if f.details.get("class") == "double_slash"]
    assert len(ds) == 1 and ds[0].severity is Severity.WARNING
    assert "//" in ds[0].suggestion
    assert stats["double_slash"] == 1
