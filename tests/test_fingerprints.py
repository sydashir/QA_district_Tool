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
from auditor.report import Finding, Severity, dedupe_findings

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


def test_dedupe_collapses_by_fingerprint():
    a = Finding(url="u", check="c", fingerprint="fp:1", severity=Severity.WARNING, issue="x")
    a2 = Finding(url="u", check="c", fingerprint="fp:1", severity=Severity.WARNING, issue="x")
    b = Finding(url="u", check="c", fingerprint="fp:2", severity=Severity.WARNING, issue="y")
    assert {f.fingerprint for f in dedupe_findings([a, a2, b])} == {"fp:1", "fp:2"}


def test_dedupe_keeps_and_warns_on_different_content(caplog):
    # same fingerprint, DIFFERENT content = scheme flaw, not a duplicate -> keep both, warn loud
    a = Finding(url="u", check="c", fingerprint="fp:x", severity=Severity.WARNING, issue="problem A")
    b = Finding(url="u", check="c", fingerprint="fp:x", severity=Severity.ERROR, issue="problem B")
    with caplog.at_level("WARNING"):
        out = dedupe_findings([a, b])
    assert len(out) == 2  # both kept — never silently dropped
    assert any("collision" in r.getMessage() for r in caplog.records)


def test_skipped_twice_is_one_identity():
    # the real limit=50 collision: two identical H1->H3 skips (same text) on one page ->
    # same fingerprint, byte-identical, collapses to one. A DIFFERENT problem would differ.
    p = ParsedPage(url=URL, headings=[
        Heading(1, "Title"), Heading(3, "Repeated"),
        Heading(1, "Title2"), Heading(3, "Repeated")])
    raw = structure.run(p, CFG)
    skips = [f for f in raw if ":skipped:" in f.fingerprint]
    assert len(skips) == 2 and skips[0].fingerprint == skips[1].fingerprint
    assert (skips[0].issue, skips[0].location) == (skips[1].issue, skips[1].location)  # identical
    deduped = dedupe_findings(raw)
    assert len(deduped) == len({f.fingerprint for f in raw})  # unique fingerprints after dedupe
