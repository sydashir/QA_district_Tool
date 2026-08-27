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


# --- TEXT CORRUPTION: template/pipeline damage, detectable structurally ---
#
# Salvaged from the parked dictionary spellchecker (ARCHITECTURE.md D10). Three of its seven real
# findings were never misspellings — `acetaminop` (truncated mid-stem), `alcoholusedisorderaud`
# (run-together) and `ency` (a stray fragment). Those are the SAME family as truncated_word: a
# template or data pipeline damaged the text, not a human mistyping it. No dictionary is needed to
# decide correctness, only structure.

@pytest.mark.parametrize("text,cls", [
    ("Read our alcoholusedisorderaud guide before admission today.", "run_together"),
    ("The bestrehabcentersincalifornia listing was published last week.", "run_together"),
])
def test_run_together_words_are_caught(text, cls):
    fs = [f for f in _run(text) if f.details["class"] == cls]
    assert len(fs) >= 1 and fs[0].severity is Severity.ERROR


@pytest.mark.parametrize("text", [
    "Notwithstanding the above, our interdisciplinary team reviews every case.",
    "We provide individualized and comprehensive confidentiality agreements.",
    "Our responsibilities include transportation and recommendations for care.",
    "Our detoxification and rehabilitation programs run continuously.",
    "The counselor recommended pharmacotherapy alongside psychotherapy.",
    "Benzodiazepines and buprenorphine are dispensed under supervision.",
    "Visit gratitudelodge.com or email admissions@gratitudelodge.com today.",
])
def test_long_ordinary_words_are_not_run_together(text):
    """Long clinical words and domain names are normal. A run-together needs several whole words
    fused with no separator, which ordinary English never produces."""
    assert [f for f in _run(text) if f.details["class"] == "run_together"] == [], \
        f"false positive: {[f.details.get('matched') for f in _run(text)]}"


def test_a_run_together_finding_says_what_it_found():
    f = [x for x in _run("See the alcoholusedisorderaud page.")
         if x.details["class"] == "run_together"][0]
    assert "alcohol" in f.suggestion.lower()
    assert "separate" in f.suggestion.lower() or "space" in f.suggestion.lower()


@pytest.mark.parametrize("text", [
    "Overdose deaths affect some communities disproportionately across the state.",
    "The disproportionate impact on young adults is well documented.",
    "See medicalnewstoday.com for the full write-up of the study.",
])
def test_real_words_and_domains_are_not_run_together(text):
    """Measured on 300 live pages: the only run_together findings were `disproportionate`,
    `disproportionately` (real words the SEGMENTER set lacks, so they decompose) and
    `medicalnewstoday` (a domain in a citation). Checking the FULL dictionary for the whole token
    first, and skipping anything followed by a dot, removes both."""
    assert [f for f in _run(text) if f.details["class"] == "run_together"] == [], \
        f"false positive: {[f.details.get('matched') for f in _run(text)]}"


# ---------------------------------------------------------------------------------------------
# CORRUPTION, not incorrectness (2026-08-24). Every POSITIVE below is verbatim from a live GL page
# and was re-fetched to confirm it is in the RENDERED text, not an artifact of our own parser.
# Measured on 876 pages / 1.7M words; see ARCHITECTURE.md D12 for the families that failed.
from auditor.parse import Block


def _blocks(*texts: str, region: str = "body"):
    """These families read ONE BLOCK at a time, so fixtures must be built as blocks."""
    return ParsedPage(
        url=URL, visible_text="\n".join(texts),
        blocks=[Block(tag="p", text=t, region=region, group=i) for i, t in enumerate(texts)])


def _classes(page) -> list[str]:
    return [f.details.get("class") for f in empty_slot.run(page, None)]


@pytest.mark.parametrize("text,cls", [
    ("using terms like “IOP near near me” or “IOP programs near me.”", "doubled_word"),
    ("the right opioid opioid addiction and dependence drug detoxification program", "doubled_word"),
    ("benefits will vary from from provider to provider and from state to state.", "doubled_word"),
    ("contributing to overdose-related deaths.These numbers highlight a growing need", "missing_space"),
    ("aftercare as you move from benzo addiction to ongoing recovery.Call our experts", "missing_space"),
    ("you will be able to go through medically-asssited detox and be supervised 24/7",
     "repeated_letters"),
    ("“ Is Buspirone addictive?. ” This guide addresses these issues", "stacked_punctuation"),
])
def test_real_corruption_from_live_pages_is_caught(text, cls):
    assert cls in _classes(_blocks(text))


def test_shattered_paragraph_is_caught():
    """Live on GL: an entire paragraph with spaces driven into the middle of its words."""
    text = ("Al ways fo llow t he inst ructions pr ovided by y our do ctor or t he gui delines "
            "on t he pa ckage. Take the pill with a full glass of water.")
    assert "shattered_text" in _classes(_blocks(text))


def test_lorem_ipsum_on_a_live_page_is_caught():
    """GL's /local-business-page-dev/ shipped 30 blocks of Latin to the public site."""
    page = _blocks("Lorem ipsum dolor sit amet, consectetur adipiscing elit. Maecenas convallis "
                   "enim quis nunc ultricies, a iaculis ex malesuada.")
    assert "lorem_ipsum" in _classes(page)


@pytest.mark.parametrize("text", [
    # legitimate English doubles — a closed set, named in the check
    "He had had enough of the waiting list before he called us.",
    "The thing that that patient needed was a longer stay.",
    # abbreviations and web addresses: the vetoes that make missing_space safe
    "Referrals come from Dr.Smith only after an assessment.",
    "Read more at gratitudelodge.Com for details of the programme.",
    # uppercase runs are acronyms and Roman numerals, never keystroke damage
    "The NIAAA and CCC both publish Type III diagnostic criteria for review.",
    # ordinary prose with short words must never look shattered
    "We ask if it is ok to go in to see him at the unit on a Tuesday or a Friday.",
    # an ellipsis is punctuation, not damage
    "She paused... then agreed to enter the programme the following morning.",
])
def test_ordinary_prose_stays_silent(text):
    assert _classes(_blocks(text)) == []


def test_a_seam_between_two_blocks_is_never_a_defect():
    """The lesson that cost 3,837 false findings: `visible_text` concatenates separate elements, so
    a heading ending "costs." beside a link reading "Verify" reads as "costs.Verify"."""
    page = _blocks("Insurance can cover up to 100% of treatment costs.", "Verify Insurance")
    assert "missing_space" not in _classes(page)


def test_pages_without_body_landmarks_stay_silent_rather_than_guess():
    """No body block means no block-wise read. These families say nothing instead of scanning the
    concatenated page, which is where the seam artifacts come from."""
    page = ParsedPage(url=URL, visible_text="deaths.These numbers highlight a growing need",
                      blocks=[Block(tag="p", text="deaths.These numbers", region="footer", group=0)])
    assert "missing_space" not in _classes(page)


# ---------------------------------------------------------------------------------------------
# PIPELINE SENTINEL RESIDUE (2026-08-27). Found by reading the content tool's own source: when a
# geo statistic is missing it substitutes rather than leaving a blank, and the substitute reaches
# the page looking like a value. Every POSITIVE below is verbatim from a live page.

@pytest.mark.parametrize("text,cls", [
    # CAD, live
    ("In communities with a total population of Not found as of 2023, there are approximately 68 "
     "treatment centers", "not_found_sentinel"),
    # RR, live
    ("news reports involving substances such as Not found, reflecting how stimulant use continues",
     "not_found_sentinel"),
    # COC / RR / GL, live
    ("Of these, there are 1 programs that maintain ratings of at least 4 stars", "count_disagreement"),
    ("No fewer than 1 programs within 20 of Oak Hill accept private insurance", "count_disagreement"),
    ("Around 1 programs accept private insurance near Rancho Santa Margarita", "count_disagreement"),
])
def test_pipeline_sentinels_that_reached_a_live_page(text, cls):
    assert cls in [f.details.get("class") for f in _run(text)]


@pytest.mark.parametrize("text", [
    # a singular noun after 1 is CORRECT — the first version of this check flagged "more than 1
    # rehab" on GL, which is perfectly good English
    "There are more than 1 rehab and treatment program available within 15 miles.",
    "We found 1 program that accepts this insurance plan in the area.",
    "Only 1 facility in the county offers medically supervised detox.",
    # hyphenated drug names ending in -1 were every false positive measured on live pages
    "GLP-1 medications may reduce cravings, and GLP-1 receptors are found in the brain.",
    "How GLP-1 Is Changing Medication-Assisted Treatment for Addiction",
    # a 404-style capital F, and ordinary lowercase prose, are not the sentinel
    "The page you requested returned a 404 Not Found error from the server.",
    "If the medication is not found at your pharmacy, ask them to order it.",
])
def test_correct_prose_is_not_flagged_as_sentinel_residue(text):
    classes = [f.details.get("class") for f in _run(text)]
    assert "count_disagreement" not in classes
    assert "not_found_sentinel" not in classes
