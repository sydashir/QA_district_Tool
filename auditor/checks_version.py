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
    # Scoped deps: modules OUTSIDE checks/ that a check's OUTPUT depends on (the general rule).
    # nap.py -> phone (classifies via its parse, P1); crawl.py -> enumeration (its enumerate
    # logic decides the sitemap/REST sets, hence the 845). Hashed here so an edit moves the
    # version; scoped (not global) to the right check in diff.py so only that check rule-changes.
    for dep in ("nap.py", "crawl.py"):
        f = checks_dir.parent / dep
        if f.exists():
            comp[f"src:{dep}"] = _sha(f.read_text(encoding="utf-8"))
    # OUT-OF-REPO dep: the placeholder check importlib-loads extract_acf_tokens from the GeoData
    # Fetcher's geo_field_validator (path via $GEODATA_SERVICES_DIR). Its content IS the ACF-token
    # ruleset, so it must be a component — otherwise repointing/updating it changes findings while
    # the version stays identical, and vanished findings read as `resolved` instead of rule_changed.
    # (The Fetcher repo is read-only for us; we only hash it.) Path is hashed too: a repoint to a
    # different checkout is a ruleset change even if we can't read the new file.
    try:
        from .checks.placeholder import _GEODATA_GFV as _gfv
        comp["geodata_gfv_path"] = str(_gfv)
        comp["src:geo_field_validator.py"] = (
            _sha(_gfv.read_text(encoding="utf-8")) if _gfv.exists() else "MISSING")
    except Exception:  # never let version() fail — a missing/odd dep must degrade, not crash
        comp["src:geo_field_validator.py"] = "UNAVAILABLE"
    # SPELLING VOCABULARIES. The dictionary spellcheck's output is decided as much by these word
    # lists as by its code: add one term and a finding disappears. Without them as components the
    # vanished finding reads as `resolved` — "someone fixed it" — when nothing on the site changed.
    # Same trap as the ACF ruleset above, same fix. Scoped to `spelling` in diff.py.
    for _name in ("allowlist.json", "domain_vocab.json"):
        _f = checks_dir.parent / "ai" / _name
        comp[f"vocab:{_name}"] = (
            _sha(_f.read_text(encoding="utf-8")) if _f.exists() else "MISSING")
    # The English dictionary itself is a ruleset we do not own — a pyspellchecker upgrade can
    # change which words are known, so its version is a component too.
    try:
        import importlib.metadata as _md
        comp["dict:pyspellchecker"] = _md.version("pyspellchecker")
    except Exception:
        comp["dict:pyspellchecker"] = "UNAVAILABLE"
    # Phone ruler = the canonical VALUES (not their source): an identical-numbers snapshot->live
    # swap is then a no-op for the version. Full NAP value-set when present, else the flat list.
    canon = getattr(config, "canon", None)
    if canon is not None:
        comp["canonical_phones"] = sorted(canon.current_set() | set(canon.stale_retired))
    else:
        comp["canonical_phones"] = sorted(getattr(config, "canonical_phones", None) or [])
    comp["third_party"] = sorted(getattr(config, "third_party", None) or [])  # phone-scoped ruler
    # cross-brand number->owner map (dial-split); a change to any brand's canon can change it.
    bn = getattr(config, "brand_numbers", None) or {}
    comp["brand_numbers"] = sorted(f"{k}:{','.join(v)}" for k, v in bn.items())
    # Per-brand title bounds (P4) — a CONFIG component keyed to meta, so a per-brand tune
    # moves only that brand's version, never RR's. Absent on non-BrandConfig test configs.
    th = getattr(config, "thresholds", None)
    if th is not None:
        comp["title_bounds"] = [th.title_min, th.title_max]
    return comp


def version(config, checks_dir: Path = CHECKS_DIR) -> str:
    blob = json.dumps(components(config, checks_dir), sort_keys=True, default=str)
    return _sha(blob)


def changed_components(old: dict, new: dict) -> list[str]:
    """Which components differ between two runs — for the invalidation log line."""
    keys = set(old) | set(new)
    return sorted(k for k in keys if old.get(k) != new.get(k))
