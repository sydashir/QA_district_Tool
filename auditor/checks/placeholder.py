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
from ..report import Finding, Severity

CHECK = "placeholder"

_GEODATA_GFV = Path(
    os.getenv("GEODATA_SERVICES_DIR", str(Path.home() / "Documents/workk/district/services"))
) / "geo_field_validator.py"

_CURLY_RE = re.compile(r"\{\{[^}]+\}\}")


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


def run(parsed: ParsedPage, config) -> list[Finding]:
    findings: list[Finding] = []
    text = parsed.visible_text  # already script/style/cfemail-stripped

    # On a LIVE page, ANY visible [acf field=...] token is unresolved (do NOT apply the
    # sheet-stage skip-list — that only makes sense pre-import).
    for name in dict.fromkeys(_extract_acf_tokens()(text)):
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.ERROR,
            issue="unresolved [acf field] token in visible text", location="body",
            snippet=f"[acf field={name}]",
            suggestion="WordPress did not resolve this ACF token on the live page."))

    for token in dict.fromkeys(m.group(0) for m in _CURLY_RE.finditer(text)):
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.ERROR,
            issue="leftover {{var}} token in visible text", location="body",
            snippet=token[:60]))

    return findings
