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
