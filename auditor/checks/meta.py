"""Meta / title / URL-basics check (v1 deterministic).

Per-page: missing meta title / description, title length out of bounds, malformed slug
(``^[a-z0-9-]+$``). Cross-page duplicate title / description / H1 detection is done in
the orchestrator (it needs the whole sample), not here.
"""
from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlparse

from ..parse import ParsedPage
from ..report import Finding, Severity, make_fingerprint

CHECK = "meta"

# SEO title length guidance (Google truncates ~60). Bounds are tunable; out-of-bounds
# is a WARNING, not an error. The M1 tuning pass on GL showed 70 flags ~77% of pages
# (GL's rank_math titles run 70-80 chars) — the real target is client-dependent, so
# the length flag is reported as tuning-needed rather than silently re-picked.
TITLE_MIN, TITLE_MAX = 15, 70
_SLUG_RE = re.compile(r"^[a-z0-9-]+$")
# Split a title into its separator-delimited segments (brand suffix, section, etc.).
_TITLE_SEP_RE = re.compile(r"\s*\|\s*|\s+[-–—]\s+")


def run(parsed: ParsedPage, config) -> list[Finding]:
    findings: list[Finding] = []
    # Per-brand title bounds (P4): hashed as a CONFIG component, so a per-brand tune doesn't
    # churn other brands. Module constants are the fallback for configs without thresholds.
    th = getattr(config, "thresholds", None)
    tmin = th.title_min if th is not None else TITLE_MIN
    tmax = th.title_max if th is not None else TITLE_MAX

    if not parsed.title:
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.ERROR,
            fingerprint=make_fingerprint(CHECK, "missing_title", parsed.url),
            issue="missing meta title", location="head"))
    else:
        n = len(parsed.title)
        if n < tmin or n > tmax:
            findings.append(Finding(
                url=parsed.url, check=CHECK, severity=Severity.WARNING,
                fingerprint=make_fingerprint(CHECK, "title_length", parsed.url),
                issue="title length out of bounds", location="head",
                snippet=parsed.title[:90],
                suggestion=f"Title is {n} chars (target {tmin}-{tmax}).",
                details={"title_chars": n}))

        # Sharp, low-FP finding hiding inside the length noise: a segment (e.g. the
        # brand suffix) repeated in the title — "… | Gratitude Lodge | Gratitude Lodge".
        segments = [s.strip().lower() for s in _TITLE_SEP_RE.split(parsed.title) if s.strip()]
        repeated = [s for s, c in Counter(segments).items() if c > 1 and len(s) > 2]
        if repeated:
            findings.append(Finding(
                url=parsed.url, check=CHECK, severity=Severity.WARNING,
                fingerprint=make_fingerprint(CHECK, "repeated_segment", parsed.url),
                issue="repeated segment in title", location="head",
                snippet=parsed.title[:90],
                suggestion=f"Title repeats {repeated!r} (likely a doubled brand suffix).",
                details={"repeated": repeated}))

    if not parsed.meta_description:
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.WARNING,
            fingerprint=make_fingerprint(CHECK, "missing_desc", parsed.url),
            issue="missing meta description", location="head"))

    path = urlparse(parsed.url).path.strip("/")
    slug = path.split("/")[-1] if path else ""
    if slug and not _SLUG_RE.match(slug):
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.WARNING,
            fingerprint=make_fingerprint(CHECK, "malformed_slug", parsed.url),
            issue="malformed slug", location="url", snippet=slug,
            suggestion="Slug should match ^[a-z0-9-]+$."))

    return findings
