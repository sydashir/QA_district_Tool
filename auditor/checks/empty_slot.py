"""Empty-variable artifacts (v1 deterministic) — the defect class the client reports most often.

`placeholder.py` catches a LITERAL unresolved token (`[acf field=geo]`). This catches the *other*
half: the token resolved to an **empty string**, so no token remains — just grammatically broken
prose that shipped to a customer-facing page. Real examples from the client (ClickUp 86baawd2a):

    "There are at least outpatient drug rehab programs available within of California"
    "In , the involving substances such as"
    "Among a population of in , overdose outcomes d compared to by %."
    "Among 9861 people in 2023, overdose outcomes d compared to 2022 by 5.96%."

This is deliberately NOT an AI check. The patterns are exact, the cost is zero, and a model would
be both slower and less certain. Phase 2 handles spelling/grammar/context; this handles the
mechanical residue of a failed merge.

Precision over recall, per the project's cry-wolf standard: three tight ERROR patterns plus one
WARNING pattern for the shape that is inherently ambiguous (a stranded single letter). Every
pattern has a negative fixture in tests/test_empty_slot.py proving it stays quiet on real prose.
"""
from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache

from ..parse import ParsedPage
from ..report import Finding, Severity, make_fingerprint

CHECK = "empty_slot"

# Prepositions/adverbs that a geo/date/number variable is normally interpolated after. When the
# variable renders empty these are left dangling — either straight into punctuation or into a
# second preposition.
_PREP = r"(?:in|on|at|of|to|by|for|from|during|within|near|across|between|through|until|since)"

# 1) "In , the ..." / "during , there were" — a preposition running into a comma with nothing
#    between. Requires the comma to be followed by a word, so a genuine list ("in, out") is safe.
_ORPHAN_COMMA = re.compile(rf"\b{_PREP}\s+,\s+\w", re.IGNORECASE)

# 2) "within of California" / "population of in ," — a preposition whose object vanished, leaving
#    it against the next preposition. Measured against live pages, a naive PREP+PREP is TOO LOOSE:
#    "ratings of at least 4" and "Read on for more" are ordinary English and fired as false
#    positives. Two tight signals instead, both verified on production text:
#      (a) PREP + 2-or-more spaces + PREP — the whitespace footprint of a value that rendered
#          empty ("private insurance within  of Aliso Viejo", live on GL);
#      (b) PREP followed by "of"/"in" specifically — "within of", "population of in" are never
#          valid, whereas the valid idioms all run the other way ("of at least", "on for").
_DOUBLE_PREP = re.compile(
    rf"\b{_PREP}\s{{2,}}{_PREP}\b|\b{_PREP}\s+(?:of|in)\s+(?![a-z]*-)", re.IGNORECASE)

# 3) "compared to by %." — a percent sign with no number in front of it.
_EMPTY_PERCENT = re.compile(r"(?<![\d.])\s%")

# 4) "outcomes d compared to" — a stranded single letter mid-sentence: the surviving fragment of a
#    merge field (e.g. "increase{d}"). Ambiguous by nature ("vitamin d"), so WARNING not ERROR, and
#    tightly bounded: lowercase consonant only, between two lowercase words, never before a period
#    (which would be an initial like "J. Smith") and never after a digit.
_TRUNCATED_WORD = re.compile(
    r"(?<![\d.])\b[a-z]+\s+([bcdfghjklmnpqrstvwyz])\s+[a-z]+\b(?!\.)")

# 5) "within 15 of Costa Mesa" / "at least 10 reviews within 20." — a distance/duration variable
#    rendered its NUMBER but lost its UNIT. Found by the Phase-2 pilot as the single largest class of
#    real AI findings (4 of 6), which is exactly the signal that it belongs here instead: it is a
#    regex, not a judgement. Units that legitimately follow a bare number are excluded, so "within 30
#    days" and "within 24 hours" stay silent.
#    The unit of a RANGE sits after the range's last number ("within 3 to 5 days", "within 5-7
#    days", "within 30, 60, and 90 days" — all live on CAD/GL), so the whole range is consumed
#    first. The group is ATOMIC: without it the engine backtracks to the shortest match, re-reads
#    "within 3" against " to 5 days", finds no unit there, and reports the false positive anyway.
# a comma is part of the number only when digits follow it ("1,500"); a list comma
# ("30, 60, and 90 days") must stay available to the range separator.
_NUM = r"\d+(?:[.,]\d+)*"
_RANGE_SEP = r"(?:\s*(?:,|to|through|and|or|[-–—])\s*)+"
_UNITS = (r"miles?|mi|km|kilometers?|metres?|meters?|m|feet|ft|blocks?|minutes?|mins?|hours?|hrs?"
          r"|days?|weeks?|months?|years?|percent|%")
_MISSING_UNIT = re.compile(
    rf"\bwithin\s+(?>{_NUM}(?:{_RANGE_SEP}{_NUM})*)(?!\s*(?:{_UNITS})\b)", re.IGNORECASE)

# 6) "alcoholusedisorderaud" / "bestrehabcentersincalifornia" — several whole words fused with no
#    separator. A template concatenated fields that were meant to be spaced, or a slug leaked into
#    body copy. Salvaged from the parked dictionary spellchecker (ARCHITECTURE.md D10), where three
#    of its seven REAL findings turned out to be corruption of this kind rather than misspellings.
#
#    Structural, not a spelling judgement: the word list is used only to SEGMENT, never to decide
#    whether a word is spelled correctly. Requires FOUR or more whole words covering the entire
#    token, because ordinary long English ("detoxification", "benzodiazepines") never decomposes
#    that way and two-word compounds ("healthcare", "bandmate") are real words.
_MIN_RUN_TOGETHER_LEN = 16
# THREE, not four: `alcoholusedisorderaud` is alcohol+use+disorder plus an acronym tail. Safe
# because the 16-character candidate floor already excludes the only 3-part English compound
# the segmenter finds — `notwithstanding` (15 chars, not+with+standing).
_MIN_RUN_TOGETHER_PARTS = 3
_MIN_PART_LEN = 3
_MIN_COVERAGE = 0.80        # the words found must account for most of the token
_MAX_TAIL = 4               # "alcoholusedisorderaud" ends in an acronym; tolerate a short remainder
_SEG_MIN_FREQ = 1_000       # measured: `disorder` 4,963, `centers` 3,672, `california` 1,029
_RUN_TOGETHER_CANDIDATE = re.compile(r"\b[a-z]{%d,}\b" % _MIN_RUN_TOGETHER_LEN)


@lru_cache(maxsize=1)
def _full_dictionary():
    """EVERY word the dictionary knows, at any frequency — used only to veto.

    The segmenter set is frequency-bounded, so an uncommon but perfectly real word can decompose
    into common ones: `disproportionately` was the single most frequent finding across 300 live
    pages. Checking the whole token against the full dictionary first kills that entire class.
    """
    try:
        from spellchecker import SpellChecker
    except ImportError:
        return frozenset()
    return frozenset(SpellChecker(language="en").word_frequency.dictionary)


@lru_cache(maxsize=1)
def _segmenter():
    """Common English words used ONLY to SPLIT a token, never to judge whether one is spelled right.

    Frequency-bounded on purpose. With rare entries admitted, almost any string decomposes; with the
    floor at 1,000 the real clinical words stay whole — `detoxification` (117) and
    `benzodiazepines` (0) are not in the set and cannot be built from it either.
    """
    try:
        from spellchecker import SpellChecker
    except ImportError:
        return frozenset()
    freq = SpellChecker(language="en").word_frequency.dictionary
    return frozenset(w for w, n in freq.items() if len(w) >= _MIN_PART_LEN and n >= _SEG_MIN_FREQ)


@lru_cache(maxsize=4096)
def _segments(token: str) -> tuple[str, ...]:
    """Longest full-coverage split into known words, or () when the token is not run-together.

    Dynamic programming rather than greedy: a greedy longest-first pass strands itself on tokens
    like "bestrehabcentersincalifornia", where taking the longest prefix first blocks a split that
    does exist.
    """
    words = _segmenter()
    if not words:
        return ()
    n = len(token)
    # best[i] = the segmentation covering token[:i] that consumed the most characters in words
    best: list[tuple[int, tuple[str, ...]] | None] = [None] * (n + 1)
    best[0] = (0, ())
    for i in range(n):
        if best[i] is None:
            continue
        covered, parts = best[i]
        for j in range(i + _MIN_PART_LEN, n + 1):
            w = token[i:j]
            if w in words:
                cand = (covered + len(w), parts + (w,))
                if best[j] is None or cand[0] > best[j][0]:
                    best[j] = cand
    # accept the furthest split that leaves at most a short unmatched tail
    for end in range(n, max(0, n - _MAX_TAIL) - 1, -1):
        if best[end] and best[end][0] / n >= _MIN_COVERAGE:
            return best[end][1]
    return ()


def _run_together(text: str):
    full = _full_dictionary()
    for m in _RUN_TOGETHER_CANDIDATE.finditer(text):
        tok = m.group(0)
        if tok in full:
            continue                      # a real word, however uncommon — never corruption
        # a domain in a citation ("medicalnewstoday.com") is a URL, not damaged prose
        if text[m.end():m.end() + 1] == "." or text[max(0, m.start() - 1):m.start()] == ".":
            continue
        parts = _segments(tok)
        if len(parts) >= _MIN_RUN_TOGETHER_PARTS:
            yield m, parts


_PATTERNS = (
    ("orphan_comma", _ORPHAN_COMMA, Severity.ERROR,
     "A variable rendered empty and left a dangling comma (e.g. \"In , the …\")."),
    ("double_preposition", _DOUBLE_PREP, Severity.ERROR,
     "A variable rendered empty and left two prepositions together (e.g. \"within of California\")."),
    ("empty_percent", _EMPTY_PERCENT, Severity.ERROR,
     "A percentage variable rendered empty, leaving a bare % sign."),
    ("missing_unit", _MISSING_UNIT, Severity.ERROR,
     "A distance/duration variable kept its number but lost its unit "
     "(e.g. \"within 15 of Costa Mesa\" — miles is missing)."),
    ("truncated_word", _TRUNCATED_WORD, Severity.WARNING,
     "A stranded single letter — usually the surviving fragment of an empty merge field "
     "(e.g. \"outcomes d compared to\")."),
)


def _context(text: str, start: int, end: int, pad: int = 45) -> str:
    return text[max(0, start - pad):min(len(text), end + pad)].strip()


def run(parsed: ParsedPage, config) -> list[Finding]:
    text = parsed.visible_text or ""
    findings: list[Finding] = []
    # The same broken string can appear twice on one page (a repeated template block); key each
    # occurrence so identities stay distinct instead of colliding (the label_leak / phone lesson).
    seen: Counter = Counter()

    for m, parts in _run_together(text):
        tok = m.group(0)
        key = ("run_together", tok)
        occ = seen[key]
        seen[key] += 1
        slot = "run_together" if occ == 0 else f"run_together#{occ}"
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.ERROR,
            fingerprint=make_fingerprint(CHECK, slot, parsed.url, tok),
            issue="several words are run together with no spaces",
            location="page body", snippet=_context(text, m.start(), m.end()),
            suggestion=f"\"{tok}\" reads as {len(parts)} separate words with the spaces missing "
                       f"({' + '.join(parts)}). A template joined fields that should have been "
                       f"separate, or a web address leaked into the wording. Put the spaces back.",
            details={"class": "run_together", "matched": tok, "parts": list(parts)}))

    for cls, pattern, severity, why in _PATTERNS:
        for m in pattern.finditer(text):
            snippet = _context(text, m.start(), m.end())
            key = (cls, m.group(0).lower())
            occ = seen[key]
            seen[key] += 1
            slot = cls if occ == 0 else f"{cls}#{occ}"
            findings.append(Finding(
                url=parsed.url, check=CHECK, severity=severity,
                fingerprint=make_fingerprint(CHECK, slot, parsed.url, m.group(0).lower()),
                issue="empty template variable left broken text on the page",
                location="page body", snippet=snippet,
                suggestion=f"{why} The page renders as written — fix the source field or hide the "
                           f"section when the value is missing.",
                details={"class": cls, "matched": m.group(0)}))
    return findings
