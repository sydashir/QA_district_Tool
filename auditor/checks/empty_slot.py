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


# --------------------------------------------------------------------------------------------
# 7-11) CORRUPTION, not incorrectness. Measured on 876 live GL pages / 1.7M body words, 2026-08-24;
# every family below cleared the project's 80% precision bar by hand-classification, and every
# surviving hit was re-verified against the RAW HTML so none of them is an artifact of our own
# parser. The families that FAILED are recorded in ARCHITECTURE.md D12 rather than deleted quietly:
# mid-sentence capitals (11,640 hits, ~0% — proper nouns), stranded fragments (4,054, ~0% — staff
# names), unterminated paragraphs (1,449, ~12% — feature cards legitimately have no full stop),
# verbless sentences (130, ~17%), two-word welds (22, 0% — "undertreated", "paddleboarding").
#
# The one that matters most: **space-before-punctuation, 2,315 hits, 0%.** The raw HTML reads
# `insurance</strong>, easing` — no space at all. Our parser inserts a separator when it closes an
# inline element, so the whole family was measuring parse.py rather than the page. Detecting on
# BLOCK text protects against block seams; it does nothing about inline ones. Any future text
# detector must be checked against raw HTML before it is believed.

# A URL is not prose. Applied to the new families only, so the patterns above keep their existing
# behaviour exactly. Without it `repeated_char_run` fired 485 times, 484 of them on `www` and
# `niaaa` inside citation links.
_URL_IN_TEXT = re.compile(r"https?://\S+|www\.\S+|\S+\.(?:com|org|net|gov|edu|html?)\b\S*")

# 7) "the the", "from from". English does have legitimate doubles, and they are a closed set, so
#    they are named rather than guessed at.
_LEGIT_DOUBLE = frozenset({"had", "that", "blah", "no", "very", "so", "ha", "bye", "night",
                           "new", "york", "walla", "sing", "pago", "baden", "well", "now", "long"})
_DOUBLED_WORD = re.compile(r"\b([A-Za-z]{2,})(\s+)(\1)\b", re.IGNORECASE)

# 8) "deaths.These numbers" — a sentence boundary that lost its space. The vetoes are what make it
#    safe: a TLD after the dot is a web address, and a known abbreviation before it is ordinary
#    prose ("e.g.", "Inc.", "Dr.").
_TLD = frozenset({"com", "org", "net", "gov", "edu", "io", "co", "uk", "us", "info", "health"})
_ABBREV = frozenset({"mr", "mrs", "ms", "dr", "prof", "st", "ave", "inc", "ltd", "co", "vs",
                     "etc", "eg", "ie", "jr", "sr", "ph", "approx", "dept", "est", "fig",
                     "no", "vol"})
_MISSING_SPACE = re.compile(r"\b([A-Za-z]{2,})\.([A-Z][a-z]{2,})\b")

# 9) "medically-asssited" — the same letter three times over. No English word does this, so it is
#    keystroke or pipeline damage. LOWERCASE ONLY: every false positive in the measurement was an
#    acronym or a Roman numeral (`NIAAA`, `CCC`, `III`), all uppercase.
_CHAR_RUN = re.compile(r"\b[a-z]*([a-z])\1{2,}[a-z]*\b")

# 10) "addictive?." — two terminal marks stacked, or a separator doubled.
_STACKED_PUNCT = re.compile(r"[?!]\s*\.|[,;:]\s*[,;:]|\.{4,}")

# 11) Lorem ipsum. Found live on GL's /local-business-page-dev/ — an unfinished development page,
#     publicly reachable, 30 blocks of Latin. Placeholder copy shipped to a customer-facing page is
#     a defect in its own right and is trivially detectable, so it gets its own class instead of
#     surfacing sideways as a doubled word ("Pellentesque pellentesque") the way it first did.
#     Deliberately excludes Latin that is also English ("sit", "in", "at", "do", "sed"), and needs
#     several DISTINCT markers in one block, so a page quoting a Latin phrase cannot trip it.
_LOREM = frozenset("""
lorem ipsum dolor consectetur adipiscing eiusmod incididunt labore aliqua pellentesque curabitur
praesent nullam phasellus suspendisse vestibulum malesuada condimentum tincidunt sagittis ultricies
bibendum dapibus volutpat feugiat facilisis sodales venenatis tristique imperdiet scelerisque
euismod lacinia ornare porttitor egestas molestie rhoncus vulputate hendrerit tortor ligula risus
lectus faucibus luctus nisl purus augue justo felis arcu mattis varius potenti cursus nibh erat
urna metus parturient montes nascetur ridiculus aenean fermentum sollicitudin pharetra gravida
""".split())
_LOREM_MIN_MARKERS = 3
_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")


def _lorem_markers(text: str) -> set[str]:
    return {t.lower() for t in _WORD.findall(text)} & _LOREM


# 12) "Al ways fo llow t he inst ructions pr ovided by y our do ctor" — a whole paragraph with
#     spaces driven into the middle of its words. Live on GL. Nothing about this is a spelling
#     question: the damage is measurable as an abnormal density of one- and two-letter fragments.
#     Tokenising contractions and alphanumerics as SINGLE tokens is what makes it safe — splitting
#     them turned "I've" into "ve" and "CB1" into "CB" and produced the only two false positives
#     the family had.
_SHATTER_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’-]*")
_CSS_LEAK = re.compile(r"[{};]|\w+-\w+:")
_SHORT_OK = frozenset(
    "a i an at be by do go he if in is it me my no of on or so to up us we am as ok oh hi id tv "
    "pm ad rx er iv mg ml dr mr st co re ii".split())
_SHATTER_MIN_TOKENS = 12
_SHATTER_MIN_ODD = 5
_SHATTER_RATIO = 0.10


def _shattered_blocks(parsed: ParsedPage):
    """Body blocks whose words have been broken apart by stray spaces."""
    for b in parsed.blocks:
        if b.region != "body" or not b.text:
            continue
        text = _URL_IN_TEXT.sub(" ", b.text)
        if len(_CSS_LEAK.findall(text)) >= 3:
            continue                      # a stylesheet leaking into text is a different bug
        toks = [t.lower() for t in _SHATTER_TOKEN.findall(text)]
        if len(toks) < _SHATTER_MIN_TOKENS:
            continue
        odd = [t for t in toks if len(t) <= 2 and t.isalpha() and t not in _SHORT_OK]
        if len(odd) >= _SHATTER_MIN_ODD and len(odd) / len(toks) >= _SHATTER_RATIO:
            yield b, len(odd), len(toks)


def _body_blocks(parsed: ParsedPage) -> list[str]:
    """Body blocks, one at a time — and NO whole-page fallback.

    The corruption families must read a single block. `visible_text` concatenates separate
    elements, and reading across that seam invents defects no reader sees: against `visible_text`
    the missing-space family fired 3,837 times instead of 12, because a heading ending "costs."
    followed by a link reading "Verify" is indistinguishable from "costs.Verify".

    Falling back to the whole page when a page exposes no body landmarks would quietly reintroduce
    that (74 hits, still mostly seams). A page we cannot read block-wise is one these families stay
    silent on. Under-reporting on a handful of pages is the cheap failure; inventing defects on
    them is the expensive one, and this project's whole standard is not crying wolf.
    """
    return [b.text for b in parsed.blocks if b.region == "body" and b.text]


def _corruption(text: str):
    """The four corruption families, over URL-stripped text. Yields (class, match)."""
    clean = _URL_IN_TEXT.sub(" ", text)
    for m in _DOUBLED_WORD.finditer(clean):
        if m.group(1).lower() not in _LEGIT_DOUBLE and "\n" not in m.group(2):
            yield "doubled_word", m
    for m in _MISSING_SPACE.finditer(clean):
        if m.group(1).lower() not in _ABBREV and m.group(2).lower() not in _TLD:
            yield "missing_space", m
    for m in _CHAR_RUN.finditer(clean):
        yield "repeated_letters", m
    for m in _STACKED_PUNCT.finditer(clean):
        yield "stacked_punctuation", m


_CORRUPTION_WHY = {
    "doubled_word": "the same word appears twice in a row",
    "missing_space": "two sentences are joined with no space after the full stop",
    "repeated_letters": "a letter is repeated three or more times inside a word",
    "stacked_punctuation": "two punctuation marks are stacked together",
}
_CORRUPTION_FIX = {
    "doubled_word": "Delete the duplicate. This is usually a merge or an edit that was half-undone.",
    "missing_space": "Add the missing space after the full stop.",
    "repeated_letters": "Correct the spelling — no English word repeats a letter three times.",
    "stacked_punctuation": "Remove the extra mark.",
}


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

    # --- lorem ipsum: reported ONCE per page, not once per Latin block ---
    markers = _lorem_markers(text)
    if len(markers) >= _LOREM_MIN_MARKERS:
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.ERROR,
            fingerprint=make_fingerprint(CHECK, "lorem_ipsum", parsed.url, "lorem"),
            issue="placeholder Latin text (lorem ipsum) is live on this page",
            location="page body", snippet=_context(text, 0, 160, pad=0),
            suggestion="This page still carries the dummy text a template ships with, so it was "
                       "published before anyone wrote the real copy. Replace it or unpublish the "
                       "page — it is publicly reachable as it stands.",
            details={"class": "lorem_ipsum", "markers": sorted(markers)[:8]}))

    for b, n_odd, n_tok in _shattered_blocks(parsed):
        key = ("shattered_text", b.text[:60].lower())
        occ = seen[key]
        seen[key] += 1
        slot = "shattered_text" if occ == 0 else f"shattered_text#{occ}"
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.ERROR,
            fingerprint=make_fingerprint(CHECK, slot, parsed.url, b.text[:60].lower()),
            issue="a paragraph has spaces broken into the middle of its words",
            location="page body", snippet=b.text[:180],
            suggestion=f"{n_odd} of {n_tok} words in this paragraph are stray one- or two-letter "
                       f"fragments (\"Al ways fo llow t he inst ructions\"). The text was damaged "
                       f"on its way onto the page, not mistyped — re-paste it from the source.",
            details={"class": "shattered_text", "odd_tokens": n_odd, "tokens": n_tok}))

    for block_text in _body_blocks(parsed):
        for cls, m in _corruption(block_text):
            matched = m.group(0)
            key = (cls, matched.lower())
            occ = seen[key]
            seen[key] += 1
            slot = cls if occ == 0 else f"{cls}#{occ}"
            findings.append(Finding(
                url=parsed.url, check=CHECK, severity=Severity.WARNING,
                fingerprint=make_fingerprint(CHECK, slot, parsed.url, matched.lower()),
                issue=f"broken text: {_CORRUPTION_WHY[cls]}",
                location="page body", snippet=_context(block_text, m.start(), m.end()),
                suggestion=f"{_CORRUPTION_FIX[cls]} Found as {matched.strip()!r}.",
                details={"class": cls, "matched": matched}))

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
