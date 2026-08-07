"""Placeholder text that shipped to production — generalised, not five hardcoded strings.

We believed `placeholder.py` covered this class. It caught `[acf field=…]` and nothing else, while
the client reported five other shapes across three separate documents:

    "No content found"                  (RR, 8 template URLs)
    "No accordion items found"          (RR, 10 template URLs)
    "No Content Found in this Field"    (COC, "displaying everywhere")
    "- GEO"                             (GL, "this issue is throughout all templates")
    "[sobriety_calculator]"             (RR sobriety-calculator page)

Built as PATTERNS so the sixth variant nobody has reported yet is caught too. The negative fixtures
matter as much: a bracketed citation, an intentional empty-state on a search page, and the word
"found" in ordinary prose must all stay silent.
"""
from __future__ import annotations

import pytest

from auditor.checks import placeholder
from auditor.parse import ParsedPage
from auditor.report import Severity


class _Cfg:
    brand = "rr"
    base_url = "https://www.renaissancerecovery.com"


def _run(text):
    return [f for f in placeholder.run(ParsedPage(url="https://x/a/", visible_text=text), _Cfg())
            if f.details.get("class") in ("empty_state", "shortcode", "variable_name")]


# --- every shape the client actually reported ---

@pytest.mark.parametrize("text", [
    "Levels of Care\nNo content found\nNext section",
    "FAQs\nNo accordion items found\nMore",
    "Heading\nNo Content Found in this Field\nBody",
    "Our Programs\nNo items found\nFooter",
])
def test_empty_state_messages_are_caught(text):
    fs = _run(text)
    assert fs and fs[0].severity is Severity.ERROR


@pytest.mark.parametrize("text", [
    "Try our tool: [sobriety_calculator] to track your progress today.",
    "See [contact_form id=4] below to reach us.",
])
def test_unrendered_shortcodes_are_caught(text):
    assert _run(text), f"missed a shortcode in {text!r}"


def test_acf_tokens_stay_with_the_acf_check_and_are_not_double_reported():
    """`[acf field=geo]` is already the `acf` class. The shortcode pattern deliberately does not
    also match it — one defect must produce one finding, not two."""
    fs = placeholder.run(
        ParsedPage(url="https://x/a/", visible_text="Welcome to [acf field=geo] treatment."), _Cfg())
    assert len(fs) == 1, [f.issue for f in fs]
    assert "acf field" in fs[0].issue
    assert fs[0].details.get("class") != "shortcode"


@pytest.mark.parametrize("text", [
    "What Addictions Do We Treat? - GEO",
    "Rehab in CITY for adults",
    "Programs across STATE today",
])
def test_literal_variable_names_left_in_copy_are_caught(text):
    assert _run(text), f"missed a bare variable name in {text!r}"


# --- must stay silent (cry-wolf guard) ---

@pytest.mark.parametrize("text", [
    "Studies found that treatment works [1] and outcomes improve [2].",
    "The team found no evidence of relapse during the follow-up period.",
    "Our search found 12 facilities near you in Orange County today.",
    "He was [sic] discharged early according to the record.",
    "Call us at (888) 707-6073 or email admissions today for help.",
    "We treat addiction across California, Florida and Tennessee.",
    "The GEO Group is unrelated to our organisation entirely.",
    # live false positive on renaissancerecovery.com/drug/rehab/sobriety-calculator/ — an all-caps
    # certification line, not a leaked template variable
    "Certified by: STATE OF TENNESSEE DEPARTMENT OF MENTAL HEALTH AND SUBSTANCE ABUSE",
    "Licensed by the CITY OF NEWPORT BEACH PLANNING DEPARTMENT",
])
def test_ordinary_copy_stays_silent(text):
    assert _run(text) == [], f"false positive on: {text!r} -> {_run(text)}"
