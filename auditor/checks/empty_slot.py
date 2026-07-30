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

_PATTERNS = (
    ("orphan_comma", _ORPHAN_COMMA, Severity.ERROR,
     "A variable rendered empty and left a dangling comma (e.g. \"In , the …\")."),
    ("double_preposition", _DOUBLE_PREP, Severity.ERROR,
     "A variable rendered empty and left two prepositions together (e.g. \"within of California\")."),
    ("empty_percent", _EMPTY_PERCENT, Severity.ERROR,
     "A percentage variable rendered empty, leaving a bare % sign."),
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
