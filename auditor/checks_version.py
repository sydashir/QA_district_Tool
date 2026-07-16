"""Derived check-version for cache invalidation.

FULLY DERIVED — no manual version constant to remember to bump (that omission is exactly
how GeoData's Check #2 silently passed for months). The version hashes the SOURCE of every
check module plus the check-relevant config, so any logic OR threshold edit auto-invalidates
the cache. Crawl timing is excluded — it doesn't affect check output. Over-invalidation (a
comment/whitespace edit also invalidates) is accepted: a rebuild is one run, and we agreed
over-invalidation beats silent staleness. ``components()`` / ``changed_components()`` expose
the per-file hashes so a run can log WHICH check changed on invalidation.

GENERAL RULE (write it down so the trap can't return in a new place): *any module a check's
OUTPUT depends on is a component* — not just the modules that live in ``checks/``. So the
GLOBAL sources ``parse.py`` (builds visible_text) + ``report.py`` (builds fingerprints) are
hashed, and ``nap.py`` is hashed because the phone check classifies against its parse output
(P1). The same rule pre-answers the 845 step: when enumeration findings enter the stream,
``crawl.py``'s enumeration LOGIC (not its timing) becomes a component too, or vanished
enumeration findings would read as genuine ``resolved``.
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
    source, the GLOBAL sources that shape check output/fingerprints (parse.py builds
    visible_text; report.py builds fingerprints), plus the check-relevant config."""
    comp = {
        f"src:{p.name}": _sha(p.read_text(encoding="utf-8"))
        for p in sorted(checks_dir.glob("*.py"))
    }
    for extra in ("parse.py", "report.py"):  # GLOBAL: shape every check's output/fingerprints
        f = checks_dir.parent / extra
        if f.exists():
            comp[f"src:{extra}"] = _sha(f.read_text(encoding="utf-8"))
    # nap.py: a PHONE-scoped dependency (the phone check classifies via its parse, P1). Hashed
    # so a grid-parse edit invalidates phone findings; scoped to phone (not global) in diff.py.
    nap_f = checks_dir.parent / "nap.py"
    if nap_f.exists():
        comp["src:nap.py"] = _sha(nap_f.read_text(encoding="utf-8"))
    # Phone ruler = the canonical VALUES (not their source): an identical-numbers snapshot->live
    # swap is then a no-op for the version. Full NAP value-set when present, else the flat list.
    canon = getattr(config, "canon", None)
    if canon is not None:
        comp["canonical_phones"] = sorted(canon.current_set() | set(canon.stale_retired))
    else:
        comp["canonical_phones"] = sorted(getattr(config, "canonical_phones", None) or [])
    return comp


def version(config, checks_dir: Path = CHECKS_DIR) -> str:
    blob = json.dumps(components(config, checks_dir), sort_keys=True, default=str)
    return _sha(blob)


def changed_components(old: dict, new: dict) -> list[str]:
    """Which components differ between two runs — for the invalidation log line."""
    keys = set(old) | set(new)
    return sorted(k for k in keys if old.get(k) != new.get(k))
