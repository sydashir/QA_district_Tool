"""The same content twice on one page (B3, B5) and a blank slot beside populated ones (B4).

Positives are the client's reported cases; negatives are the repetition patterns that are normal
on every page of this network and would make these checks cry wolf.
"""
from __future__ import annotations

import pytest

from auditor.checks import duplication, empty_row
from auditor.parse import Actionable, Block, ParsedPage
from auditor.report import Severity


class _Cfg:
    brand = "GL"
    base_url = "https://www.gratitudelodge.com"


_PARA = ("Cognitive behavioural therapy helps clients identify the thought patterns that drive "
         "substance use, and replace them with responses that hold up under real pressure at home "
         "and at work over the long term.")
_OTHER = ("Family therapy brings the people closest to a client into the clinical process so that "
          "recovery is supported at home rather than undone there, which matters most in the first "
          "months after discharge from a programme.")


def _page(blocks=(), actionables=()):
    return ParsedPage(url="https://x/a/", blocks=list(blocks), actionables=list(actionables))


def _blk(text, tag="p", region="body", group=0, media=False):
    return Block(tag=tag, text=text, region=region, group=group, has_media=media)


def _run(page, cls):
    return [f for f in duplication.run(page, _Cfg()) if f.details["class"] == cls]


# --- B3: the same paragraph twice ---

def test_the_same_paragraph_under_two_headings_is_caught():
    """"The identical paragraph appears under two different headings" — reported 5 times."""
    fs = _run(_page([_blk(_PARA, group=1), _blk(_OTHER, group=2), _blk(_PARA, group=3)]),
              "duplicate_paragraph")
    assert len(fs) == 1
    assert fs[0].details["count"] == 2
    assert fs[0].severity is Severity.WARNING


def test_repetition_is_matched_through_typography():
    """Two slots filled from one source field differ only in punctuation and spacing."""
    fs = _run(_page([_blk(_PARA, group=1), _blk(_PARA.replace(",", " —").upper(), group=2)]),
              "duplicate_paragraph")
    assert len(fs) == 1


@pytest.mark.parametrize("text", [
    "Verify your insurance",
    "Call us now at (844) 576-0144",
    "3849 Chatwin Ave, Long Beach, CA 90808",
    "Learn more about our programmes",
])
def test_short_repeated_strings_are_not_duplicate_content(text):
    """CTAs, phone numbers and addresses repeat down every page in this network by design."""
    assert _run(_page([_blk(text, group=1), _blk(text, group=2)]), "duplicate_paragraph") == []


def test_a_repeated_heading_is_not_duplicate_content():
    long_heading = _PARA
    assert _run(_page([_blk(long_heading, tag="h2", group=1),
                       _blk(long_heading, tag="h2", group=2)]), "duplicate_paragraph") == []


def test_repetition_in_boilerplate_is_ignored():
    assert _run(_page([_blk(_PARA, region="footer", group=1),
                       _blk(_PARA, region="footer", group=2)]), "duplicate_paragraph") == []


# --- B5: the same link twice in one list ---

def _act(text, href, group=0, region="body"):
    return Actionable(tag="a", text=text, href=href, group=group, region=region)


def test_the_same_link_twice_in_one_list_is_caught():
    """The reported case: an interlink widget listing the same link twice."""
    fs = _run(_page(actionables=[_act("Detox in Long Beach", "/detox/long-beach/", group=4),
                                 _act("Detox in Long Beach", "/detox/long-beach/", group=4),
                                 _act("Detox in Newport", "/detox/newport/", group=4)]),
              "duplicate_link")
    assert len(fs) == 1 and fs[0].details["count"] == 2


def test_the_same_cta_in_different_sections_is_normal_design():
    """A "Verify Insurance" button repeated down a long page is on every page of every brand.
    The container is the discriminator, not the count."""
    assert _run(_page(actionables=[_act("Verify Insurance", "/verify/", group=1),
                                   _act("Verify Insurance", "/verify/", group=7),
                                   _act("Verify Insurance", "/verify/", group=9)]),
                "duplicate_link") == []


def test_duplicate_links_in_the_menu_are_ignored():
    """Mobile + desktop menus render the same links twice on every page in the network."""
    assert _run(_page(actionables=[_act("Admissions", "/admissions/", group=1, region="nav"),
                                   _act("Admissions", "/admissions/", group=1, region="nav")]),
                "duplicate_link") == []


def test_two_different_links_sharing_a_label_are_not_flagged():
    assert _run(_page(actionables=[_act("Read more", "/a/", group=1),
                                   _act("Read more", "/b/", group=1)]), "duplicate_link") == []


# --- B4: a blank slot beside populated ones ---

def _erun(page):
    return empty_row.run(page, _Cfg())


def test_a_blank_cell_among_populated_ones_is_caught():
    """The blank sits BETWEEN populated cells. Written first with the blank last, which the live
    measurement then showed is grid padding — see test_a_grid_padding_cell_is_not_a_missing_value.
    """
    blocks = [_blk("Detox", tag="td", group=1), _blk("", tag="td", group=1),
              _blk("30 days", tag="td", group=1), _blk("Inpatient", tag="td", group=1)]
    fs = _erun(_page(blocks))
    assert len(fs) == 1 and fs[0].details["empty"] == 1


def test_a_cell_holding_an_icon_is_populated():
    blocks = [_blk("Detox", tag="td", group=1), _blk("30 days", tag="td", group=1),
              _blk("Inpatient", tag="td", group=1), _blk("", tag="td", group=1, media=True)]
    assert _erun(_page(blocks)) == []


def test_a_half_empty_group_is_a_layout_not_a_defect():
    blocks = [_blk("A", tag="td", group=1), _blk("", tag="td", group=1),
              _blk("B", tag="td", group=1), _blk("", tag="td", group=1)]
    assert _erun(_page(blocks)) == []


def test_a_group_too_small_to_judge_is_left_alone():
    assert _erun(_page([_blk("A", tag="td", group=1), _blk("", tag="td", group=1)])) == []


def test_an_empty_paragraph_is_a_spacer_not_a_missing_value():
    blocks = [_blk("A", group=1), _blk("B", group=1), _blk("C", group=1), _blk("", group=1)]
    assert _erun(_page(blocks)) == []


def test_blank_slots_in_boilerplate_are_ignored():
    blocks = [_blk("A", tag="li", region="footer", group=1),
              _blk("B", tag="li", region="footer", group=1),
              _blk("C", tag="li", region="footer", group=1),
              _blk("", tag="li", region="footer", group=1)]
    assert _erun(_page(blocks)) == []


def test_a_grid_padding_cell_is_not_a_missing_value():
    """AR's "Addictions Alliance Recovery Treats" table lays names out five per row, so the final
    row is four names and one empty cell. Measured: this ONE shape produced 492 of 492 findings
    across 507 live pages before trailing blanks were excluded."""
    blocks = [_blk("Alcohol addiction", tag="td", group=1),
              _blk("Cocaine addiction", tag="td", group=1),
              _blk("Inhalant addiction", tag="td", group=1),
              _blk("Ritalin addiction", tag="td", group=1),
              _blk("", tag="td", group=1)]
    assert _erun(_page(blocks)) == []


def test_a_gap_between_populated_cells_still_counts():
    """A blank with content after it is a value that failed to render, not padding."""
    blocks = [_blk("Methadone", tag="td", group=1), _blk("", tag="td", group=1),
              _blk("Daily dosing", tag="td", group=1), _blk("Approved", tag="td", group=1),
              _blk("Yes", tag="td", group=1)]
    assert len(_erun(_page(blocks))) == 1


# --- template collapse: one fault on 1,500 pages is one fix ---

def _dup_finding(url, text):
    from auditor.report import Finding
    return Finding(url=url, check="duplication", severity=Severity.WARNING,
                   fingerprint=f"duplication:duplicate_paragraph:{url}", issue="repeated twice",
                   location="page body", snippet=text, suggestion="Fix it.",
                   details={"class": "duplicate_paragraph", "text": text, "count": 2})


def test_a_template_wide_fault_collapses_to_one_finding():
    """GL's addictions grid has a hole in the same row on every geo page; the Hydromorphone
    paragraph repeats on the same template across two brands."""
    from auditor.audit import _collapse_repeats
    fs = [_dup_finding(f"https://www.gratitudelodge.com/p{i}/", "Hydromorphone, marketed as ...")
          for i in range(40)]
    out = _collapse_repeats(fs)
    assert len(out) == 1
    assert out[0].details["page_count"] == 40 and out[0].details["template_wide"] is True
    assert "40 pages" in out[0].issue


def test_a_one_off_stays_per_page():
    from auditor.audit import _collapse_repeats
    fs = [_dup_finding("https://x/a/", "one page only"),
          _dup_finding("https://x/b/", "a different paragraph entirely")]
    assert len(_collapse_repeats(fs)) == 2


# --- grouping: found by review, confirmed on a live page ---

def test_sibling_links_in_one_list_share_a_group():
    """Each <a> sits in its OWN <li>, so keying the group on the immediate parent put 78 body
    anchors on GL /locations/ into 72 groups — duplicate_link could never fire for the case the
    client reported. The group must be the enclosing list, not the <li>."""
    from auditor.parse import parse_html
    html = ('<body><main><ul>'
            '<li><a href="/detox/long-beach/">Detox in Long Beach</a></li>'
            '<li><a href="/detox/long-beach/">Detox in Long Beach</a></li>'
            '<li><a href="/detox/newport/">Detox in Newport</a></li>'
            '</ul></main></body>')
    p = parse_html(html, page_url="https://x/a/", base_url="https://x/")
    assert len({a.group for a in p.actionables}) == 1
    fs = [f for f in duplication.run(p, _Cfg()) if f.details["class"] == "duplicate_link"]
    assert len(fs) == 1 and fs[0].details["count"] == 2


def test_separate_cards_are_separate_groups():
    """The other side: two Elementor cards each holding one link are NOT one list, so a repeated
    CTA across cards must stay silent."""
    from auditor.parse import parse_html
    html = ('<body><main><div class="cards">'
            '<div class="card"><a href="/verify/">Verify Insurance</a></div>'
            '<div class="card"><a href="/verify/">Verify Insurance</a></div>'
            '</div></main></body>')
    p = parse_html(html, page_url="https://x/a/", base_url="https://x/")
    assert len({a.group for a in p.actionables}) == 2
    assert [f for f in duplication.run(p, _Cfg()) if f.details["class"] == "duplicate_link"] == []


def test_an_icon_only_cell_counts_as_populated():
    """Elementor renders ticks as an icon font on an empty <i> — 57 of them on one GL page. Those
    cells have no text and no <img>, so without icon-class detection every one reads as blank."""
    from auditor.parse import parse_html
    html = ('<body><main><table><tr>'
            '<td>Detox</td><td><i class="fas fa-check"></i></td>'
            '<td>Inpatient</td><td>Yes</td></tr></table></main></body>')
    p = parse_html(html, page_url="https://x/a/", base_url="https://x/")
    icon = [b for b in p.blocks if b.tag == "td" and not b.text]
    assert icon and icon[0].has_media is True
    assert empty_row.run(p, _Cfg()) == []
