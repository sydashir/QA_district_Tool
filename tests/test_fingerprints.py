"""Every check emits a stable, per-invariant fingerprint (the diff keys on it).

A subtly-wrong invariant would propagate to every check + cache + diff, so pin the
exact fingerprint each check produces for representative findings.
"""
from __future__ import annotations

import asyncio

from auditor import audit
from auditor.checks import blank, links, meta, phone, placeholder, structure
from auditor.config import load_brand
from auditor.parse import Heading, Link, ParsedPage

CFG = load_brand("gl")
URL = "https://x/p/"


def _fps(findings):
    return {f.fingerprint for f in findings}


def test_structure_fingerprints():
    p = ParsedPage(url=URL, headings=[Heading(1, "A"), Heading(1, "Contact (Pillar)"),
                                      Heading(3, "5000+")], visible_text="x" * 600)
    fps = _fps(structure.run(p, CFG))
    assert "heading_structure:multi_h1:https://x/p/" in fps
    assert "heading_structure:label_leak:https://x/p/:Contact (Pillar)" in fps
    assert any(f.startswith("heading_structure:skipped:https://x/p/:H1->H3") for f in fps)


def test_blank_fingerprints():
    p = ParsedPage(url=URL, headings=[], visible_text="short")
    fps = _fps(blank.run(p, CFG))
    assert {"blank:missing_h1:https://x/p/", "blank:thin:https://x/p/"} <= fps


def test_placeholder_fingerprints(monkeypatch):
    monkeypatch.setattr(placeholder, "_extract_acf_tokens",
                        lambda: (lambda t: ["geo"] if "[acf" in t else []))
    p = ParsedPage(url=URL, visible_text="text [acf field=geo] and {{foo}}")
    fps = _fps(placeholder.run(p, CFG))
    assert "placeholder:acf:https://x/p/:geo" in fps
    assert "placeholder:curly:https://x/p/:{{foo}}" in fps


def test_phone_fingerprints():
    # tel: dials the retired 800 number; display shows the canonical -> mismatch + non-canonical
    p = ParsedPage(url=URL, raw_html='<a href="tel:+18006929850">(844) 576-0144</a>',
                   visible_text="call (844) 576-0144")
    fps = _fps(phone.run(p, CFG))
    assert "phone:mismatch:https://x/p/:+18006929850" in fps
    assert "phone:non_canonical:https://x/p/:+18006929850" in fps


def test_meta_fingerprints():
    p = ParsedPage(url="https://x/Bad_Slug/", title=None, meta_description=None)
    fps = _fps(meta.run(p, CFG))
    assert {"meta:missing_title:https://x/Bad_Slug/",
            "meta:missing_desc:https://x/Bad_Slug/",
            "meta:malformed_slug:https://x/Bad_Slug/"} <= fps


def test_cross_page_dup_fingerprint():
    p1 = ParsedPage(url="https://x/a/", title="Same Title")
    p2 = ParsedPage(url="https://x/b/", title="Same Title")
    assert "meta:dup:title:same title" in _fps(audit._cross_page_duplicates([p1, p2]))


def test_links_fingerprint_is_target():
    class _Resp:
        def __init__(self, code, url):
            self.status_code, self.url = code, url

    class _Client:
        async def request(self, method, url, headers=None):
            return _Resp(404, url)

    p = ParsedPage(url=URL, links=[Link(href="/dead", url="https://x/dead")])
    findings, _stats = asyncio.run(links.check_links([p], _Client(), CFG))
    assert any(f.fingerprint == "broken_links:https://x/dead" for f in findings)
