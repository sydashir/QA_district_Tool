"""Result models emitted by checks (M1) and — later — the report writers (M2).

M0/M1 define only the pydantic models. The CSV/JSON writers (per-brand output,
severity rollup, run-diff) are M2 and are intentionally NOT implemented here yet.
"""
from __future__ import annotations

import json
import logging
from enum import Enum

from pydantic import BaseModel, Field

_log = logging.getLogger(__name__)


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


def make_fingerprint(*parts: object) -> str:
    """Stable, human-readable identity for a finding across runs — the diff keys on it
    for new/persisting/resolved. Each check builds it from its INVARIANT (link target,
    e164, token, heading text, page+subtype), NOT the snippet (which shifts). Parts are
    whitespace-collapsed and ':'-joined; empty parts are dropped."""
    return ":".join(
        " ".join(str(p).split()) for p in parts if p is not None and str(p).strip() != ""
    )


class Finding(BaseModel):
    """One issue found on one page by one check. Emitted by the M1 checks."""

    url: str
    check: str  # e.g. "broken_links", "heading_structure", "phone", "blank", "meta"
    fingerprint: str  # stable cross-run identity (make_fingerprint) — required
    severity: Severity
    issue: str  # short issue type/label
    location: str | None = None  # where on the page (selector / section / link target)
    snippet: str | None = None  # evidence snippet
    suggestion: str | None = None  # suggested fix
    details: dict = Field(default_factory=dict)
    # Populated by the in-stream run-diff (auditor/diff.py), not the checks.
    first_seen: str | None = None
    last_seen: str | None = None
    status: str | None = None  # "new" | "persisting" | "resolved"


class PageAudit(BaseModel):
    """All findings for a single page, plus fetch metadata."""

    url: str
    final_url: str | None = None
    status: int | None = None
    fetched_ok: bool = False
    content_hash: str | None = None
    findings: list[Finding] = Field(default_factory=list)


class AuditReport(BaseModel):
    """A full audit run for one brand."""

    brand: str
    base_url: str
    enumeration_method: str
    pages_enumerated: int = 0
    pages_fetched: int = 0
    pages: list[PageAudit] = Field(default_factory=list)


def dedupe_findings(findings: list[Finding]) -> list[Finding]:
    """Collapse only IDENTICAL repeats — same fingerprint AND same content (e.g. the same
    skipped-level pattern emitted twice on one page). Same fingerprint but DIFFERENT content is
    NOT a duplicate: it's the fingerprint scheme failing to distinguish two real problems. We
    KEEP all of them and log LOUDLY — never silently drop, that's the Check #2 pathology (a
    mechanism quietly passing over something it didn't expect)."""
    groups: dict[str, list[Finding]] = {}
    order: list[str] = []
    for f in findings:
        if f.fingerprint not in groups:
            groups[f.fingerprint] = []
            order.append(f.fingerprint)
        groups[f.fingerprint].append(f)

    out: list[Finding] = []
    for fp in order:
        group = groups[fp]
        variants = {json.dumps(f.model_dump(mode="json"), sort_keys=True) for f in group}
        if len(variants) == 1:
            out.append(group[0])  # identical repeats -> collapse silently
        else:
            _log.warning(
                "fingerprint collision: %r maps to %d DIFFERENT findings — scheme flaw, "
                "keeping all (fix the fingerprint, don't drop)", fp, len(variants))
            out.extend(group)  # keep all -> surface it, never silently drop
    return out


# --- M2: report writers (CSV/JSON, severity rollup, run-diff) live here. ---
# Intentionally not implemented in M0.
