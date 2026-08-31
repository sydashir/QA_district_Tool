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


# WordPress appends `-2` to a slug when the one you asked for is taken, so a trailing `-2` is a
# reliable fingerprint of an accidental duplicate page — which is what the client kept finding by
# hand ("make sure the existing URL for each page does not have -2", ClickUp 86b7xbvb9).
#
# Anchored to the END of the slug, and only when what precedes it is a word rather than a digit.
# `-2` in the middle is ordinary wording (`top-2-rehabs`), and a preceding digit means a real
# number, not a collision counter (`step-12-program`, `covid-19`). Only `-2` — `-3` and beyond do
# occur but are rare enough that including them buys little and risks `phase-3`, `level-3`.
_COLLISION_SLUG_RE = re.compile(r"(?<![0-9])-2$")


def _is_collision_slug(url: str) -> bool:
    slug = urlparse(url).path.strip("/").split("/")[-1]
    return bool(slug) and bool(_COLLISION_SLUG_RE.search(slug))


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
    if _is_collision_slug(parsed.url):
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.WARNING,
            fingerprint=make_fingerprint(CHECK, "collision_slug", parsed.url),
            issue="this page's address ends in -2, which usually means a duplicate",
            location="url", snippet=slug,
            suggestion=("WordPress adds `-2` to a web address when a page with that address "
                        "already exists, so this is usually a second copy of another page rather "
                        "than a page anyone meant to create. Check whether the original still "
                        "exists: if it does, decide which one is real, and redirect the other to "
                        "it. If this `-2` page is the one you want, give it the proper address."),
            details={"class": "collision_slug", "slug": slug}))
    if slug and not _SLUG_RE.match(slug):
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.WARNING,
            fingerprint=make_fingerprint(CHECK, "malformed_slug", parsed.url),
            issue="malformed slug", location="url", snippet=slug,
            suggestion="Slug should match ^[a-z0-9-]+$."))

    return findings
