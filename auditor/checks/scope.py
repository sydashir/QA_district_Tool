"""Geographic scope errors (v1 deterministic) — the "county" that should be "country".

The client-confirmed case: Renaissance Recovery ships national pages headed
**"PTSD TREATMENT CENTERS ACROSS THE COUNTY"** and **"BEST DEPRESSION/ANXIETY TREATMENT CENTERS IN
THE COUNTY"** (Connor Bringas -> Jake, ClickUp 86baawd2a). District Behavioral Health's own
national page reads **"DBT THERAPY ACROSS THE COUNTRY"** — so the correct wording exists in-house
and this is a typo, not a house style. That positive control is why this is safe to automate.

Deterministic on purpose: if a regex nails it, don't spend a token. Phase 2's AI pass handles the
open-ended context-scope class (a page claiming facts outside its declared scope); this handles the
one high-frequency, high-precision instance of it.

Precision guard: a bare "the county" is flagged only on pages that are NOT county-scoped. A real
county page (URL contains ``…-county``) may legitimately say "the county", and a legitimate
reference elsewhere names the county ("in Orange County"), which this never matches.
"""
from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlparse

from ..parse import ParsedPage
from ..report import Finding, Severity, make_fingerprint

CHECK = "scope"

# "across the county" / "in the county" / "throughout the county" — a bare, article-led "county"
# used as a region. Excludes "the county of X" (a real, if formal, construction).
_BARE_COUNTY = re.compile(
    r"\b(?:across|in|throughout|around|within|serving|nationwide in)\s+the\s+county\b"
    r"(?!\s+of\b)", re.IGNORECASE)

# The page is genuinely county-scoped if its URL names a county.
_COUNTY_URL = re.compile(r"-county(?:/|$)|/county/", re.IGNORECASE)


def _declared_scope(parsed: ParsedPage) -> str:
    """Cheap scope signal from what the page says about itself: the URL path, then the H1/title.
    Used only to suppress the finding on genuinely county-scoped pages."""
    path = urlparse(parsed.url).path
    if _COUNTY_URL.search(path):
        return "county"
    h1 = next((h.text for h in parsed.headings if h.level == 1), "") or ""
    if re.search(r"\b[A-Z][a-z]+\s+County\b", f"{h1} {parsed.title or ''}"):
        return "county"
    return "not_county"


def run(parsed: ParsedPage, config) -> list[Finding]:
    if _declared_scope(parsed) == "county":
        return []  # a county page may legitimately say "the county"

    findings: list[Finding] = []
    seen: Counter = Counter()
    # Check headings and title too — the confirmed instances were all in H1s.
    surfaces = [("page body", parsed.visible_text or "")]
    surfaces += [(f"H{h.level}", h.text) for h in parsed.headings if h.text]
    if parsed.title:
        surfaces.append(("title", parsed.title))

    for location, text in surfaces:
        for m in _BARE_COUNTY.finditer(text):
            phrase = m.group(0)
            key = phrase.lower()
            occ = seen[key]
            seen[key] += 1
            slot = "county_for_country" if occ == 0 else f"county_for_country#{occ}"
            findings.append(Finding(
                url=parsed.url, check=CHECK, severity=Severity.ERROR,
                fingerprint=make_fingerprint(CHECK, slot, parsed.url, key),
                issue="\"county\" where \"country\" is meant (national page)",
                location=location,
                snippet=text[max(0, m.start() - 45):m.end() + 45].strip(),
                suggestion="This page is not county-scoped, so \"the county\" reads as a typo for "
                           "\"the country\" — the same wording DBH's national pages get right "
                           "(\"ACROSS THE COUNTRY\"). Usually template-wide; fix the template.",
                details={"class": "county_for_country", "phrase": phrase}))
    return findings
