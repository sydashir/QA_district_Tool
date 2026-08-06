"""Dictionary spellcheck (v1 deterministic, no AI) — ticket 86bapv5yn line 2.

`misspelling.py` catches the SEVEN strings Connor reported. It is zero-false-positive by
construction and stays exactly as it is — but a typo nobody has reported is invisible to it. This
check closes that: a real English dictionary, so an unreported typo is caught the first time.

**Why this is viable here and would not be otherwise.** A bare dictionary on this corpus is
unusable — pyspellchecker's 160,572-word English list does not know `buprenorphine`, `naloxone`,
`benzodiazepine` or `comorbid`, and every geo page is full of city and county names. Three
vocabularies are layered on top of it, and the first two already existed:

1. **Proper nouns** (`allowlist.json`, 2,488 terms) — mined from 16,216 crawled pages, counties,
   and the brand guides. Proven across four AI runs to produce zero proper-noun false positives.
2. **Domain vocabulary** (`domain_vocab.json`) — lowercase clinical terms mined from body copy,
   trusted only when they appear on **>= 2 brands**. One site's typo is a typo; a word nine
   independently edited sites all use is the industry's vocabulary.
3. **The brand's own canon** — its name and location words, from config.

SINGLE-BRAND unknowns are deliberately NOT trusted. That is exactly where real typos live, so
admitting them would blind the check to the thing it exists for.

Scope is body text, meta title, meta description AND the slug. The ticket names "on-page content,
meta titles, URLs" explicitly, and three of Connor's seven confirmed misspellings were in URLs. A
slug stays a separate finding class because fixing one costs a redirect, which is a different
decision from editing a sentence.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from ..parse import ParsedPage
from ..report import Finding, Severity, make_fingerprint

CHECK = "spelling"

# PARKED — NOT IN THE DEFAULT PIPELINE. Measured on 150 GL pages: 9% precision by distinct word,
# 12% by finding count (7 real of 78). See ARCHITECTURE.md D10 for the full result and why tuning
# cannot fix it: the residue is rare single-brand pharmaceutical vocabulary (isotonitazene,
# solriamfetol, pitolisant), and single-brand rarity is exactly the signal that identifies a typo,
# so the two are indistinguishable on this corpus.
#
# Kept, not deleted, because the measurement is the asset and the code is correct — it simply does
# not clear the cry-wolf bar. Enable with SPELLCHECK=1 to reproduce or to re-measure after adding a
# pharmaceutical vocabulary.
ENABLED = os.getenv("SPELLCHECK", "") not in ("", "0", "false", "False")

_AI_DIR = Path(__file__).resolve().parent.parent / "ai"
ALLOWLIST_PATH = _AI_DIR / "allowlist.json"
VOCAB_PATH = _AI_DIR / "domain_vocab.json"

# Body words: letters only, >= 4 chars. Anything shorter is dominated by abbreviations and noise,
# and a 3-letter typo is not reliably distinguishable from an acronym.
_WORD = re.compile(r"[A-Za-z][A-Za-z']*(?:-[A-Za-z']+)*")
_CURLY = str.maketrans({"\u2019": "'", "\u2018": "'"})
# Endings that make a known base word into another real word the 160k list may simply lack:
# "accreditation" -> "accreditations", "stressor" -> "stressors".
_SUFFIXES = ("s", "es", "'s", "ed", "d", "ing", "ly", "er", "ers", "est")
_SLUG_WORD = re.compile(r"[a-z]{4,}")
MIN_LEN = 4


@lru_cache(maxsize=1)
def _speller():
    from spellchecker import SpellChecker
    return SpellChecker(language="en")


@lru_cache(maxsize=1)
def _extra_vocab() -> frozenset[str]:
    """Everything beyond the English dictionary, lowercased."""
    out: set[str] = set()
    for path in (ALLOWLIST_PATH, VOCAB_PATH):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for term in data.get("terms", []):
            t = str(term).strip().lower()
            if t:
                out.add(t)
                # "Newport Beach" contributes both halves; a multiword term is never one token
                out.update(p for p in re.split(r"[\s/]+", t) if len(p) >= 3)
    return frozenset(out)


def _brand_vocab(config) -> frozenset[str]:
    words: set[str] = set()
    for attr in ("brand", "base_url"):
        v = str(getattr(config, attr, "") or "")
        words.update(w.lower() for w in re.findall(r"[A-Za-z]{3,}", v))
    return frozenset(words)


def _is_ignorable(token: str, first_in_sentence: bool = False) -> bool:
    """Shapes that are not English words at all, so a dictionary has nothing to say about them."""
    if len(token) < MIN_LEN or any(c.isdigit() for c in token):
        return True
    if token.isupper():
        return True                       # acronym (PHP, EMDR, LGBTQ) — not a spelling question
    if token[1:].lower() != token[1:]:
        return True                       # internal capitals: CamelCase, brand styling
    # A CAPITALISED word inside a sentence is a proper noun — a surname, a place, a product. A
    # dictionary has no opinion on somebody's name, and 27% of the first GL run was staff surnames
    # (Krier, Pennino, Muldoon, Reitz). Sentence-initial capitals are excluded from this rule
    # because there the capital carries no information.
    if token[:1].isupper() and not first_in_sentence:
        return True
    return False


def _candidates(text: str) -> Counter:
    """Words worth asking a dictionary about, with sentence-initial position tracked."""
    text = (text or "").translate(_CURLY)   # typography, not spelling: `we’ll` IS `we'll`
    out: Counter = Counter()
    for m in _WORD.finditer(text):
        tok = m.group(0).strip("'-")
        before = text[:m.start()].rstrip()
        first = not before or before[-1] in ".!?:;\n" or before.endswith(("\u201c", '"'))
        if not _is_ignorable(tok, first_in_sentence=first):
            out[tok] += 1
    return out


def _base_is_known(word: str, spell, known: frozenset) -> bool:
    """True when the word is an ordinary inflection of something known.

    The 160k list carries "accreditation" but not "accreditations", and "stressor" but not
    "stressors" — 27% of the first GL run was this shape. Stripping a common ending is far safer
    than proposing a correction for a word that is not wrong.
    """
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            base = word[: -len(suf)]
            for cand in (base, base + "e", base[:-1] if base.endswith(("i",)) else "",
                         (base[:-1] + "y") if base.endswith("i") else ""):
                if cand and (cand in known or spell.known([cand])):
                    return True
    return False


def _unknown(tokens, config) -> set[str]:
    """Lowercased tokens no vocabulary layer recognises."""
    spell = _speller()
    known = _extra_vocab() | _brand_vocab(config)
    lowered = {t.lower().translate(_CURLY) for t in tokens}
    candidate = {t for t in lowered if t not in known}
    # hyphenated compounds are fine when every part is a real word ("co-occurring", "pre-screening")
    simple, compound = set(), set()
    for t in candidate:
        (compound if "-" in t else simple).add(t)
    bad = {t for t in spell.unknown(simple)} if simple else set()
    bad = {t for t in bad if not _base_is_known(t, spell, known)}
    for t in compound:
        # short joiners ("pre", "co", "non") are not words to spellcheck on their own
        parts = [p for p in t.split("-") if len(p) >= MIN_LEN]
        if any(p not in known and spell.unknown([p]) and not _base_is_known(p, spell, known)
               for p in parts):
            bad.add(t)
    return bad


def _suggest(word: str) -> str:
    try:
        fix = _speller().correction(word)
    except Exception:
        fix = None
    return fix if fix and fix.lower() != word.lower() else ""


def run(parsed: ParsedPage, config) -> list[Finding]:
    if not ENABLED:
        return []
    findings: list[Finding] = []
    seen: Counter = Counter()

    surfaces = [
        ("page text", parsed.visible_text or ""),
        ("search-result title", parsed.title or ""),
        ("search-result description", parsed.meta_description or ""),
    ]
    for location, text in surfaces:
        counts = _candidates(text)
        for word in sorted(_unknown(counts, config)):
            original = next((w for w in counts if w.lower() == word), word)
            fix = _suggest(word)
            occ = seen[word]
            seen[word] += 1
            slot = "word" if occ == 0 else f"word#{occ}"
            findings.append(Finding(
                url=parsed.url, check=CHECK, severity=Severity.WARNING,
                fingerprint=make_fingerprint(CHECK, slot, parsed.url, word),
                issue=f"possible misspelling: \"{original}\"",
                location=location,
                snippet=original,
                suggestion=(f"\"{original}\" is not in the dictionary and is not one of this "
                            f"network's known brand, place or clinical terms"
                            + (f" — it looks like it should be \"{fix}\". " if fix else ". ")
                            + f"It appears in the {location}. If the word is correct, tell Syed so "
                              f"it can be added to the approved word list."),
                details={"class": "dictionary", "word": original,
                         "suggestion": fix, "surface": location}))

    # SLUG: a separate class because fixing it costs a redirect, not just an edit.
    slug = urlparse(parsed.url).path.rstrip("/").rsplit("/", 1)[-1]
    slug_counts = Counter({w: 1 for w in _SLUG_WORD.findall(slug.lower())})
    for word in sorted(_unknown(slug_counts, config)):
        fix = _suggest(word)
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.ERROR,
            fingerprint=make_fingerprint(CHECK, "slug", parsed.url, word),
            issue=f"possible misspelling in the page address: \"{word}\"",
            location="page address (URL)",
            snippet=slug,
            suggestion=(f"The web address contains \"{word}\", which is not a known word"
                        + (f" and looks like it should be \"{fix}\"" if fix else "")
                        + ". Changing a published address needs a redirect from the old one, so "
                          "confirm the spelling is wrong before changing it — but a typo in a URL "
                          "is visible to every visitor and to Google."),
            details={"class": "slug", "word": word, "suggestion": fix, "slug": slug}))
    return findings
