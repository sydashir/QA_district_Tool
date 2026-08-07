"""The same content twice on one page (v1 deterministic) — B3 and B5.

Reported eight times across the client's attachments:

* **B3, duplicate_paragraph (5 reports)** — "the identical paragraph appears under two different
  headings", "step 2 and step 3 are the same text", "the CBT description is reused for Couples
  Therapy", "three accordion entries share one description". A merge that filled several slots
  from the same source field, so the page reads as though it has more content than it does.
* **B5, duplicate_link (3 reports)** — an interlink widget listing the same link twice.

**Both need a tight discriminator, and it is not the same one.**

A repeated *paragraph* is almost never intentional once it is long enough: a 25-word sentence
appearing twice in body copy is a merge artefact. Short strings are a different matter — "Learn
more", "Verify your insurance", an address, a phone number and a disclaimer all repeat legitimately
down a page, so a length floor does the work.

A repeated *link* is the opposite: the same CTA down a long page is ordinary design, and flagging
it would fire on every page in the network. What the client actually reported is the same link
twice **inside one list** — so the container is the discriminator, not the count.

Boilerplate is excluded for both. Nav and footer repeat by definition, and a mobile + desktop menu
pair renders the same links twice on every page of every brand.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict

from ..parse import ParsedPage
from ..report import Finding, Severity, make_fingerprint

CHECK = "duplication"

# A paragraph must be this long before repetition is evidence of anything. Measured against live
# pages: below it, the repeats are CTAs, addresses, licence lines and headings that legitimately
# recur; above it, a repeat is prose that was pasted twice.
_MIN_DUP_CHARS = 120
_MIN_DUP_WORDS = 18
# Headings legitimately repeat across sections of a long page ("What We Treat" on a hub page), and
# a heading is not "content" in the sense the client meant.
_SKIP_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "th"})
_PUNCT = re.compile(r"[^\w\s]+")
_WS = re.compile(r"\s+")


def _digest(text: str) -> str:
    """Short stable hash of the FULL text — a prefix is not an identity."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _normalise(text: str) -> str:
    """Lowercase, punctuation- and whitespace-insensitive. Two slots filled from one source field
    differ only in typography, so comparing raw text would miss them."""
    return _WS.sub(" ", _PUNCT.sub(" ", text.lower())).strip()


def _duplicate_paragraphs(parsed: ParsedPage) -> list[tuple[str, list[str]]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for b in parsed.blocks:
        if b.region != "body" or b.tag in _SKIP_TAGS:
            continue
        if len(b.text) < _MIN_DUP_CHARS or len(b.text.split()) < _MIN_DUP_WORDS:
            continue
        groups[_normalise(b.text)].append(b.text)
    return [(k, v) for k, v in groups.items() if len(v) > 1]


def _duplicate_links(parsed: ParsedPage) -> list[tuple[str, str, int]]:
    """The same (text, href) more than once INSIDE ONE container."""
    counts: Counter = Counter()
    for a in parsed.actionables:
        if a.region != "body" or not a.href or not (a.text or "").strip():
            continue
        href = a.href.strip()
        if href.startswith("#"):
            continue                      # in-page jumps repeat legitimately
        counts[(a.group, " ".join(a.text.split()), href)] += 1
    return [(text, href, n) for (_g, text, href), n in counts.items() if n > 1]


def run(parsed: ParsedPage, config) -> list[Finding]:
    findings: list[Finding] = []
    seen: Counter = Counter()

    for _key, texts in _duplicate_paragraphs(parsed):
        sample = texts[0]
        occ = seen[("dup_para", _key)]
        seen[("dup_para", _key)] += 1
        slot = "duplicate_paragraph" if occ == 0 else f"duplicate_paragraph#{occ}"
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.WARNING,
            # HASH the whole normalised text, never a prefix. The live run reported
            # "fingerprint collision ... maps to 2 DIFFERENT findings" on an AH page carrying two
            # lorem-ipsum paragraphs that share their first 80 characters.
            fingerprint=make_fingerprint(CHECK, slot, parsed.url, _digest(_key)),
            issue=f"the same paragraph appears {len(texts)} times on this page",
            location="page body",
            snippet=sample[:200],
            suggestion=(f"This wording appears {len(texts)} times on the page. Usually two "
                        f"sections were filled from the same source field, so one of them is "
                        f"showing the wrong text — check which section should say something else."),
            details={"class": "duplicate_paragraph", "count": len(texts),
                     "text": sample[:300]}))

    for text, href, n in _duplicate_links(parsed):
        occ = seen[("dup_link", text, href)]
        seen[("dup_link", text, href)] += 1
        slot = "duplicate_link" if occ == 0 else f"duplicate_link#{occ}"
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.WARNING,
            fingerprint=make_fingerprint(CHECK, slot, parsed.url, text.lower(), href),
            issue=f"the link \"{text}\" is listed {n} times in the same place",
            location="page body",
            snippet=f"{text} -> {href}",
            suggestion=(f"\"{text}\" appears {n} times in one list, pointing at the same page "
                        f"both times. One of them is probably meant to link somewhere else, or "
                        f"the duplicate should be removed."),
            details={"class": "duplicate_link", "text": text, "href": href, "count": n}))
    return findings
