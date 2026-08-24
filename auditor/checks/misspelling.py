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
from functools import lru_cache
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


# =============================================================================================
# SITE-LEVEL typo mining — the live version of MINED above.
#
# A static list goes stale the moment the client publishes new content, and this check's whole
# value is catching the typo nobody has reported yet. But the signal cannot be computed per page:
# "appears 1-3 times" is a statement about the SITE, and on a single page almost every word is
# rare. So the ledger below is filled while each page is parsed (the only moment its text exists —
# `_project` releases the ParsedPage immediately after) and read once, after the crawl.
#
# Cost, measured rather than estimated: 9,176 distinct tokens over 876 GL pages, ~1.2 MB of
# counters and contexts; distinct vocabulary grows sub-linearly with pages, so RR projects to ~5 MB.
# Counting ~1M tokens takes well under a second against a multi-hour crawl.
#
# TWO GATES, and both are correctness rather than caution. Either one, ignored, invents typos:
#   * RESUMED PAGES CARRY NO TEXT. `load_resume` returns projections, so a resumed page never
#     reaches the ledger. If a large share of the audit was resumed, a word that is common on the
#     site can look rare in what we counted.
#   * A SAMPLE IS NOT THE SITE. MHD audits 900 of 11,439 pages; a word appearing 40 times site-wide
#     can appear twice in the sample. Sample frequencies are not site frequencies.
# When either gate fails the check emits NOTHING and says so, rather than guessing.
MIN_COVERAGE = 0.90
_BRITISH_MIN = 2          # one is a typo; two or more is an editorial decision
RARE_MAX = 3
COMMON_MIN = 100
_LETTERS = "abcdefghijklmnopqrstuvwxyz"
_URL_IN_TEXT = re.compile(r"https?://\S+|www\.\S+|\S+\.(?:com|org|net|gov|edu|html?)\b\S*")
_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’-]*")
# ", WI" / ", SC" — a US state code immediately after a word marks that word as a place name.
_PLACE_AFTER = re.compile(r"\s*,\s*(?:A[KLRZ]|C[AOT]|D[CE]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]"
                          r"|N[CDEHJMVY]|O[HKR]|P[AR]|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])\b")


def _is_lorem(text: str) -> bool:
    """Reuses `empty_slot`'s marker set rather than keeping a second copy that can drift."""
    from .empty_slot import _lorem_markers, _LOREM_MIN_MARKERS
    return len(_lorem_markers(text)) >= _LOREM_MIN_MARKERS


class TokenLedger:
    """Body-token counts for one brand, plus one example of every rare token.

    Deliberately stores counts and a single short context per rare token — never page text.
    """

    def __init__(self) -> None:
        self.freq: Counter = Counter()
        self.ever_lower: set[str] = set()
        self.example: dict[str, tuple[str, str]] = {}
        self.placelike: set[str] = set()
        self.pages = 0

    def add_page(self, parsed) -> None:
        self.pages += 1
        for b in getattr(parsed, "blocks", ()) or ():
            if b.region != "body" or not b.text:
                continue
            # Latin filler is not vocabulary and its words are not typos. Without this veto GL's
            # one lorem-ipsum dev page contributed `risus`, `varius`, `potenti` and `mattis`, each
            # of which sits one edit from a common English word purely by accident. `empty_slot`
            # reports the placeholder text itself; it must not also poison this ledger.
            if _is_lorem(b.text):
                continue
            clean = _URL_IN_TEXT.sub(" ", b.text)     # a web address is not prose
            for m in _TOKEN.finditer(clean):
                raw = m.group(0).strip("'’-")
                t = raw.lower()
                if len(t) < 5 or not t.isalpha():
                    continue
                self.freq[t] += 1
                if raw[0].islower():
                    self.ever_lower.add(t)
                if _PLACE_AFTER.match(clean, m.end()) and not _misspells_address_word(t):
                    # "Prairie du Chien, WI" / "Taylors, SC" — a US place name, not a typo. AR
                    # carries city-listing content, and this single shape was three of its five
                    # false positives. A token sitting immediately before a state code is a place;
                    # it is never counted, so it can neither be accused nor inflate a count.
                    self.placelike.add(t)
                    continue
                if t not in self.example:
                    i = m.start()
                    self.example[t] = (parsed.url, clean[max(0, i - 60):i + len(raw) + 60].strip())


# British vs US spelling. The client mandated US English (jake_doc answer [k]), so `behavioural`
# on a US brand's site is a defect — but five of them on one site is ONE editorial decision, not
# five typos, and reporting it five times both buries the real typos and misdescribes the fix.
# Same collapse principle as the template-wide defects: one row per cause.
#
# Detected by TRANSFORMATION rather than a word list, so it needs no maintenance and cannot go
# stale. Each rule is only accepted when applying it to the flagged word yields exactly the common
# form already in use on that same site, so a coincidence cannot pass.
# Anchored where the real rule is anchored. A bare ("ll", "l") looked right and was WRONG: it
# classified `vallium` -> `valium`, a misspelled drug name, as a British spelling. British doubling
# happens in specific SUFFIXES (-lling, -lled, -llor), not anywhere an "ll" occurs.
_BRITISH_RULES = (
    ("our", "or"),          # behaviour -> behavior, colour -> color
    ("re$", "er"),          # centre -> center, metre -> meter
    ("res$", "ers"),        # centres -> centers
    ("ise", "ize"),         # realise -> realize
    ("isation", "ization"),
    ("yse", "yze"),         # analyse -> analyze
    ("lling", "ling"),      # counselling -> counseling
    ("lled", "led"),        # travelled -> traveled
    ("llor", "lor"),        # counsellor -> counselor
    ("ence$", "ense"),      # defence -> defense, licence -> license
    ("ogue$", "og"),        # catalogue -> catalog
    ("ae", "e"),            # paediatric -> pediatric
    ("oe", "e"),            # oestrogen -> estrogen
)


# Address STRUCTURE words, as opposed to place NAMES. "Palm Beach Coutny, FL" is a misspelled
# address; "Prairie du Chien, WI" is a town that happens to sit one edit from "chief". The place
# veto silences both, so this narrow exception rescues the first without re-admitting the second —
# nowhere in the United States is a town called County, Boulevard or Suite.
#
# Measured before choosing this shape: the obvious alternative — "report it when the correction
# ALSO appears before a state code" — rescues `coutny` but re-admits `centre` (Centre, AL),
# `taylors` (Taylors, SC) and `gardena` (Gardena, CA). One finding gained, three invented.
_ADDRESS_WORDS = frozenset({
    "county", "counties", "city", "township", "borough", "parish", "avenue", "street", "road",
    "drive", "boulevard", "suite", "highway", "district", "village", "province",
})


def _misspells_address_word(token: str) -> bool:
    return bool(_edits1(token) & _ADDRESS_WORDS)


def _is_british_variant(wrong: str, correct: str) -> bool:
    """True when `wrong` is `correct` spelled the British way.

    Verified, never assumed: a rule counts only when applying it to the flagged word yields exactly
    the common form already in use on that same site.
    """
    for uk, us in _BRITISH_RULES:
        if uk.endswith("$"):
            stem = uk[:-1]
            if wrong.endswith(stem) and wrong[:-len(stem)] + us == correct:
                return True
        elif uk in wrong and wrong.replace(uk, us, 1) == correct:
            return True
    return False


# A known prefix or suffix bolted onto a known word IS a real word, whether or not the dictionary
# happens to list the combination. This closes the gap that put AR at 67%: `rehydration` and
# `destress` are ordinary English absent from pyspellchecker, and both sat one edit from a common
# word, so both were accused.
#
# Self-contained ON PURPOSE. `/usr/share/dict/words` closes the same gap and wrongly vetoes nothing
# — measured — but it is not installed by default on Ubuntu 24.04, our deploy target. A check whose
# vocabulary differs between the dev machine and the server would emit findings in one place and
# not the other, and this tool reports a DIFF: that variance would surface as phantom new/resolved
# rows every run. Determinism beats the extra coverage.
#
# The remainder floor of 6 is measured, not chosen for neatness. At 5, `recovey` decomposes to
# `re+covey` ("covey" being a real word) and a genuine typo is silently lost; at 7, `destress` slips
# back through. At 6 the veto catches both real words and loses none of the 19 confirmed typos found
# across GL, AR and AH.
_AFFIX_MIN_STEM = 6
_PREFIXES = ("re", "de", "un", "non", "pre", "post", "over", "under", "anti", "semi", "multi",
             "co", "mis", "sub", "inter", "intra", "extra", "micro", "macro", "hyper", "hypo",
             "dis", "out", "self")
_SUFFIXES = ("s", "es", "ed", "ing", "ly", "er", "ers", "ion", "ions", "al", "able",
             "ment", "ments", "ness")


def _is_affixed_real_word(token: str) -> bool:
    d = _dictionary()
    for p in _PREFIXES:
        stem = token[len(p):]
        if token.startswith(p) and len(stem) >= _AFFIX_MIN_STEM and stem in d:
            return True
    for s in _SUFFIXES:
        stem = token[:-len(s)]
        if token.endswith(s) and len(stem) >= _AFFIX_MIN_STEM and stem in d:
            return True
    return False


def _edits1(w: str) -> set[str]:
    out: set[str] = set()
    for i in range(len(w) + 1):
        a, b = w[:i], w[i:]
        if b:
            out.add(a + b[1:])
            for c in _LETTERS:
                out.add(a + c + b[1:])
            if len(b) > 1:
                out.add(a + b[1] + b[0] + b[2:])
        for c in _LETTERS:
            out.add(a + c + b)
    out.discard(w)
    return out


@lru_cache(maxsize=1)
def _dictionary() -> frozenset:
    """Used ONLY to veto — never to accuse. A word being absent from a dictionary says nothing
    (that assumption is what put the parked spellchecker at 9% precision, ARCHITECTURE.md D10);
    a word being PRESENT is proof it is not a typo."""
    try:
        from spellchecker import SpellChecker
    except ImportError:
        return frozenset()
    return frozenset(SpellChecker(language="en").word_frequency.dictionary)


def from_audit(ledger: TokenLedger, audited_pages: int, partial_sample: bool) -> list[Finding]:
    """Typos mined from the whole brand, after the crawl. See the gates above."""
    if partial_sample or not ledger.pages:
        return []
    if audited_pages and ledger.pages / audited_pages < MIN_COVERAGE:
        return []

    common = {t for t, n in ledger.freq.items() if n >= COMMON_MIN}
    full = _dictionary()
    findings: list[Finding] = []
    british: list[tuple] = []
    for token, n in sorted(ledger.freq.items()):
        # An address word is exempt from the rarity test. "coutny" is not a word in any context,
        # so the correction is certain however often it appears — and it WILL appear often, because
        # an address sits in a template and repeats on every page. That is the general limit of the
        # rarity signal (a typo repeated by a template is not rare, so it is not found); here the
        # certainty of the correction lets us step around it. Measured: AH's "Palm Beach Coutny, FL"
        # occurs 4 times, one past RARE_MAX, and was silently missed before this.
        address_typo = _misspells_address_word(token) and token not in full
        if ((n > RARE_MAX and not address_typo) or token in full or token in KNOWN
                or token in ledger.placelike or _is_affixed_real_word(token)):
            continue
        near = sorted(_edits1(token) & common)
        # A plural is not a typo. RR writes "Are You Addicted to Percocets?"; `percocets` is one
        # edit from `percocet` and was accused of being a misspelling of it. Checked against the
        # site's OWN common words rather than a dictionary, because the singular here is a drug
        # brand no dictionary carries.
        near = [c for c in near if token not in (c + "s", c + "es")]
        if not near:
            continue
        if token not in ledger.ever_lower and not address_typo:
            # Proper-noun shaped. Address typos are exempt above: "Coutny" is capitalised because
            # it sits in an address, and its correction "county" is ordinary lowercase English, so
            # this guard would otherwise discard a certain typo.
            # Keep it only when the correction is ALSO proper-noun shaped —
            # i.e. a misspelled name (Adderal -> Adderall), not a name that merely resembles a
            # common word (Humana -> human, SoCal -> social, Clarita -> clarity).
            near = [c for c in near if c not in ledger.ever_lower]
            if not near:
                continue
        url, snippet = ledger.example[token]
        if _is_british_variant(token, near[0]):
            british.append((token, near[0], n, url, snippet))
            continue
        findings.append(Finding(
            url=url, check=CHECK, severity=Severity.WARNING,
            fingerprint=make_fingerprint(CHECK, "mined", url, token),
            issue=f"probable typo: {token!r} appears {n}x on this site and is one letter from "
                  f"{near[0]!r}",
            location="page body", snippet=snippet,
            suggestion=f"{token!r} is used {n} time(s) across the whole site, while {near[0]!r} is "
                       f"used {ledger.freq.get(near[0], 0)} times. A word used once that is one "
                       f"letter from a word used hundreds of times is a typo, not vocabulary. "
                       f"Check it and correct if wrong.",
            details={"class": "mined", "wrong": token, "correct": near[0],
                     "count": n, "candidates": near[:4]}))

    # A single British spelling is a typo; several is an editorial decision, and the fix is one
    # decision too ("this site should be written in US English"), not N separate content tickets.
    if len(british) >= _BRITISH_MIN:
        words = ", ".join(f"{w} -> {c}" for w, c, _, _, _ in british)
        url, snippet = british[0][3], british[0][4]
        findings.append(Finding(
            url=url, check=CHECK, severity=Severity.WARNING,
            fingerprint=make_fingerprint(CHECK, "british_spelling", "site", ""),
            issue=f"this site is written with British spellings, not US ({len(british)} words)",
            location="site-wide", snippet=snippet,
            suggestion=f"The client's standard is US English, and these use the British forms: "
                       f"{words}. That is one editorial decision rather than {len(british)} "
                       f"separate typos — it usually means a writer or a content source is set to "
                       f"British English, so new pages will keep arriving the same way. Fix the "
                       f"source, then the pages.",
            details={"class": "british_spelling",
                     "words": [{"wrong": w, "correct": c, "count": n} for w, c, n, _, _ in british]}))
    else:
        for token, correct, n, url, snippet in british:
            findings.append(Finding(
                url=url, check=CHECK, severity=Severity.WARNING,
                fingerprint=make_fingerprint(CHECK, "mined", url, token),
                issue=f"probable typo: {token!r} appears {n}x on this site and is one letter from "
                      f"{correct!r}",
                location="page body", snippet=snippet,
                suggestion=f"{token!r} is the British spelling of {correct!r}; the client's "
                           f"standard is US English.",
                details={"class": "mined", "wrong": token, "correct": correct, "count": n}))
    return findings
