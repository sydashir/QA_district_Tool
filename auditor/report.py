"""Result models emitted by checks (M1) and — later — the report writers (M2).

M0/M1 define only the pydantic models. The CSV/JSON writers (per-brand output,
severity rollup, run-diff) are M2 and are intentionally NOT implemented here yet.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class Finding(BaseModel):
    """One issue found on one page by one check. Emitted by the M1 checks."""

    url: str
    check: str  # e.g. "broken_links", "heading_structure", "phone", "blank", "meta"
    severity: Severity
    issue: str  # short issue type/label
    location: str | None = None  # where on the page (selector / section / link target)
    snippet: str | None = None  # evidence snippet
    suggestion: str | None = None  # suggested fix
    details: dict = Field(default_factory=dict)


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


# --- M2: report writers (CSV/JSON, severity rollup, run-diff) live here. ---
# Intentionally not implemented in M0.
