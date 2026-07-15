"""Blank / thin-page check (v1 deterministic).

Flags a missing <h1> and pages whose normalized visible text is below a length
threshold. The threshold is a **tuning knob validated against real GL pages** (M1
brief) — see ``MIN_VISIBLE_CHARS``; it is set from the observed GL distribution, not
hardcoded blind. Section-level blank-ACF detection (CLAUDE.md §7 [h]) is deeper and
tracked as open question #8; M1 does page-level thin + missing-h1.
"""
from __future__ import annotations

from ..parse import ParsedPage
from ..report import Finding, Severity, make_fingerprint

CHECK = "blank"

# Tuned on the real GL sample (see M1 report). Pages below this are genuinely thin;
# GL's normal geo/content pages sit far above it.
MIN_VISIBLE_CHARS = 500


def run(parsed: ParsedPage, config) -> list[Finding]:
    findings: list[Finding] = []

    if not any(h.level == 1 for h in parsed.headings):
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.ERROR,
            fingerprint=make_fingerprint(CHECK, "missing_h1", parsed.url),
            issue="missing <h1>", location="page"))

    n = len(parsed.visible_text)
    if n < MIN_VISIBLE_CHARS:
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.WARNING,
            fingerprint=make_fingerprint(CHECK, "thin", parsed.url),
            issue="thin content", location="page",
            snippet=parsed.visible_text[:80],
            suggestion=f"Visible text is {n} chars (< {MIN_VISIBLE_CHARS}).",
            details={"visible_chars": n}))

    return findings
