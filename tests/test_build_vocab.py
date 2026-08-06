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


def test_distance_2_alone_cannot_separate_a_typo_from_a_real_term(spell):
    """behavorial (a typo) and comorbid (a real clinical term) are BOTH two letters from a common
    word. The correction signal therefore reports distance 2 for each and CANNOT decide between
    them — which is exactly why the caller requires missing context diversity as well."""
    bad = bv._correction_signal("behavorial", spell)
    good = bv._correction_signal("comorbid", spell)
    assert bad[0] == "behavioral" and bad[1] == 2
    assert good[0] == "morbid" and good[1] == 2


def test_long_words_skip_the_expensive_distance_2_search(spell):
    """Distance 2 is O(n^2): 995ms at 10 chars, 5.3s at 23. Long unknown words are never typos of a
    dictionary word, so the cap costs no recall and keeps the mine finishing."""
    import time
    t = time.time()
    assert bv._correction_signal("about-the-asam-criteria", spell)[0] == ""
    assert time.time() - t < 1.0


def test_curly_apostrophes_are_typography_not_spelling():
    """`it’s` must be tested as `it's`. Without this every contraction on the site — it’s, don’t,
    doesn’t, person’s — was mined as an unknown word and rejected as a typo."""
    assert bv._normalise("it\u2019s and don\u2019t") == "it's and don't"


def test_the_correction_signal_is_fast_enough_to_run_on_thousands_of_words(spell):
    import time
    words = ["acamprosate", "about-the-asam-criteria", "cardiomyopathy", "addeventlistener"]
    t = time.time()
    for w in words:
        bv._correction_signal(w, spell)
    per = (time.time() - t) / len(words)
    assert per < 1.5, f"{per:.1f}s per word — correction() distance-2 slowness is back"


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
