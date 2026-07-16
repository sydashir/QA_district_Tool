"""Derived check-version for cache invalidation.

FULLY DERIVED — no manual version constant to remember to bump (that omission is exactly
how GeoData's Check #2 silently passed for months). The version hashes the SOURCE of every
check module plus the check-relevant config, so any logic OR threshold edit auto-invalidates
the cache. Crawl timing is excluded — it doesn't affect check output. Over-invalidation (a
comment/whitespace edit also invalidates) is accepted: a rebuild is one run, and we agreed
over-invalidation beats silent staleness. ``components()`` / ``changed_components()`` expose
the per-file hashes so a run can log WHICH check changed on invalidation.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

CHECKS_DIR = Path(__file__).resolve().parent / "checks"


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def components(config, checks_dir: Path = CHECKS_DIR) -> dict:
    """Everything whose change should invalidate cached findings: each check module's
    source, plus the check-relevant config (which lives outside the source)."""
    comp = {
        f"src:{p.name}": _sha(p.read_text(encoding="utf-8"))
        for p in sorted(checks_dir.glob("*.py"))
    }
    comp["canonical_phones"] = sorted(getattr(config, "canonical_phones", None) or [])
    return comp


def version(config, checks_dir: Path = CHECKS_DIR) -> str:
    blob = json.dumps(components(config, checks_dir), sort_keys=True, default=str)
    return _sha(blob)


def changed_components(old: dict, new: dict) -> list[str]:
    """Which components differ between two runs — for the invalidation log line."""
    keys = set(old) | set(new)
    return sorted(k for k in keys if old.get(k) != new.get(k))
