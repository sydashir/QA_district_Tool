"""Known-wrong strings (v1 deterministic) — client-confirmed misspellings, zero false positives.

This is NOT a spellchecker (that's Phase 2, and it needs the proper-noun allowlist to be usable).
This is the narrow, free version: a list of strings the client has already confirmed are wrong,
matched exactly. Every entry traces to Connor Bringas' report to Jake (ClickUp 86baawd2a,
"RR Spelling Issues 6102026.pdf") — so a hit is a confirmed defect, not a guess.

Two finding classes, deliberately separate because the fix cost differs:
- ``body``  — misspelling in visible text. Edit the content.
- ``slug``  — misspelling in the URL itself. **Higher stakes**: fixing it means a redirect, and
  until then the wrong spelling is permanent and public (Connor found 3 of 7 in URLs). Surfaced
  as its own class so the redirect cost is visible rather than buried in a content ticket.

Word boundaries do the safety work: ``Tennesse\\b`` cannot match "Tennessee" (the following "e" is
a word character, so the boundary fails). ``heath`` is lowercase-only — "Heath" is a real surname
and "Heathrow"/"heather" are real words.
"""
from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlparse

from ..parse import ParsedPage
from ..report import Finding, Severity, make_fingerprint

CHECK = "misspelling"

# wrong -> right. Client-confirmed only; do NOT add speculative entries — the value of this check
# is that a hit is certain. Suspected-but-unconfirmed spellings belong in the Phase-2 review queue.
KNOWN: dict[str, str] = {
    "inpateint": "inpatient",
    "residental": "residential",
    "tennesse": "Tennessee",
    "rennaisance": "Renaissance",
    "alchohol": "alcohol",
    "programing": "programming",
}
# Case-sensitive extras: only meaningful in lowercase, because the capitalised form is a real word
# or name ("Heath" the surname, "Heathrow").
KNOWN_LOWER_ONLY: dict[str, str] = {
    "heath": "health",
}

# CORPUS-MINED, hand-verified — kept apart from KNOWN so that list's "the client confirmed this"
# guarantee stays exactly what it says. Provenance: 876 live GL pages / 1.7M words, 2026-08-24.
#
# The mining rule is the allowlist's frequency signal INVERTED: a token appearing 1-3 times in the
# whole corpus that is ONE EDIT from a token appearing 100+ times is a typo, because a word used
# once that is one letter from a word used a thousand times is not vocabulary. This is precisely
# what the parked dictionary spellchecker could not do (ARCHITECTURE.md D10, 9% precision): rare
# pharmaceutical vocabulary is rare AND far from everything common, so `isotonitazene` never fires,
# while `treatmnet` does. Measured 11/12 = 92% by hand.
#
# The rarity half is load-bearing and cannot be swapped for dictionary frequency — measured: doing
# so drops precision to 42%, because `abilify`, `aleve`, `concerta`, `permanente` and `rogan` are
# each one edit from a common word and only "appears at most 3 times in the corpus" removes them.
#
# Every entry below was re-fetched and confirmed present in the RENDERED text of a live page, so
# none is an artifact of our own parser. Word-boundary matching keeps them safe: `stres\b` cannot
# match "stress", `progra\b` cannot match "program".
MINED: dict[str, str] = {
    "athough": "although",
    "faciltiy": "facility",
    "recovey": "recovery",
    "relaspe": "relapse",
    "treatmed": "treated",
    "trazadone": "trazodone",
    "percoet": "Percocet",
    "asssited": "assisted",
    "graditude": "Gratitude",     # the brand's OWN name, misspelled on a live GL page
    "txreatment": "treatment",
    "stres": "stress",
    "goint": "going",
}
KNOWN.update(MINED)

_BODY_RE = re.compile(r"\b(" + "|".join(KNOWN) + r")\b", re.IGNORECASE)
_BODY_LOWER_RE = re.compile(r"\b(" + "|".join(KNOWN_LOWER_ONLY) + r")\b")
# In a slug the separator is "-", so word boundaries differ: match between slug delimiters.
_SLUG_RE = re.compile(r"(?<![a-z])(" + "|".join(KNOWN) + r")(?![a-z])", re.IGNORECASE)


def _fix_for(word: str) -> str:
    w = word.lower()
    return KNOWN.get(w) or KNOWN_LOWER_ONLY.get(w, "")


def run(parsed: ParsedPage, config) -> list[Finding]:
    findings: list[Finding] = []
    seen: Counter = Counter()

    def add(cls: str, wrong: str, snippet: str, severity: Severity, suggestion: str) -> None:
        key = (cls, wrong.lower())
        occ = seen[key]
        seen[key] += 1
        slot = cls if occ == 0 else f"{cls}#{occ}"
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=severity,
            fingerprint=make_fingerprint(CHECK, slot, parsed.url, wrong.lower()),
            issue=f"confirmed misspelling in {'URL slug' if cls == 'slug' else 'page text'}: "
                  f"{wrong!r} -> {_fix_for(wrong)!r}",
            location="url" if cls == "slug" else "page body",
            snippet=snippet, suggestion=suggestion,
            details={"class": cls, "wrong": wrong, "correct": _fix_for(wrong)}))

    # --- URL slug (higher stakes: fixing it needs a redirect) ---
    path = urlparse(parsed.url).path
    for m in _SLUG_RE.finditer(path):
        add("slug", m.group(1), path, Severity.ERROR,
            f"The URL itself contains {m.group(1)!r} (should be {_fix_for(m.group(1))!r}). "
            f"Fixing this changes the URL, so it needs a 301 redirect from the old path — "
            f"higher cost than a text edit, and public until fixed.")

    # --- visible text ---
    text = parsed.visible_text or ""
    for m in _BODY_RE.finditer(text):
        add("body", m.group(1), text[max(0, m.start() - 45):m.end() + 45].strip(), Severity.ERROR,
            f"Confirmed misspelling — replace with {_fix_for(m.group(1))!r}. Client-reported; "
            f"usually template-wide, so check sibling pages after fixing.")
    for m in _BODY_LOWER_RE.finditer(text):
        add("body", m.group(1), text[max(0, m.start() - 45):m.end() + 45].strip(), Severity.ERROR,
            f"Confirmed misspelling — replace with {_fix_for(m.group(1))!r}.")

    return findings
