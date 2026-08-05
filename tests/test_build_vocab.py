"""The mining heuristic itself — because getting it wrong put a misspelling in the allowlist.

The first version trusted any word appearing on >= 2 brands. That admitted `behavorial`, because
the brands share templates: one author's typo propagates and looks exactly like industry
vocabulary. Cross-brand frequency assumes independent editing, and these sites are not
independently edited.
"""
from __future__ import annotations

import pytest

from auditor.ai import build_vocab as bv


@pytest.fixture(scope="module")
def spell():
    return bv._dictionary()


# --- signal 3: a confident nearby correction means typo, not vocabulary ---

@pytest.mark.parametrize("typo,fix", [
    ("inpateint", "inpatient"), ("residental", "residential"), ("alchohol", "alcohol"),
    ("recieve", "receive"), ("psychatric", "psychiatric"), ("assesment", "assessment"),
])
def test_a_typo_has_a_confident_correction(typo, fix, spell):
    corr, ed, freq = bv._correction_signal(typo, spell)
    assert corr == fix and ed == 1 and freq >= bv.MIN_CORR_FREQ


@pytest.mark.parametrize("term", [
    "buprenorphine", "naloxone", "benzodiazepine", "acamprosate", "naltrexone", "buspirone",
])
def test_real_domain_vocabulary_has_no_correction_at_all(term, spell):
    corr, _ed, _f = bv._correction_signal(term, spell)
    assert corr == "", f"{term} was 'corrected' to {corr!r} — it is a real clinical term"


def test_edit_distance_2_alone_cannot_separate_a_typo_from_a_real_term(spell):
    """This is WHY the context signal exists. Measured: behavorial->behavioral (freq 2,932) and
    comorbid->morbid (freq 2,677) are indistinguishable on the correction signal alone."""
    bad = bv._correction_signal("behavorial", spell)
    good = bv._correction_signal("comorbid", spell)
    assert bad[1] == good[1] == 2
    assert bad[2] > bv.MIN_CORR_FREQ and good[2] > bv.MIN_CORR_FREQ


# --- signal 2: context diversity ---

def test_context_windows_differ_for_a_word_used_in_real_prose():
    a = bv._contexts("patients with comorbid anxiety respond well to integrated care", {"comorbid"})
    b = bv._contexts("a comorbid diagnosis changes the treatment plan entirely here", {"comorbid"})
    assert len(a["comorbid"] | b["comorbid"]) == 2


def test_context_windows_are_identical_for_a_copied_template_block():
    line = "our behavorial health team supports you through every stage here"
    a = bv._contexts(line, {"behavorial"})
    b = bv._contexts(line, {"behavorial"})       # same block, different brand
    assert len(a["behavorial"] | b["behavorial"]) == 1, "a copied block must look like one context"


# --- british spellings are a question, not an error ---

@pytest.mark.parametrize("brit,us", [
    ("behavioural", "behavioral"), ("counselling", "counseling"),
    ("organisation", "organization"), ("recognise", "recognize"),
])
def test_british_variants_are_identified_not_called_typos(brit, us, spell):
    assert bv._looks_british(brit, spell) == us


@pytest.mark.parametrize("word", ["buprenorphine", "naloxone", "detox", "inpatient"])
def test_ordinary_words_are_not_mistaken_for_british_variants(word, spell):
    assert bv._looks_british(word, spell) == ""
