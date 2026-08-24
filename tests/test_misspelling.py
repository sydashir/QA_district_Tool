"""Corpus-mined misspellings — the fourth spelling attempt, and the one that worked.

D9 (AI), D10 (dictionary) and D11 (LanguageTool) each asked "is this word CORRECT?" and each
failed. This asks a different question: is this token CORRUPT? A word used once that is one letter
from a word used a thousand times is a typo, not vocabulary — which is why rare pharmaceutical
vocabulary (`isotonitazene`), the thing that put D10 at 9% precision, cannot trip it.

Every POSITIVE below is verbatim from a live Gratitude Lodge page.
"""
from __future__ import annotations

import pytest

from auditor.checks import misspelling
from auditor.parse import ParsedPage

URL = "https://x/p/"

# ---------------------------------------------------------------------------------------------
# CORPUS-MINED entries (2026-08-24). Mining rule: a token appearing 1-3 times across 876 live GL
# pages that is ONE EDIT from a token appearing 100+ times. Measured 11/12 = 92% by hand; every
# entry below was re-fetched and confirmed in the RENDERED text of a live page.

@pytest.mark.parametrize("text,wrong", [
    ("Allergic reactions to tramadol, athough rare, can occur almost immediately", "athough"),
    ("We offer a comfortable detox faciltiy where you will be able to go through", "faciltiy"),
    ("Because no two recovey journeys are alike, we consult with you", "recovey"),
    ("Vulnerability to relaspe is at an all-time high because of the desire", "relaspe"),
    ("addiction should be treatmed similarly to mental or physical health conditions", "treatmed"),
    ("If you have developed an addiction to trazadone, initiate a full recovery", "trazadone"),
    ("drug tests can detect Percoet for days or even months after the last dose", "percoet"),
    ("At Graditude Lodge in Orange County, Veterans Rehab is incorporated", "graditude"),
    ("accept private insurance near Cerritos, helping make txreatment more feasible", "txreatment"),
    ("sleep disturbances, and increased sensitivity to stres. These symptoms can last", "stres"),
])
def test_corpus_mined_misspellings_are_caught(text, wrong):
    found = misspelling.run(ParsedPage(url=URL, visible_text=text), None)
    assert any(f.details.get("wrong", "").lower() == wrong for f in found), \
        f"{wrong!r} not flagged in {text!r}"


@pytest.mark.parametrize("text", [
    # word boundaries do the safety work: the CORRECT spellings must never fire
    "The stress of withdrawal is real, and the program will treat it.",
    "Our facility offers a full recovery program for every patient treated here.",
    "Gratitude Lodge treats trazodone dependence alongside Percocet dependence.",
    "Although rare, allergic reactions are assisted by our medical team.",
])
def test_correct_spellings_never_fire(text):
    assert misspelling.run(ParsedPage(url=URL, visible_text=text), None) == []
