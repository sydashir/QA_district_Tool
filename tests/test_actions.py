"""Dead call-to-action buttons, and social icons wired to the wrong network.

**The most-reported defect in the client's own attachments — 12 separate reports** across the seven
PDFs on ClickUp 86baawd2a: "the Verify Your Insurance button doesn't actually take the user
anywhere", "Read our Success Stories doesn't take the user anywhere", "the Apply Now and Learn More
links don't work", "Get in Touch doesn't work", "Call us now and View centers buttons don't work".
Verified still live on districtbehavioralhealth.com eight weeks after they reported it.

**The discriminator is the whole job.** GL's `/locations/` page alone carries 33 anchors with no
href that are perfectly legitimate mega-menu toggles, and this network's real CTAs open Elementor
popups via `href="#elementor-action%3A…"`. Keying on "missing href" would cry wolf on the single
defect class the client cares most about. So a finding requires the element to LOOK like a
call-to-action *and* to have nothing behind it *and* to show no sign that JavaScript drives it.
"""
from __future__ import annotations

import pytest

from auditor.checks import actions
from auditor.parse import Actionable, ParsedPage
from auditor.report import Severity


class _Cfg:
    brand = "rr"
    base_url = "https://www.renaissancerecovery.com"


def _page(*acts):
    return ParsedPage(url="https://x/a/", actionables=list(acts))


def _run(*acts, cls="dead_cta"):
    return [f for f in actions.run(_page(*acts), _Cfg()) if f.details.get("class") == cls]


def _a(text, href=None, **kw):
    return Actionable(tag=kw.pop("tag", "a"), text=text, href=href, **kw)


# --- the defects the client reported, verbatim ---

@pytest.mark.parametrize("text,href", [
    ("Verify Insurance", "#"),                 # live on districtbehavioralhealth.com today
    ("Verify Your Insurance", "#"),
    ("Read our Success Stories", "#"),
    ("Apply Now", None),
    ("Learn More", None),
    ("Get in Touch", "javascript:void(0)"),
    ("Call us now", "#"),
    ("View Centers", ""),
    ("Read More Reviews", "#"),
])
def test_a_call_to_action_with_nothing_behind_it_is_caught(text, href):
    fs = _run(_a(text, href))
    assert len(fs) == 1, f"missed {text!r} -> {href!r}"
    assert fs[0].severity is Severity.ERROR
    assert text.split()[0].lower() in fs[0].suggestion.lower() or text in fs[0].snippet


def test_a_button_element_is_never_flagged():
    """A <button> has no href BY DESIGN — its behaviour is JavaScript, which this tool does not
    execute. Every <button> this check reached on live pages was working: GL's form Submit
    (`type=submit` inside a <form>), COC's `relatedloadMoreBtn`, a modal's close "x"."""
    assert _run(_a("Submit", None, tag="button")) == []
    assert _run(_a("Load More", None, tag="button")) == []


def test_button_styling_alone_is_not_enough_when_there_is_no_href():
    """TDRC's homepage styles 14 amenity labels as `elementor-button` with no href — decorative
    chips that were never links. Flagging them would put 14 non-defects on one page."""
    for label in ("Paintball", "Hiking", "Nutrition Plans", "Beach Bonfires"):
        assert _run(_a(label, None, classes=("elementor-button",), role="button")) == [], label


def test_button_styling_IS_enough_when_a_link_was_authored_and_goes_nowhere():
    """The other side of the same rule: `href="#"` means somebody wrote a link. It is broken
    whatever the words say."""
    assert len(_run(_a("Paintball", "#", classes=("elementor-button",)))) == 1


# --- must stay silent: 33 of these on ONE GL page ---

def test_a_mega_menu_toggle_is_not_a_dead_button():
    """`<a>About Us</a>` with no href, inside nav, opening a submenu. GL /locations/ has 33."""
    assert _run(_a("About Us", None, in_nav=True, has_submenu=True)) == []
    assert _run(_a("Treatment Programs", None, in_nav=True, has_submenu=True)) == []


def test_a_menu_toggle_outside_a_nav_element_is_also_silent():
    """GL builds its mega-menu from `<a class="dropbtn">` in plain <div>s — no <nav>, no <li>.
    Requiring nav membership reported 21 of these on /locations/ as broken buttons."""
    assert _run(_a("Admissions", None, classes=("dropbtn", "main_item"), has_submenu=True)) == []


def test_the_submenu_flag_needs_a_real_menu_behind_it():
    """The escape hatch must not swallow genuine dead buttons: a CTA with no link list after it
    is still dead, whatever it sits next to."""
    assert len(_run(_a("Verify Insurance", None, classes=("dropbtn",), has_submenu=False))) == 1


def test_an_elementor_popup_trigger_is_functional():
    """This network's REAL insurance CTA opens a popup: href="#elementor-action%3A…". Flagging it
    would flag the working button on nearly every page."""
    assert _run(_a("Verify Insurance", "#elementor-action%3Aaction%3Dpopup%3Aopen")) == []


def test_an_in_page_anchor_is_not_dead():
    assert _run(_a("Learn More", "#our-programs")) == []


@pytest.mark.parametrize("kw", [
    {"has_js_hooks": True},
    {"role": "tab"},
])
def test_javascript_driven_controls_are_not_dead(kw):
    assert _run(_a("Learn More", "#", **kw)) == []


def test_a_working_link_is_not_flagged():
    assert _run(_a("Verify Insurance", "/insurance-verification/")) == []


def test_non_cta_text_with_no_href_is_ignored():
    """Only call-to-action shapes. A hrefless anchor wrapping an image or a stray label is not a
    broken button, and treating it as one is how this check would cry wolf."""
    assert _run(_a("", None)) == []
    assert _run(_a("Read our latest blog post about seasonal affective disorder", None)) == []


# --- B8: social icon pointing at the wrong network ---

@pytest.mark.parametrize("label,href,wrong", [
    ("LinkedIn", "https://www.instagram.com/renaissancerecoveryoc", "instagram"),
    ("YouTube", "https://www.instagram.com/renaissancerecoveryoc", "instagram"),
    ("Facebook", "https://twitter.com/someone", "twitter"),
])
def test_a_social_icon_pointing_at_the_wrong_network_is_caught(label, href, wrong):
    fs = _run(_a("", href, aria_label=label), cls="social_misrouted")
    assert len(fs) == 1, f"missed {label} -> {href}"
    assert label.lower() in fs[0].suggestion.lower()
    assert wrong in fs[0].suggestion.lower()


def test_a_social_icon_identified_by_class_is_also_checked():
    fs = _run(_a("", "https://www.instagram.com/x", classes=("fab", "fa-linkedin-in")),
              cls="social_misrouted")
    assert len(fs) == 1


@pytest.mark.parametrize("label,href", [
    ("LinkedIn", "https://www.linkedin.com/company/renaissancerecovery"),
    ("YouTube", "https://www.youtube.com/channel/UCabc"),
    ("Instagram", "https://www.instagram.com/renaissancerecoveryoc"),
    ("Facebook", "https://www.facebook.com/renaissancerecovery"),
    ("X", "https://x.com/renaissance"),
])
def test_correctly_wired_social_icons_are_silent(label, href):
    assert _run(_a("", href, aria_label=label), cls="social_misrouted") == []


def test_a_non_social_link_with_a_social_word_in_it_is_silent():
    """"Share on Facebook" pointing at a share endpoint is not a misrouted profile icon."""
    assert _run(_a("Share on Facebook",
                   "https://www.facebook.com/sharer/sharer.php?u=https://x.com/a"),
                cls="social_misrouted") == []


def test_a_mega_menu_column_heading_is_not_a_broken_button():
    """RR renders `<a href="#">Admissions Resources</a>` as a menu COLUMN HEADING. Inside a menu,
    an anchor that goes nowhere is routinely a label, so nav is held to the same action-phrase
    standard as a missing href."""
    assert _run(_a("Admissions Resources", "#", in_nav=True)) == []
    # ...but a real CTA in the header is still a real CTA
    assert len(_run(_a("Verify Insurance", "#", in_nav=True))) == 1


def test_a_template_wide_footer_fault_is_one_finding_not_one_per_page():
    """GL's Instagram-icon-to-LinkedIn fault is in the header and footer template: 119 rows across
    60 pages on the first real run. One template field, one fix, one row."""
    from auditor.audit import _collapse_repeats
    from auditor.report import Finding
    fs = [Finding(url=f"https://www.gratitudelodge.com/p{i}/", check="actions",
                  severity=Severity.ERROR, fingerprint=f"actions:social:{i}",
                  issue="the instagram icon links to linkedin", location="page body",
                  snippet="https://www.linkedin.com/company/gratitude-lodge",
                  suggestion="Point it at the Instagram profile.",
                  details={"class": "social_misrouted", "intended": "instagram",
                           "actual": "linkedin"})
          for i in range(60)]
    out = _collapse_repeats(fs)
    assert len(out) == 1 and out[0].details["page_count"] == 60


def test_two_different_dead_buttons_do_not_merge():
    from auditor.audit import _collapse_repeats
    from auditor.report import Finding

    def dead(label, i):
        return Finding(url=f"https://x/p{i}/", check="actions", severity=Severity.ERROR,
                       fingerprint=f"actions:dead:{label}:{i}",
                       issue=f'button goes nowhere: "{label}"', location="page body",
                       snippet=label, suggestion="Fix it.",
                       details={"class": "dead_cta", "label": label})
    fs = [dead("View All", i) for i in range(5)] + [dead("Verify Insurance", i) for i in range(5)]
    out = _collapse_repeats(fs)
    assert len(out) == 2
    assert {f.details["label"] for f in out} == {"View All", "Verify Insurance"}
