#!/usr/bin/env python3
"""Post-run report: what the new checks added, and the footer-template family.

Reads the LATEST report directory for each brand and answers the three questions asked of the
nine-brand re-run:

1. how many findings the new checks add per brand, and whether any brand's total became unreadable
2. the footer-template corruption family as ONE rollup item
3. (the section-D boundary is a written doc, not a number: docs/WHAT_THE_AUDIT_DOES_NOT_CHECK.md)

Usage:  python3 scripts/post_run_report.py [--baseline reports/_baseline.json]
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REPORTS = REPO / "reports"

# Everything shipped since the acceptance run. `placeholder` and `empty_slot` are pre-existing
# checks that GAINED classes (B2, missing_unit, run_together), so they are counted by CLASS.
NEW_CHECKS = {"actions", "brands", "duplication", "empty_row"}
NEW_CLASSES = {
    "placeholder": {"empty_state", "shortcode", "variable_name", "lorem"},
    "empty_slot": {"missing_unit", "run_together"},
    "broken_links": {"double_slash", "fused_url", "cruft_link", "feed_link"},
}
# One template field, every page fixed at once. Named as a single client-facing item.
FOOTER_FAMILY = {"fused_url", "social_misrouted", "double_slash"}
# Above this a brand's tab stops being triageable by a human in one sitting.
UNREADABLE_ROWS = 400

BRANDS = ["rr", "gl", "ah", "coc", "cad", "ar", "tdrc", "dbh", "mhd"]


def _pages(d: Path) -> int:
    try:
        return int(json.loads((d / "summary.json").read_text()).get("pages_audited") or 0)
    except (OSError, ValueError, TypeError):
        return 0


def run_dirs(brand: str) -> list[Path]:
    base = REPORTS / brand
    if not base.is_dir():
        return []
    # `d.is_dir()` alone is not enough — reports/ also holds stray .md/.log files.
    # Sorted by MTIME, not by name. The 2026-08-10 clock skew (21h behind) produced report dirs
    # whose timestamp names sort BEFORE older runs, so name-sorting silently picked a stale report.
    return sorted((d for d in base.iterdir()
                   if d.is_dir() and (d / "findings.jsonl").is_file()),
                  key=lambda d: d.stat().st_mtime)


def latest_dir(brand: str) -> Path | None:
    runs = run_dirs(brand)
    return runs[-1] if runs else None


def baseline_dir(brand: str, current: Path | None) -> Path | None:
    """The previous FULL run, for an honest before/after.

    Not simply "the run before this one": this repo is full of deliberate small samples
    (`-n 40`, `-n 60`) used while tuning a check, and comparing a 3,500-page run against a
    60-page sample would invent an explosion. A run counts as comparable only if it audited at
    least 70% as many pages as the current one.
    """
    if current is None:
        return None
    want = _pages(current)
    if not want:
        return None
    earlier = [d for d in run_dirs(brand) if d.name < current.name and _pages(d) >= 0.7 * want]
    return earlier[-1] if earlier else None


def load(brand: str):
    """Read findings.jsonl, not findings.csv.

    Two reasons, both found by running this against a real report:
      * only the JSONL carries `details` (and therefore the finding's CLASS and page_count);
      * the CSV includes the run-diff's RESOLVED tail. GL's latest report has 144 `actions` rows
        of which 138 are resolved — the per-page findings correctly retiring after the template
        collapse. Counting those would have reported a 48x explosion that does not exist.
    `publish.py:137` drops `resolved` before writing the sheet, so this mirrors it exactly: what
    is counted here is what a human actually sees in the tab.
    """
    d = latest_dir(brand)
    if not d:
        return None, []
    return d, load_dir(d)


def load_dir(d: Path | None) -> list[dict]:
    if d is None:
        return []
    src = d / "findings.jsonl"
    if not src.exists():
        return []
    rows = []
    with src.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("status") != "resolved":
                rows.append(r)
    return rows


# Statuses that are DIFF BOOKKEEPING, not an open defect: the finding's page was not audited in
# this run, so its state is simply unknown. They dominate a small sample (9,796 of 10,147 rows on
# a 60-page GL run) and are negligible on a full one (23 on RR's 7,962-page run) — so they are
# counted separately rather than folded into "rows a human triages".
# `rule_changed` belongs here too: those rows are in the diff's VANISHED tail — findings whose
# identity changed because a rule did (e.g. the shape-collapse replacing 8,953 per-page RR rows
# with a handful of template rows). They are not open defects, and counting them as open reported
# RR at 25,904 when the sheet correctly showed 16,951.
CARRIED = {"page_unsitemapped", "page_removed", "rule_changed"}


def triageable(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("status") not in CARRIED]


_DIGITS = __import__("re").compile(r"\d+")


def _shape(row: dict) -> str:
    """The defect's SHAPE, with instance detail normalised away.

    "within 25 of Long Beach" and "within 30 of Newport" are the same missing word in the same
    template, and counting them as two problems is how a real one-line fix reads as 4,002.
    """
    d = row.get("details") or {}
    raw = str(d.get("matched") or d.get("label") or d.get("text") or d.get("example")
              or row.get("issue") or "")
    return _DIGITS.sub("N", raw)[:60].lower()


def cls_of(row: dict) -> str:
    return str((row.get("details") or {}).get("class", "") or "")


def is_new(row: dict) -> bool:
    check = row.get("check", "")
    if check in NEW_CHECKS:
        return True
    return cls_of(row) in NEW_CLASSES.get(check, set())


def pages_covered(row: dict) -> int:
    """A collapsed finding stands for many pages; the sheet shows one row."""
    d = row.get("details") or {}
    for key in ("page_count", "source_count"):
        try:
            n = int(d.get(key) or 0)
            if n:
                return n
        except (TypeError, ValueError):
            pass
    srcs = d.get("sources")
    return len(srcs) if isinstance(srcs, list) and srcs else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.parse_args()

    print("=" * 96)
    print("1. WHAT THE NEW CHECKS ADDED, PER BRAND")
    print("=" * 96)
    print(f"{'brand':<6} {'open':>7} {'new':>7} {'new%':>6} {'prev':>7} {'carry':>6} {'len':>5}  top new classes")
    print("-" * 96)

    family_rows = []
    grand = Counter()
    for b in BRANDS:
        d, all_rows = load(b)
        if not all_rows:
            print(f"{b.upper():<6} {'—':>7}   (no report found)")
            continue
        rows = triageable(all_rows)
        carried = len(all_rows) - len(rows)
        new = [r for r in rows if is_new(r)]
        grand["rows"] += len(rows)
        grand["new"] += len(new)
        pct = 100.0 * len(new) / max(1, len(rows))
        base = baseline_dir(b, d)
        was = len(triageable(load_dir(base))) if base else ""
        verdict = "OK" if len(rows) <= UNREADABLE_ROWS else "LONG"
        top = Counter(cls_of(r) or r.get("check", "") for r in new).most_common(3)
        top_s = ", ".join(f"{k}×{v}" for k, v in top)
        print(f"{b.upper():<6} {len(rows):>7} {len(new):>7} {pct:>5.1f}% {str(was):>7} "
              f"{carried:>6} {verdict:>5}  {top_s}")
        for r in rows:
            if cls_of(r) in FOOTER_FAMILY:
                family_rows.append((b, r))

    print("-" * 96)
    print(f"{'ALL':<6} {grand['rows']:>7} {grand['new']:>7} "
          f"{100.0 * grand['new'] / max(1, grand['rows']):>5.1f}%")

    print()
    print("=" * 96)
    print("1b. CONCENTRATION — is a long tab many problems, or ONE template repeated?")
    print("=" * 96)
    print("A row count only means something next to the number of DISTINCT defect shapes behind it.")
    print("GL: 4,002 `missing_unit` rows reduce to 2 shapes (\"within N\") across 1,149 pages —")
    print("one template field missing the word \"miles\", not 4,002 problems.\n")
    for b in BRANDS:
        d, all_rows = load(b)
        rows = triageable(all_rows)
        if not rows:
            continue
        worst = Counter(f"{r.get('check')}/{cls_of(r)}" for r in rows).most_common(1)[0]
        key, n = worst
        same = [r for r in rows if f"{r.get('check')}/{cls_of(r)}" == key]
        shapes = {_shape(r) for r in same}
        pages = len({r.get("url") for r in same})
        print(f"  {b.upper():<5} biggest class {key:<34} {n:>6} rows | "
              f"{len(shapes):>4} distinct shapes | {pages:>5} pages"
              + ("   <-- ONE TEMPLATE" if len(shapes) <= 3 and n > 100 else ""))

    print()
    print("=" * 96)
    print("2. FOOTER-TEMPLATE CORRUPTION FAMILY — one template field, every page at once")
    print("=" * 96)
    if not family_rows:
        print("  none found")
    else:
        by_brand = defaultdict(list)
        for b, r in family_rows:
            by_brand[b].append(r)
        total_pages = 0
        for b, rs in sorted(by_brand.items()):
            pages = sum(pages_covered(r) for r in rs)
            total_pages += pages
            kinds = Counter(cls_of(r) for r in rs)
            print(f"  {b.upper():<5} {len(rs):>3} finding(s) covering {pages:>6} page(s)   "
                  f"{dict(kinds)}")
            for r in rs[:3]:
                print(f"        - {r.get('issue','')[:86]}")
        print(f"\n  {len(family_rows)} findings across {len(by_brand)} brands, "
              f"covering {total_pages} pages — all of them ONE template field each.")

    print()
    print("=" * 96)
    print("3. BOUNDARY DOC (section D)")
    print("=" * 96)
    doc = REPO / "docs" / "WHAT_THE_AUDIT_DOES_NOT_CHECK.md"
    print(f"  {'WRITTEN' if doc.exists() else 'MISSING'}: {doc.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
