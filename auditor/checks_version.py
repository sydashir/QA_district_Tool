"""Derived check-version for cache invalidation.

Cached intrinsic findings are reusable only when the page's content_hash AND this check
version both match. The version is DERIVED — a hash of the check-relevant config/constants
plus a manual code version — so a threshold change (e.g. P4's title bound) auto-invalidates
the cache with no remembering to bump. Crawl timing (delay/concurrency/timeout) is NOT
included: it doesn't change check output. ``components()`` exposes the raw parts so a run
can log WHICH component changed on invalidation (no mystery full recomputes).
"""
from __future__ import annotations

import hashlib
import json

from .checks import blank, links, meta, phone, structure

# Bump on check LOGIC / regex changes not captured by the value constants below.
CHECKS_CODE_VERSION = 1


def components(config) -> dict:
    """The check-relevant inputs whose change should invalidate cached findings."""
    canon = getattr(config, "canonical_phones", None) or []
    return {
        "code_version": CHECKS_CODE_VERSION,
        "meta.title_bounds": [meta.TITLE_MIN, meta.TITLE_MAX],
        "blank.min_visible_chars": blank.MIN_VISIBLE_CHARS,
        "structure.label_markers": sorted(structure._LABEL_MARKERS),
        "links.bot_hostile_hosts": sorted(links.BOT_HOSTILE_HOSTS),
        "links.cdn_cgi": links._CDN_CGI,
        "phone.region": phone._REGION,
        "canonical_phones": sorted(canon),
    }


def version(config) -> str:
    blob = json.dumps(components(config), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def changed_components(old: dict, new: dict) -> list[str]:
    """Which components differ between two runs — for the invalidation log line."""
    keys = set(old) | set(new)
    return sorted(k for k in keys if old.get(k) != new.get(k))
