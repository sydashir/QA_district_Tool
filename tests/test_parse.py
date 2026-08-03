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
