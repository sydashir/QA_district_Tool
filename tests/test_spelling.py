"""Dictionary spellcheck — must catch unreported typos WITHOUT drowning in domain vocabulary.

A bare English dictionary is unusable on this corpus: pyspellchecker does not know
`buprenorphine`, `naloxone`, `benzodiazepine` or `comorbid`, and every geo page is full of place
names. The whole design rests on the vocabulary layers, so these tests pin BOTH directions — real
typos caught, real vocabulary silent. The negative cases matter more: a check that cries wolf on
clinical terms is worse than no check.
"""
from __future__ import annotations

import pytest

from auditor.checks import spelling
from auditor.parse import ParsedPage
from auditor.report import Severity


class _Cfg:
    brand = "gl"
    base_url = "https://www.gratitudelodge.com"


def _run(text="", title=None, desc=None, url="https://www.gratitudelodge.com/rehab/"):
    return spelling.run(ParsedPage(url=url, visible_text=text, title=title,
                                   meta_description=desc), _Cfg())


def _words(findings):
    return {f.details["word"].lower() for f in findings}


# --- catches typos nobody reported (the whole point) ---

@pytest.mark.parametrize("typo,fix", [
    ("inpateint", "inpatient"),
    ("residental", "residential"),
    ("alchohol", "alcohol"),
    ("recieve", "receive"),
    ("detoxifcation", "detoxification"),
    ("psychatric", "psychiatric"),
])
def test_catches_a_misspelling(typo, fix):
    fs = _run(f"We provide {typo} care for people in recovery today.")
    assert typo in _words(fs), f"missed {typo!r}"
    got = next(f for f in fs if f.details["word"].lower() == typo)
    assert got.details["suggestion"] == fix, f"suggested {got.details['suggestion']!r} for {typo!r}"


# --- must stay silent on real vocabulary (the reason this is hard) ---

@pytest.mark.parametrize("text", [
    "Our clinicians treat co-occurring disorders with trauma-informed care.",
    "We accept most major insurance including Aetna and Cigna.",
    "Detox is followed by residential treatment and aftercare planning.",
    "Group therapy sessions run every weekday morning at the facility.",
])
def test_ordinary_copy_is_silent(text):
    assert _run(text) == [], f"false positive on ordinary copy: {_run(text)}"


@pytest.mark.parametrize("token", ["PHP", "IOP", "EMDR", "LGBTQ", "CBT", "MAT"])
def test_acronyms_are_not_spelling_questions(token):
    assert _run(f"We offer {token} programs for adults.") == []


def test_numbers_and_codes_are_ignored():
    assert _run("License 190119BP expires 5/31/28 and covers 24/7 care.") == []


def test_short_words_are_not_flagged():
    # 3-letter tokens are dominated by abbreviations; a 3-letter typo is not reliably separable
    assert _run("The DBT and ACT approaches are used alongside AA and NA.") == []


# --- slugs are a separate class because fixing one costs a redirect ---

def test_a_slug_typo_is_its_own_finding_and_says_the_redirect_cost():
    fs = _run(url="https://www.gratitudelodge.com/rehab/inpateint-care/")
    slug = [f for f in fs if f.details["class"] == "slug"]
    assert slug, "slug typo not caught"
    assert slug[0].severity is Severity.ERROR
    assert "redirect" in slug[0].suggestion.lower()


def test_a_clean_slug_is_silent():
    assert [f for f in _run(url="https://www.gratitudelodge.com/drug-rehab/costa-mesa/")
            if f.details["class"] == "slug"] == []


# --- the reader must be able to act on it ---

def test_the_suggestion_names_the_word_and_where_it_is():
    f = _run("Our inpateint program helps.")[0]
    assert "inpateint" in f.suggestion
    assert "inpatient" in f.suggestion
    assert f.location and f.location in f.suggestion


def test_title_and_description_are_checked_too():
    fs = _run(title="Inpateint Rehab", desc="Our residental program in California")
    locs = {f.location for f in fs}
    assert "search-result title" in locs and "search-result description" in locs


# --- identity discipline ---

def test_the_same_word_twice_on_a_page_keeps_distinct_fingerprints():
    fs = _run("The inpateint wing and the inpateint annex are both open.",
              title="Inpateint Rehab")
    fps = [f.fingerprint for f in fs if f.details.get("class") == "dictionary"]
    assert len(fps) == len(set(fps)), f"fingerprints collided: {fps}"


# --- lorem ipsum is placeholder text, not a spelling question ---

def test_lorem_ipsum_is_caught_by_the_placeholder_check():
    """Found only because a vocabulary mine turned up Latin: enim, labore, magna, nostrud, tempor
    and aliqua all appeared on >=2 brands' LIVE pages. Placeholder Latin shipping to production is
    the same class of defect as an unresolved [acf field] token, and it should not take a
    spellchecker to notice it."""
    from auditor.checks import placeholder
    from auditor.parse import ParsedPage
    text = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor "
            "incididunt ut labore et dolore magna aliqua.")
    fs = [f for f in placeholder.run(ParsedPage(url="https://x/a/", visible_text=text), None)
          if f.details.get("class") == "lorem_ipsum"]
    assert len(fs) == 1
    assert fs[0].severity is Severity.ERROR
    assert "placeholder" in fs[0].suggestion.lower()


@pytest.mark.parametrize("text", [
    "Our team treats anxiety, depression and dual diagnosis at every location.",
    "Sed is a surname and elit is not a word we use, but one Latin-looking token is not enough.",
    "The magna cum laude graduate joined our clinical team in 2019.",
])
def test_ordinary_copy_is_not_mistaken_for_lorem_ipsum(text):
    from auditor.checks import placeholder
    from auditor.parse import ParsedPage
    assert [f for f in placeholder.run(ParsedPage(url="https://x/a/", visible_text=text), None)
            if f.details.get("class") == "lorem_ipsum"] == []


# --- the three causes that made the first GL run unusable (1,873 findings on 150 pages) ---

@pytest.mark.parametrize("text", [
    "We’ll call you back and you’ll hear from us; it doesn’t take long.",
    "The body’s response isn’t the same as a person’s expectations here.",
    "Gratitude Lodge’s team can’t promise outcomes but won’t stop trying.",
])
def test_curly_apostrophes_are_typography_not_misspellings(text):
    """38% of the first run's findings were contractions and possessives written with a curly
    apostrophe. `we’ll` is not a misspelling of `well` — it is `we'll` with different punctuation."""
    assert _run(text) == [], f"flagged a contraction: {[f.details['word'] for f in _run(text)]}"


@pytest.mark.parametrize("text", [
    "Our clinical team includes Krier, Pennino, Muldoon and Reitz.",
    "The sessions are led by Jayla and Valanda every week.",
])
def test_capitalised_names_in_running_text_are_not_spellchecked(text):
    """27% of the first run was staff surnames. A capitalised word INSIDE a sentence is a proper
    noun; a dictionary has no opinion on somebody's surname."""
    assert _run(text) == [], f"flagged a name: {[f.details['word'] for f in _run(text)]}"


def test_a_name_at_the_START_of_a_sentence_is_a_known_limitation():
    """Stated rather than hidden: sentence-initial capitals carry no information, so a name there
    cannot be told from an ordinary word. `Jayla and Valanda lead...` will flag `Jayla`. The
    alternative — skipping every sentence-initial word — would blind the check to the first word of
    every sentence, which is worse. Names that recur elsewhere in running text are already
    suppressed, so in practice this bites only for a name used exactly once, sentence-initially."""
    fs = _run("Jayla leads the family sessions each week without fail.")
    assert [f.details["word"] for f in fs] == ["Jayla"]


@pytest.mark.parametrize("word,sentence", [
    ("accreditations", "Our accreditations are listed on the about page."),
    ("stressors", "Common stressors include work and family pressure."),
    ("pre-screening", "A short pre-screening call happens before admission."),
])
def test_ordinary_derived_and_compound_words_are_not_flagged(word, sentence):
    """27% of the first run was real English the 160k dictionary simply lacks: plurals of known
    words, and hyphenated compounds whose parts are all known."""
    assert _run(sentence) == [], f"flagged {word}: {[f.details['word'] for f in _run(sentence)]}"
