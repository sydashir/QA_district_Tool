"""Build the Phase-2 proper-noun allowlist.

Without this a spellchecker flags "Costa Mesa", "buprenorphine", and "Laguna Niguel" on every page
and the review queue is worthless — it is the make-or-break input for the whole AI layer.

Sources, in order of trust:
1. **Our own crawl cache** (`cache/*/pages.json`) — titles + H1s across all crawled brands. This is
   the richest source and it costs nothing: we already crawled the network, so every proper noun the
   sites actually use is sitting on disk. Guard against enshrining a typo by requiring a term to
   appear in **>= MIN_BRANDS brands** — cross-brand agreement is what separates a real proper noun
   from one site's mistake.
2. **The Fetcher's county CSV** (read-only) — national county names, 51 state codes.
3. **The Fetcher's LOCATION_MAP** — CA cities/counties/regions (thin, CA-only; a seed).
4. **Brand guides + NAP** — brands, facilities, execs, clinical terms.

Anything the client has CONFIRMED wrong (`checks/misspelling.KNOWN`) is subtracted at the end, so a
typo that reached two brands can never be allowlisted.

Output `auditor/ai/allowlist.json` is COMMITTED: a run must be reproducible and diffable, and the
file is hashed as a check-version component so a rebuild rule-changes Phase-2 findings rather than
silently resolving them.

Usage: python3 -m auditor.ai.build_allowlist
"""
from __future__ import annotations

import csv
import json
import os
import re
from collections import defaultdict
from pathlib import Path

from ..checks.misspelling import KNOWN, KNOWN_LOWER_ONLY

REPO = Path(__file__).resolve().parent.parent.parent
CACHE = REPO / "cache"
OUT = Path(__file__).resolve().parent / "allowlist.json"

FETCHER = Path(os.getenv("GEODATA_SERVICES_DIR", str(Path.home() / "Documents/workk/district/services"))).parent
COUNTY_CSV = FETCHER / "data" / "integrated_all_drugs_county.csv"

MIN_BRANDS = 2          # a term must appear in >=2 brands to be trusted from crawl data
_TOKEN = re.compile(r"\b[A-Z][a-zA-Z'’\-]{2,}\b")

# Words that are capitalised only because they start a sentence / title-case a heading — they are
# ordinary English and must NOT enter a proper-noun allowlist, or the spellchecker goes blind.
_STOP = {
    "The", "And", "For", "With", "Your", "Our", "You", "We", "How", "What", "When", "Where", "Why",
    "Best", "Top", "Near", "About", "From", "Into", "That", "This", "These", "Those", "Are", "Can",
    "Get", "Find", "More", "Most", "All", "New", "Now", "Also", "Their", "They", "Its", "Has",
    "Have", "Was", "Were", "Been", "Will", "Would", "Should", "Could", "May", "Might", "Must",
}


def from_cache() -> tuple[dict[str, set[str]], int]:
    """{term: {brands}} mined from crawled titles + H1s."""
    seen: dict[str, set[str]] = defaultdict(set)
    pages = 0
    for path in sorted(CACHE.glob("*/pages.json")):
        brand = path.parent.name
        try:
            data = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        pages += len(data)
        for meta in data.values():
            for field in ("title", "h1_text"):
                for tok in _TOKEN.findall(meta.get(field) or ""):
                    if tok not in _STOP:
                        seen[tok].add(brand)
    return seen, pages


def from_county_csv() -> set[str]:
    """County names + state codes from the Fetcher's national CSV (read-only)."""
    out: set[str] = set()
    if not COUNTY_CSV.exists():
        return out
    with COUNTY_CSV.open() as fh:
        for row in csv.DictReader(fh):
            loc = (row.get("location") or "").strip()
            if not loc:
                continue
            name, _, state = loc.partition(",")
            out.update(t for t in _TOKEN.findall(name) if t not in _STOP)
            if state.strip():
                out.add(state.strip())
    return out


def from_brand_guides() -> set[str]:
    """Brand/exec/clinical terms from the exported brand guides, if present on disk."""
    out: set[str] = set()
    for name in ("brand_guide_general.txt", "brand_guide_geo.txt"):
        p = Path.home() / "Documents/workk/district/.tmp_dd" / name
        if p.exists():
            out.update(t for t in _TOKEN.findall(p.read_text(errors="replace")) if t not in _STOP)
    return out


def build() -> dict:
    cache_terms, pages = from_cache()
    trusted_cache = {t for t, brands in cache_terms.items() if len(brands) >= MIN_BRANDS}
    single_brand = {t for t, brands in cache_terms.items() if len(brands) == 1}
    counties = from_county_csv()
    guides = from_brand_guides()

    allow = trusted_cache | counties | guides
    # never allowlist a client-confirmed misspelling, however widespread it is
    wrong = {w.lower() for w in KNOWN} | {w.lower() for w in KNOWN_LOWER_ONLY}
    removed = {t for t in allow if t.lower() in wrong}
    allow -= removed

    return {
        "terms": sorted(allow),
        "meta": {
            "pages_mined": pages,
            "from_cache_trusted": len(trusted_cache),
            "from_cache_single_brand_excluded": len(single_brand),
            "from_county_csv": len(counties),
            "from_brand_guides": len(guides),
            "confirmed_misspellings_removed": sorted(removed),
            "min_brands": MIN_BRANDS,
            "total": len(allow),
        },
    }


if __name__ == "__main__":
    data = build()
    OUT.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    m = data["meta"]
    print(f"allowlist -> {OUT.relative_to(REPO)}")
    print(f"  terms: {m['total']}")
    print(f"  from crawl cache (>={m['min_brands']} brands): {m['from_cache_trusted']}"
          f"   (excluded {m['from_cache_single_brand_excluded']} single-brand terms)")
    print(f"  from county CSV: {m['from_county_csv']}   from brand guides: {m['from_brand_guides']}")
    print(f"  pages mined: {m['pages_mined']}")
    print(f"  confirmed misspellings removed: {m['confirmed_misspellings_removed'] or 'none'}")
