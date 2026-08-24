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


# ---------------------------------------------------------------------------------------------
# SITE-LEVEL mining. The gates below are correctness, not caution: either one ignored invents typos.
from auditor.checks.misspelling import TokenLedger, from_audit
from auditor.parse import Block


def _page(url: str, *texts: str) -> ParsedPage:
    return ParsedPage(url=url, visible_text=" ".join(texts),
                      blocks=[Block(tag="p", text=t, region="body", group=i)
                              for i, t in enumerate(texts)])


def _ledger(n_common: int = 120, rare: str = "treatmnet") -> tuple[TokenLedger, int]:
    """A site where 'treatment' is common and 'treatmnet' appears once."""
    led = TokenLedger()
    for i in range(n_common):
        led.add_page(_page(f"https://x/{i}/", "our treatment programme helps people every day"))
    led.add_page(_page("https://x/typo/", f"our {rare} programme helps people every day"))
    return led, n_common + 1


def test_a_rare_token_one_edit_from_a_common_one_is_flagged():
    led, n = _ledger()
    found = from_audit(led, audited_pages=n, partial_sample=False)
    assert [f.details["wrong"] for f in found] == ["treatmnet"]
    assert found[0].details["correct"] == "treatment"


def test_a_sampled_run_emits_nothing():
    """MHD audits 900 of 11,439 pages. A word appearing 40x site-wide can appear twice in the
    sample, so sample frequencies are not site frequencies and must not be mined."""
    led, n = _ledger()
    assert from_audit(led, audited_pages=n, partial_sample=True) == []


def test_a_mostly_resumed_run_emits_nothing():
    """Resumed pages carry no text, so they never reach the ledger. If most of the audit was
    resumed, the counts describe a subset and a site-common word can read as rare."""
    led, n = _ledger()
    assert from_audit(led, audited_pages=n * 3, partial_sample=False) == []


def test_rare_pharmaceutical_vocabulary_is_not_a_typo():
    """The failure that put the parked spellchecker at 9% precision (ARCHITECTURE.md D10):
    `isotonitazene` is rare AND far from everything common, so the rarity signal alone would
    accuse it. Being far from every common word is what saves it."""
    led, n = _ledger(rare="isotonitazene")
    assert from_audit(led, audited_pages=n, partial_sample=False) == []


def test_latin_placeholder_text_never_enters_the_ledger():
    """GL's dev page contributed `risus`, `varius`, `potenti` and `mattis` — each one edit from a
    common English word purely by accident. empty_slot reports the placeholder itself."""
    led = TokenLedger()
    for i in range(120):
        led.add_page(_page(f"https://x/{i}/", "our treatment programme helps people every day"))
    led.add_page(_page("https://x/lorem/",
                       "Curabitur feugiat ante vel libero volutpat, id bibendum nibh cursus. "
                       "Pellentesque malesuada risus a condimentum faucibus."))
    assert from_audit(led, audited_pages=121, partial_sample=False) == []


def test_a_word_already_in_the_known_list_is_not_reported_twice():
    led, n = _ledger(rare="athough")
    assert from_audit(led, audited_pages=n, partial_sample=False) == []


# --- British spellings: one editorial decision, not N typos -----------------------------------

def _uk_ledger(*pairs: str) -> tuple[TokenLedger, int]:
    """A site where the US forms are common and the British forms appear a few times."""
    led = TokenLedger()
    common = "behavioral behaviors centers counseling personalized treatment"
    for i in range(120):
        led.add_page(_page(f"https://x/{i}/", f"our {common} programme helps people every day"))
    for j, w in enumerate(pairs):
        led.add_page(_page(f"https://x/uk{j}/", f"our {w} programme helps people every day"))
    return led, 120 + len(pairs)


def test_several_british_spellings_collapse_to_one_finding():
    """Five British words on one site is ONE editorial decision. Reported five times it both
    buries the real typos and misdescribes the fix."""
    led, n = _uk_ledger("behavioural", "behaviours", "centres", "counselling", "personalised")
    found = from_audit(led, audited_pages=n, partial_sample=False)
    assert len(found) == 1
    f = found[0]
    assert f.details["class"] == "british_spelling"
    assert {w["wrong"] for w in f.details["words"]} == {
        "behavioural", "behaviours", "centres", "counselling", "personalised"}
    assert "US English" in f.suggestion


def test_a_single_british_spelling_is_just_a_typo():
    led, n = _uk_ledger("behavioural")
    found = from_audit(led, audited_pages=n, partial_sample=False)
    assert len(found) == 1 and found[0].details["class"] == "mined"


@pytest.mark.parametrize("wrong,correct", [
    ("vallium", "valium"),        # a misspelled drug, NOT a British spelling — the `ll` trap
    ("trazadone", "trazodone"),
    ("clonopin", "klonopin"),
    ("faciltiy", "facility"),
])
def test_a_misspelled_word_is_not_mistaken_for_a_british_one(wrong, correct):
    from auditor.checks.misspelling import _is_british_variant
    assert not _is_british_variant(wrong, correct)


@pytest.mark.parametrize("wrong,correct", [
    ("behavioural", "behavioral"), ("centres", "centers"), ("counselling", "counseling"),
    ("personalised", "personalized"), ("defence", "defense"), ("oestrogen", "estrogen"),
])
def test_british_forms_are_recognised(wrong, correct):
    from auditor.checks.misspelling import _is_british_variant
    assert _is_british_variant(wrong, correct)


def test_a_place_name_before_a_state_code_is_never_a_typo():
    """AR lists cities: "Prairie du Chien, WI" and "Taylors, SC" were 2 of its 5 false positives."""
    led = TokenLedger()
    for i in range(120):
        led.add_page(_page(f"https://x/{i}/", "our chief medical officer runs the treatment team"))
    led.add_page(_page("https://x/cities/", "Prairie du Chien, WI (September 9, 2025)"))
    assert from_audit(led, audited_pages=121, partial_sample=False) == []
