"""Placeholder / ACF-token check (v1 deterministic).

Flags unresolved ``[acf field=...]`` tokens and leftover ``{{var}}`` in the page's
**rendered visible text** — a live-page ``[acf field=...]`` means WordPress failed to
resolve it after import (CLAUDE.md §8).

Two hard lessons baked in:
- Runs on ``parsed.visible_text``, which ``parse.strip_volatile`` has already stripped
  of ``<script>``/``<style>``. The spike's single ACF "hit" was inside a ``<script>``
  guard — a 100% false positive. Scanning raw HTML makes this check pure noise.
- Uses the REAL ``extract_acf_tokens`` from the Fetcher's ``geo_field_validator`` (single
  source of truth), loaded in isolation so we don't copy-paste the regex or pull in
  gspread. The Fetcher stays read-only.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import types
from functools import lru_cache
from pathlib import Path

from ..parse import ParsedPage
from ..report import Finding, Severity, make_fingerprint

CHECK = "placeholder"

_GEODATA_GFV = Path(
    os.getenv("GEODATA_SERVICES_DIR", str(Path.home() / "Documents/workk/district/services"))
) / "geo_field_validator.py"

_CURLY_RE = re.compile(r"\{\{[^}]+\}\}")

# LOREM IPSUM. Found on live pages of >=2 brands by a vocabulary mine, not by any check — there was
# no pattern for it anywhere. Placeholder Latin reaching production is the same defect class as an
# unresolved [acf field] token: a template shipped before its content was written.
#
# Requires THREE distinct markers before firing. Individually several of these are ordinary English
# or real names ("sed", "elit", "magna cum laude"), so one or two hits prove nothing; three in the
# same page do not happen by accident.
_LOREM_WORDS = frozenset("""
lorem ipsum dolor consectetur adipiscing elit eiusmod tempor incididunt labore dolore magna aliqua
enim minim veniam quis nostrud exercitation ullamco laboris aliquip commodo consequat duis aute
irure reprehenderit voluptate velit cillum fugiat nulla pariatur excepteur sint occaecat cupidatat
proident culpa officia deserunt mollit anim laborum
""".split())
_LOREM_MIN_MARKERS = 3
_WORD_RE = re.compile(r"[a-z]+")

# --- TEMPLATE PLACEHOLDERS THAT REACHED PRODUCTION -------------------------------------------
# We believed this module already covered these. It caught `[acf field=…]` and nothing else, while
# the client reported five other shapes across three documents (see
# docs/plans/2026-08-06-client-reported-defect-coverage.md, item B2). Written as PATTERNS rather
# than as those five strings, so the sixth variant nobody has reported yet is caught too.

# 1) A widget's EMPTY-STATE message rendered as page copy: "No content found",
#    "No accordion items found", "No Content Found in this Field".
#    Anchored to a whole LINE — visible_text carries one newline per block element, so a widget's
#    own message is its own line. That is what separates it from "The team found no evidence…",
#    which is a clause inside a sentence.
_EMPTY_STATE = re.compile(
    r"^[\s\W]*no\s+[\w'’\- ]{0,40}?found(?:\s+in\s+this\s+field)?[\s\W]*$",
    re.IGNORECASE | re.MULTILINE)

# 2) An unrendered shortcode: "[sobriety_calculator]", "[contact_form id=4]". Requires a
#    shortcode-SHAPED identifier — an underscore, attributes, or a long lowercase name — so that
#    "[1]" citations and "[sic]" are not swept up.
_SHORTCODE = re.compile(r"\[(?=[a-z])([a-z][a-z0-9]*(?:[_-][a-z0-9]+)+|[a-z_]{6,})"
                        r"(\s+[^\]\n]{0,80})?\]")

# 3) A bare variable NAME left where its value belonged: "What Addictions Do We Treat? - GEO".
#    Restricted to the ACF field names this network actually uses (the Fetcher's own skip-list),
#    matched as a standalone ALL-CAPS token, so ordinary capitals and acronyms are unaffected.
_VARIABLE_NAMES = ("GEO", "CITY", "STATE", "TOPIC", "DRUG", "FULL_GEO", "NEAR_IN", "NEAR-IN")
#    The trailing guard matters: "The GEO Group" is a real company. A variable name standing in for
#    a value is never followed by another capitalised word, so requiring that excludes proper-noun
#    phrases without losing "- GEO" (end of line) or "Rehab in CITY for adults" (mid-sentence).
#    A second guard, added after a live false positive on RR: "Certified by: STATE OF TENNESSEE
#    DEPARTMENT OF MENTAL HEALTH…". The Title-Case guard alone missed it because "OF" is also
#    capitalised. A leaked variable stands among ordinary sentence case; a word inside an ALL-CAPS
#    run is part of that run.
_BARE_VARIABLE = re.compile(
    r"(?<![\w-])(?<![A-Z]{2}\s)(" + "|".join(_VARIABLE_NAMES) + r")(?![\w-])"
    r"(?!\s+[A-Z][a-z])(?!\s+[A-Z]{2,})")

_PLACEHOLDER_PATTERNS = (
    ("empty_state", _EMPTY_STATE, Severity.ERROR,
     "a widget's \"nothing here\" message is showing as page content",
     "This is the message a widget prints when it has nothing to display, and a visitor can read "
     "it. Either fill the section in WordPress or hide the widget when it is empty."),
    ("shortcode", _SHORTCODE, Severity.ERROR,
     "an unrendered shortcode is visible on the page",
     "WordPress did not turn this shortcode into content, so the raw code is on the page. Check "
     "the plugin that provides it is active, or remove the shortcode."),
    ("variable_name", _BARE_VARIABLE, Severity.ERROR,
     "a template field NAME is showing where its value should be",
     "The page is printing the name of a template field instead of the value it should hold — for "
     "example \"- GEO\" where a city name belongs. Fill the field in, or fix the template."),
)


@lru_cache(maxsize=1)
def _extract_acf_tokens():
    """Load the REAL ``extract_acf_tokens`` from the GeoData Fetcher (read-only,
    single source of truth). The module does ``from services.sheets_service import
    SheetsService`` at import time, so we stub *only* that (avoids gspread)."""
    if not _GEODATA_GFV.exists():
        raise FileNotFoundError(
            f"geo_field_validator not found at {_GEODATA_GFV}; "
            "set GEODATA_SERVICES_DIR to the Fetcher's services/ dir.")
    if "services.sheets_service" not in sys.modules:
        sys.modules.setdefault("services", types.ModuleType("services"))
        stub = types.ModuleType("services.sheets_service")
        stub.SheetsService = object  # only the name is needed to satisfy the import
        sys.modules["services.sheets_service"] = stub
    spec = importlib.util.spec_from_file_location("gfv_real", str(_GEODATA_GFV))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.extract_acf_tokens


def _pattern_findings(parsed: ParsedPage) -> list[Finding]:
    text = parsed.visible_text or ""
    out: list[Finding] = []
    seen: dict = {}
    for cls, pattern, severity, issue, suggestion in _PLACEHOLDER_PATTERNS:
        for m in pattern.finditer(text):
            hit = m.group(0).strip()
            key = (cls, hit.lower())
            occ = seen.get(key, 0)
            seen[key] = occ + 1
            slot = cls if occ == 0 else f"{cls}#{occ}"
            out.append(Finding(
                url=parsed.url, check=CHECK, severity=severity,
                fingerprint=make_fingerprint(CHECK, slot, parsed.url, hit.lower()),
                issue=issue, location="page body",
                snippet=text[max(0, m.start() - 40):m.end() + 60].strip(),
                suggestion=suggestion,
                details={"class": cls, "matched": hit}))
    return out


def _lorem_findings(parsed: ParsedPage) -> list[Finding]:
    text = parsed.visible_text or ""
    hits = sorted(_LOREM_WORDS.intersection(_WORD_RE.findall(text.lower())))
    if len(hits) < _LOREM_MIN_MARKERS:
        return []
    i = text.lower().find(hits[0])
    return [Finding(
        url=parsed.url, check=CHECK, severity=Severity.ERROR,
        fingerprint=make_fingerprint(CHECK, "lorem_ipsum", parsed.url),
        issue="placeholder Latin (lorem ipsum) is visible on the page",
        location="page body",
        snippet=text[max(0, i - 40):i + 120].strip(),
        suggestion="This page is showing lorem-ipsum placeholder text — the Latin filler used while "
                   "a design is being built. A visitor sees it. Replace it with the real copy, or "
                   "hide the section until the copy exists.",
        details={"class": "lorem_ipsum", "markers": hits[:10], "marker_count": len(hits)})]


def run(parsed: ParsedPage, config) -> list[Finding]:
    findings: list[Finding] = _lorem_findings(parsed) + _pattern_findings(parsed)
    text = parsed.visible_text  # already script/style/cfemail-stripped

    # On a LIVE page, ANY visible [acf field=...] token is unresolved (do NOT apply the
    # sheet-stage skip-list — that only makes sense pre-import).
    for name in dict.fromkeys(_extract_acf_tokens()(text)):
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.ERROR,
            fingerprint=make_fingerprint(CHECK, "acf", parsed.url, name),
            issue="unresolved [acf field] token in visible text", location="body",
            snippet=f"[acf field={name}]",
            suggestion="WordPress did not resolve this ACF token on the live page."))

    for token in dict.fromkeys(m.group(0) for m in _CURLY_RE.finditer(text)):
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.ERROR,
            fingerprint=make_fingerprint(CHECK, "curly", parsed.url, token),
            issue="leftover {{var}} token in visible text", location="body",
            snippet=token[:60]))

    return findings
