"""Heading-structure check (v1 deterministic).

Scope: **H1–H3 only** — the client says H4/H5 are SEO-irrelevant (CLAUDE.md §7 [j]),
and the spike's H4/H5 hits were noise. H1-count is unambiguous and ships now; strict
H2/H3 hierarchy rules are open question #5 (unwritten) and are NOT enforced here.

Flags: multiple <h1>, skipped levels within H1–H3, empty headings, template labels
leaking into headings (the spike's "Contact Us (Pillar)" / "…-copy" case).
"""
from __future__ import annotations

from collections import Counter

from ..parse import ParsedPage
from ..report import Finding, Severity, make_fingerprint

CHECK = "heading_structure"

# Internal template/taxonomy labels that must not surface as user-facing headings.
_LABEL_MARKERS = ("(pillar)", "[pillar]", "pillar copy", " copy", "(copy)")


def run(parsed: ParsedPage, config) -> list[Finding]:
    findings: list[Finding] = []
    headings = [(h.level, h.text) for h in parsed.headings if h.level <= 3]

    h1s = [t for lvl, t in headings if lvl == 1]
    if len(h1s) > 1:
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.WARNING,
            fingerprint=make_fingerprint(CHECK, "multi_h1", parsed.url),
            issue="multiple <h1>", location="page",
            snippet=" | ".join(t[:50] for t in h1s[:4]),
            # NOT an SEO penalty (Google permits multiple H1s, confirmed 2026); harm is the
            # one-H1 standard [j] + DOM bloat -> low priority.
            suggestion="Violates the one-H1-per-page standard [j] and adds DOM bloat. Not an "
                       "SEO penalty (Google permits multiple H1s) — low priority.",
            details={"h1_count": len(h1s)}))

    for lvl, text in headings:
        if not text.strip():
            findings.append(Finding(
                url=parsed.url, check=CHECK, severity=Severity.WARNING,
                fingerprint=make_fingerprint(CHECK, "empty", parsed.url, f"H{lvl}"),
                issue="empty heading", location=f"H{lvl}"))

    prev = 0
    for lvl, text in headings:
        if prev and lvl > prev + 1:
            findings.append(Finding(
                url=parsed.url, check=CHECK, severity=Severity.INFO,
                fingerprint=make_fingerprint(CHECK, "skipped", parsed.url, f"H{prev}->H{lvl}", text),
                issue=f"skipped level H{prev}->H{lvl}", location=f"H{lvl}",
                snippet=text[:60],
                # minor: breaks the assistive-tech heading outline slightly. Not a real barrier
                # (screen readers still navigate) and not an SEO factor -> INFO, best-practice nit.
                suggestion="Heading level skipped — a minor document-outline nit, not a barrier."))
        prev = lvl

    # A page can leak the SAME label at two heading positions (e.g. "Services (Pillar)" as both an
    # H1 and an H2). (url, text) alone collides those into one identity and would drop the second
    # (Check #2 pathology). Key on (H{lvl}, occurrence) so each instance keeps its own identity;
    # the first occurrence stays bare "H{lvl}" so the common single-leak fingerprint is stable.
    seen_leak: Counter = Counter()
    for lvl, text in headings:
        low = text.lower()
        if any(m in low for m in _LABEL_MARKERS):
            occ = seen_leak[(lvl, text)]
            seen_leak[(lvl, text)] += 1
            slot = f"H{lvl}" if occ == 0 else f"H{lvl}#{occ}"
            findings.append(Finding(
                url=parsed.url, check=CHECK, severity=Severity.ERROR,
                fingerprint=make_fingerprint(CHECK, "label_leak", parsed.url, slot, text),
                issue="template label leaked into heading", location=f"H{lvl}",
                snippet=text[:80],
                suggestion="Internal label (e.g. '(Pillar)'/'copy') is rendering as a heading."))

    return findings
