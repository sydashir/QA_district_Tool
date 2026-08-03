"""Empty-variable artifacts — the defect class Jake reports most often.

v1's `placeholder` check catches a LITERAL token (`[acf field=geo]`). It does NOT catch the case
where the token resolved to an EMPTY STRING and left grammatically broken prose behind. Every
POSITIVE fixture below is Jake's verbatim text from ClickUp task 86baawd2a — these are real strings
that shipped to production, not invented examples.

The NEGATIVE fixtures matter as much: this check runs on every page of every brand, so a pattern
that fires on ordinary prose would bury the real findings (the project's cry-wolf standard).
"""
from __future__ import annotations

import pytest

from auditor.checks import empty_slot
from auditor.parse import ParsedPage
from auditor.report import Severity

URL = "https://x/p/"


def _run(text: str):
    return empty_slot.run(ParsedPage(url=URL, visible_text=text), None)


# --- POSITIVE: Jake's verbatim production strings (ClickUp 86baawd2a) ---

@pytest.mark.parametrize("text,subtype", [
    # "There are at least outpatient drug rehab programs available within of California"
    ("There are at least outpatient drug rehab programs available within of California, offering "
     "structured treatment", "double_preposition"),
    # CAD homepage: "In , the involving substances such as"
    ("In , the involving substances such as alcohol", "orphan_comma"),
    # CAD homepage: "Among a population of in , overdose outcomes d compared to by %."
    ("Among a population of in , overdose outcomes d compared to by %.", "orphan_comma"),
    # GL city template: the date field is empty -> "In Los Angeles during , there were 5 news reports"
    ("In Los Angeles during , there were 5 news reports", "orphan_comma"),
    # RR: "Among 9861 people in 2023, overdose outcomes d compared to 2022 by 5.96%."
    ("Among 9861 people in 2023, overdose outcomes d compared to 2022 by 5.96%.", "truncated_word"),
])
def test_jakes_real_defects_are_caught(text, subtype):
    fs = _run(text)
    assert fs, f"missed a real production defect: {text!r}"
    assert any(f.details["class"] == subtype for f in fs), \
        f"expected {subtype}, got {[f.details['class'] for f in fs]}"


@pytest.mark.parametrize("text", [
    # found live by the Phase-2 pilot on GL — the number survived, the unit did not
    "There are more than 10 programs available within 15 of Costa Mesa, including LGBTQ care.",
    "At least 27 programs accept private insurance within 30 of Ventura, reducing barriers.",
    "3 facilities maintain a rating of 4 and have at least 10 reviews within 20.",
])
def test_missing_unit_is_caught(text):
    fs = [f for f in _run(text) if f.details["class"] == "missing_unit"]
    assert len(fs) >= 1 and fs[0].severity is Severity.ERROR


@pytest.mark.parametrize("text", [
    "Detox typically completes within 30 days of admission.",
    "We return calls within 24 hours, seven days a week.",
    "The facility is within 10 miles of Los Angeles.",
    "Most clients are admitted within 48 hrs.",
    "Coverage is confirmed within 15 minutes in most cases.",
    "Programs are located within 5 mi of the coast.",
    # ranges: the unit sits after the END of the range, so a naive lookahead fires on the first
    # number. Found live on CAD: "symptoms subside within 3 to 5 days".
    "Withdrawal symptoms tend to subside within 3 to 5 days after the last dose.",
    "Most admissions complete within 24 to 48 hours of the first call.",
    "Aftercare check-ins happen within 30, 60, and 90 days of discharge.",
    "Detox typically runs within 5-7 days depending on the substance.",
])
def test_real_units_never_fire(text):
    assert [f for f in _run(text) if f.details["class"] == "missing_unit"] == [], \
        f"false positive on a real unit: {text!r}"


def test_empty_percent_is_caught():
    # "...overdose outcomes d compared to by %." — the percentage never populated
    fs = [f for f in _run("overdose outcomes changed compared to 2022 by %.")
          if f.details["class"] == "empty_percent"]
    assert len(fs) == 1 and fs[0].severity is Severity.ERROR


# --- NEGATIVE: ordinary prose that must NEVER fire (cry-wolf guard) ---

@pytest.mark.parametrize("text", [
    "Renaissance Recovery treats addiction in California, Florida, and Tennessee.",
    "Vitamin D deficiency is common among people in early recovery.",
    "Our program is at least twelve weeks long and covers 100% of the curriculum.",
    "Dr. J. Smith, LMHC, leads the clinical team at our Costa Mesa facility.",
    "Detox lasts 5 to 7 days, followed by residential treatment.",
    "We accept most major insurance, including Blue Cross, Aetna, and Cigna.",
    "Call 866-330-9449 to speak with an admissions specialist today.",
    "Rates fell by 5.96% compared to 2022, according to the CDC.",
    "The facility is within 10 miles of Los Angeles.",
    # --- these three were REAL false positives found on live GL/CAD pages; they are ordinary
    # English and the pattern was tightened until they stayed quiet.
    "Programs maintain ratings of at least 4 stars with a minimum of 10 reviews.",
    "Read on for additional information about companion-friendly treatment.",
    "Roughly 52 facilities maintain ratings of at least 4 with a minimum of 10 reviews.",
])
def test_ordinary_prose_does_not_fire(text):
    assert _run(text) == [], f"false positive on ordinary prose: {text!r}"


# --- identity / fingerprint discipline ---

def test_repeated_artifact_on_one_page_keeps_distinct_fingerprints():
    # the same broken string can appear twice on one page (a repeated template block); each
    # occurrence must keep its own identity rather than collapsing (the label_leak/phone lesson).
    fs = _run("In , the first section. Then later: In , the second section.")
    fps = [f.fingerprint for f in fs if f.details["class"] == "orphan_comma"]
    assert len(fps) == 2 and len(set(fps)) == 2, f"fingerprints collided: {fps}"


def test_finding_shape_is_actionable():
    f = _run("In , the involving substances")[0]
    assert f.check == "empty_slot" and f.severity is Severity.ERROR
    assert f.snippet and f.suggestion
    assert "empty" in f.suggestion.lower() or "variable" in f.suggestion.lower()
