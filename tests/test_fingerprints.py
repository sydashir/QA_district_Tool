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


def test_dials_retired_is_error():
    # button DIALS the retired 800 (display shows canonical). Dialing a dead line is airtight ERROR
    # regardless of call-routing intent -> "dials_retired". Number is also present -> "retired".
    p = ParsedPage(url=URL, raw_html='<a href="tel:+18006929850">(844) 576-0144</a>',
                   visible_text="call (844) 576-0144")
    fs = phone.run(p, CFG)
    fps = _fps(fs)
    assert "phone:dials_retired:https://x/p/:+18006929850" in fps
    assert "phone:retired:https://x/p/:+18006929850" in fps
    assert next(f for f in fs if "dials_retired" in f.fingerprint).severity is Severity.ERROR


def test_display_dial_mismatch_to_live_number_is_a_warning_question():
    # shows a local number, dials the LIVE national line -> NOT asserted a bug (likely call-tracking);
    # a WARNING framed as a question, never ERROR.
    p = ParsedPage(url=URL, raw_html='<a href="tel:+18445760144">562-330-1644</a>', visible_text="")
    fs = [f for f in phone.run(p, CFG) if "display_dial_mismatch" in f.fingerprint]
    assert len(fs) == 1 and fs[0].severity is Severity.WARNING
    assert "call-tracking" in fs[0].suggestion


def test_title_bounds_come_from_config():
    # P4: an 80-char title flags under title_max=70 but NOT under GL's 88 -> the ruler is
    # per-brand config, not a module constant.
    from auditor.config import Thresholds
    p = ParsedPage(url=URL, title="x" * 80, meta_description="d" * 50)
    c = load_brand("gl")
    c.thresholds = Thresholds(title_max=70)
    assert any("meta:title_length" in fp for fp in _fps(meta.run(p, c)))
    c.thresholds = Thresholds(title_max=88)
    assert not any("meta:title_length" in fp for fp in _fps(meta.run(p, c)))


def test_encoded_tel_is_decoded_then_classified():
    # a retired number hidden behind %20 must be caught as RETIRED, not buried as "malformed" —
    # and the encoding itself is kept as its own finding (both).
    p = ParsedPage(url=URL, raw_html='<a href="tel:%20800-692-9850">call</a>', visible_text="")
    fps = _fps(phone.run(p, CFG))
    assert f"phone:encoded_tel:{URL}:%20800-692-9850" in fps   # the encoding defect
    assert f"phone:retired:{URL}:+18006929850" in fps          # the number behind it, classified


def test_third_party_hotline_is_info_not_unknown():
    # Poison Control on a page is EXPECTED (client-documented), not an unknown-number defect.
    assert "+18002221222" in CFG.third_party
    p = ParsedPage(url=URL, raw_html="", visible_text="In an emergency call 1-800-222-1222")
    fs = [f for f in phone.run(p, CFG) if f.check == "phone"]
    assert len(fs) == 1
    assert fs[0].details["class"] == "third_party" and fs[0].severity is Severity.INFO
    assert "third_party" in fs[0].fingerprint


def test_phone_classification_buckets():
    # clean (canonical) -> no finding; retired -> ERROR/retired; unknown -> WARNING/unknown.
    # +12125551234 is a valid US number in NONE of GL's national/per_location/stale sets ->
    # exercises the 'unknown' path, which never fires on GL real data (all 5 classify).
    clean = phone.run(ParsedPage(url=URL, raw_html='<a href="tel:+18445760144">x</a>',
                                 visible_text=""), CFG)
    assert [f for f in clean if f.check == "phone"] == []
    unknown = _fps(phone.run(ParsedPage(url=URL, raw_html='<a href="tel:+12125551234">x</a>',
                                        visible_text=""), CFG))
    assert "phone:unknown:https://x/p/:+12125551234" in unknown


def test_meta_fingerprints():
    p = ParsedPage(url="https://x/Bad_Slug/", title=None, meta_description=None)
    fps = _fps(meta.run(p, CFG))
    assert {"meta:missing_title:https://x/Bad_Slug/",
            "meta:missing_desc:https://x/Bad_Slug/",
            "meta:malformed_slug:https://x/Bad_Slug/"} <= fps


def test_cross_page_dup_fingerprint():
    # barriers run off compact projections now, not ParsedPage
    p1 = audit.PageProjection(url="https://x/a/", title="Same Title")
    p2 = audit.PageProjection(url="https://x/b/", title="Same Title")
    assert "meta:dup:title:same title" in _fps(audit._cross_page_duplicates([p1, p2]))


def test_links_fingerprint_is_target():
    class _Resp:
        def __init__(self, code, url):
            self.status_code, self.url = code, url

    class _Client:
        async def request(self, method, url, headers=None):
            return _Resp(404, url)

    # external host with a real TLD (a 404 -> broken; identity = the bare target url)
    p = audit.PageProjection(url=URL, link_urls=["https://dead.example.com/gone"])
    findings, _stats = asyncio.run(links.check_links([p], _Client(), CFG))
    assert any(f.fingerprint == "broken_links:https://dead.example.com/gone" for f in findings)


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
