"""Visible-text extraction — the input every content check reads.

Two welds and one manufactured defect have all shipped from this one function, so each is pinned
here with a fixture taken from real client markup:

1. BLOCK WELD — ``get_text(" ")`` joined an <h2> to the <p> beneath it, producing a sentence that
   is not on the page ("Residential Rehab Options Because emergency workers…").
2. INLINE WELD — the naive fix dropped the separator entirely and fused sibling <a> links in a nav
   menu into "addictionDetox"; the Phase-2 pilot reported 24 bogus "missing space" defects from a
   single block before this was caught.
3. MANUFACTURED PUNCTUATION — emitting a space after EVERY inline text node turned live GL's
   correct ``<strong>phone rings</strong>.`` into "phone rings .", and the AI layer correctly
   reported the text it was shown. The original ``get_text(" ", strip=True)`` had this same flaw,
   so the artifact is present in every report produced before this fix.

All three are the same underlying question — when does a boundary between two text nodes deserve
whitespace — so they are tested together.
"""
from __future__ import annotations

import pytest

from auditor.parse import parse_html

URL = "https://x/p/"


def _vt(html: str) -> str:
    return parse_html(html, page_url=URL, base_url=URL).visible_text


# --- 1. block boundaries survive ---

def test_heading_is_not_welded_to_the_paragraph_below_it():
    vt = _vt("<h2>Residential Rehab Options</h2><p>Because emergency workers face trauma.</p>")
    assert "Options Because" not in vt
    assert "Residential Rehab Options" in vt and "Because emergency workers" in vt


def test_consecutive_list_items_stay_separate():
    vt = _vt("<ul><li>Rapid intervention promotes wellness</li><li>Comprehensive treatment helps</li></ul>")
    assert "wellness Comprehensive" not in vt


# --- 2. adjacent inline nodes stay apart ---

def test_adjacent_inline_nodes_still_separate():
    vt = _vt('<p><a href="/a">Adderall addiction</a><a href="/b">Detox</a></p>')
    assert "addictionDetox" not in vt and "addiction Detox" in vt


def test_inline_markup_mid_sentence_is_not_broken():
    assert _vt("<p>Call <b>now</b> for help today.</p>") == "Call now for help today."


# --- 3. no manufactured space before punctuation ---

@pytest.mark.parametrize("html,bad,why", [
    ("<p>He got his <em>life</em>. Then more.</p>", " .", "inline tag before a period"),
    ('<p>the <a href="/x">lodge</a>, dedicated to care here</p>', " ,", "link before a comma"),
    ("<p>Care at <span>Gratitude</span>; call now please.</p>", " ;", "span before a semicolon"),
    ("<p>Is it <b>real</b>? Yes it is.</p>", " ?", "bold before a question mark"),
    ("<p>She said <i>stop</i>! Loudly and clearly.</p>", " !", "italic before an exclamation"),
    ("<p>Ready for your <strong>needs</strong>, avoiding delays.</p>", " ,", "live GL markup"),
])
def test_inline_tag_before_punctuation_does_not_gain_a_space(html, bad, why):
    vt = _vt(html)
    assert bad not in vt, f"manufactured a space before punctuation ({why}): {vt!r}"


def test_a_genuine_space_before_punctuation_is_still_visible():
    # the fix must not paper over a defect the client's own copy really contains: if the SOURCE text
    # has the stray space, it must survive so the check can report it.
    assert " ," in _vt("<p>Ready for your needs , avoiding delays and waiting.</p>")


# --- hidden content must not reach visible_text ---
#
# Found by adversarial review of the Phase-2 findings, then verified against live GL markup:
#   <style>.geo-topic a span{display:none}</style>
#   <a href="...">Adderall addiction<span>Detox</span></a>
# The span is a hover-only label the user never sees. Extracting it produced "addictionDetox", which
# was originally mis-diagnosed as a MISSING SEPARATOR and "fixed" by inserting a space — giving
# "addiction Detox", still text that is not on the page. The real defect was including hidden
# content at all. This matters well beyond the AI layer: visible_text is what the blank/thin-section
# check measures, and hidden text makes an empty section look populated.

def test_stylesheet_display_none_is_not_visible_text():
    vt = _vt('<style>.geo-topic a span{display:none}</style>'
             '<div class="geo-topic"><a href="/x">Adderall addiction<span>Detox</span></a></div>')
    assert vt == "Adderall addiction", vt


def test_inline_display_none_is_not_visible_text():
    assert _vt('<p>Visible text<span style="display:none">HIDDEN</span> continues here.</p>') \
        == "Visible text continues here."


def test_visibility_hidden_is_not_visible_text():
    assert "HIDDEN" not in _vt('<p>Visible<span style="visibility:hidden">HIDDEN</span> text.</p>')


def test_hidden_attribute_is_not_visible_text():
    assert _vt("<p>Visible<span hidden>HIDDEN</span> text.</p>") == "Visible text."


def test_display_none_inside_a_media_query_is_still_shown():
    # a mobile-menu rule hides content only at some breakpoints; the content is real page copy at
    # others, so stripping it would delete text the user can see.
    vt = _vt('<style>@media (max-width:600px){.m{display:none}}</style>'
             '<div class="m">Call our admissions team today for help.</div>')
    assert "Call our admissions team today" in vt


def test_whitespace_only_node_after_punctuation_still_separates():
    # "<strong>View more details here:</strong> <a>Outpatient</a>" — the markup HAS a space, but a
    # whitespace-only text node was dropped and ":" is not alphanumeric, so no separator was
    # re-inserted and the words fused into "here:Outpatient".
    assert _vt('<p><strong>View more details here:</strong> <a href="/y">Outpatient Rehab</a></p>') \
        == "View more details here: Outpatient Rehab"


# --- HTML comments are not visible text ---
#
# Found while mining domain vocabulary: JavaScript identifiers (addEventListener, getElementById)
# and lorem-ipsum Latin were turning up as "words on the page" across brands. The cause was not a
# script-stripping failure — bs4's Comment is a SUBCLASS of NavigableString, so a walk over
# `descendants` picks comments up while `get_text()` deliberately excludes them. Commented-out
# markup, developer TODOs and disabled tracking snippets were all being read as page copy.
#
# Same family as the display:none bug: text that no reader can see must never reach visible_text,
# because visible_text is what the blank/thin check measures and what every content check reads.

def test_an_html_comment_is_not_page_text():
    vt = _vt('<p>Real copy here.</p><!-- TODO: fix this before launch -->')
    assert vt == "Real copy here."


def test_commented_out_script_does_not_leak_javascript():
    vt = _vt('<p>Call us today.</p>'
             '<!-- <script>document.addEventListener("DOMContentLoaded", go)</script> -->')
    assert "addEventListener" not in vt and "script" not in vt.lower()


def test_a_comment_between_two_paragraphs_does_not_weld_them():
    vt = _vt("<p>First sentence.</p><!-- note --><p>Second sentence.</p>")
    assert "note" not in vt
    assert "First sentence." in vt and "Second sentence." in vt


def test_a_doctype_is_not_page_text():
    assert "DOCTYPE" not in _vt("<!DOCTYPE html><html><body><p>Copy.</p></body></html>").upper()
