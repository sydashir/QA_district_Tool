#!/usr/bin/env python3
"""Measure whether the element locator actually resolves, per finding class, against the ship floor.

Syed's acceptance test for element screenshots (2026-08-29), and it is a test rather than
bookkeeping: if `dead_cta` resolves 90% of the time and another class 30%, the second is not a
capability but an occasional accident, and from outside the two are indistinguishable. A class below
`render.shots.SHIP_FLOOR` does not ship its pictures.

This is the TEXT-check half of that measurement. `scripts/render_measure.py` covers the render-layer
findings it generates itself (contrast, tap-target, broken images); those never leave the page they
were measured on. The text checks are different: `phone.py` and `actions.py` record a
`details.selector` during the crawl, and the picture is taken later, from a report, against a page
that has been re-fetched. Whether that selector still resolves hours later is the whole question, so
it has to be measured the same way — replayed from a finished report through the SAME
`render.shots.capture()` the feature uses. A second implementation would measure the wrong thing.

DELIBERATELY NOT MHD: its origin 503s under concurrency (max_concurrency is a locked ceiling of 2)
and a render costs far more requests than a text fetch. Nothing here is worth degrading a live site.

Usage:
    python3 scripts/locator_measure.py gl                     # newest report for GL
    python3 scripts/locator_measure.py gl --classes dead_cta display_dial_mismatch
    python3 scripts/locator_measure.py gl --report reports/gl/20260903-032946 --max-pages 40
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# HOST-ONLY, for the same reason render_measure.py is: `render/` is deliberately NOT copied into the
# image (keeping it out of checks_version is the entire reason it is a separate package) and
# playwright is in neither requirements file. Say that plainly rather than raising a bare import
# error about a file the operator can see sitting right there.
try:
    from playwright.sync_api import sync_playwright

    from render.safety import SafetyLedger, install, prove_attached
    from render.shots import SHIP_FLOOR, ShotTally, capture
except ModuleNotFoundError as exc:  # pragma: no cover - container-only path
    raise SystemExit(
        f"{exc.name} is not available, so this measurement cannot run here.\n"
        f"scripts/locator_measure.py is a HOST-ONLY tool: the container ships neither the render/ "
        f"package nor playwright. Run it on the host, from the repo root.") from exc

from auditor.config import load_brand

VIEWPORT = {"width": 1280, "height": 900}

# The classes that carry a `details.selector`. Named rather than inferred, because a class that
# silently stops emitting selectors should show up as "no findings to measure", not as a clean pass.
DEFAULT_CLASSES = ("dead_cta", "display_dial_mismatch", "cross_brand_dial")

# Be polite: this re-fetches live client pages, and the crawler has its own rate limit that this
# tool is not covered by.
DELAY_S = 1.0


def newest_report(brand: str) -> Path:
    base = ROOT / "reports" / brand.lower()
    dirs = [d for d in base.iterdir()
            if d.is_dir() and (d / "findings.jsonl").is_file()] if base.is_dir() else []
    if not dirs:
        raise SystemExit(f"no report with findings.jsonl under {base}")
    # Report directory names are NOT reliably ordered (a clock skew on 2026-08-10 produced names
    # that sort before older runs), so order by the run's own timestamp, same as the importer.
    def key(d: Path):
        try:
            return json.loads((d / "summary.json").read_text()).get("run_at", "")
        except (OSError, ValueError):
            return ""
    return sorted(dirs, key=key)[-1]


def load_targets(report: Path, classes: tuple[str, ...]) -> dict[str, list[tuple[str, str]]]:
    """url -> [(class, selector)], from a finished report. Deduped per (url, class, selector)."""
    by_url: dict[str, list[tuple[str, str]]] = defaultdict(list)
    seen: set[tuple[str, str, str]] = set()
    with (report / "findings.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            # A resolved finding describes a defect that is GONE — re-fetching its page and failing
            # to find the element would be counted as a locator miss for something that is not
            # there any more. Measure only what the run says is currently present.
            if r.get("status") in ("resolved", "rule_changed", "page_removed"):
                continue
            fp = r.get("fingerprint") or ""
            # The second field is the class, but BOTH checks that emit a selector suffix it for
            # repeat occurrences on one page: phone.py builds "display_dial_mismatch#1", "#2", ...
            # for the 2nd+ (class, tel, displayed) pair on a URL, and actions.py does the same for
            # dead_cta. Splitting on ":" alone yields "display_dial_mismatch#1", which failed the
            # membership test and was dropped WITH NO MESSAGE. On GL run 128 that silently measured
            # 22 of 70 display_dial_mismatch findings and 29 of 41 dead_cta — the first anchor on
            # each page only, which is not a random subset of anything.
            cls = fp.split(":")[1] if ":" in fp else ""
            cls = cls.split("#")[0]
            if cls not in classes:
                continue
            sel = (r.get("details") or {}).get("selector")
            url = r.get("url")
            if not sel or not url:
                continue
            key = (url, cls, sel)
            if key in seen:
                continue
            seen.add(key)
            by_url[url].append((cls, sel))
    return by_url


def measure(brand: str, report: Path, classes: tuple[str, ...],
            max_pages: int) -> tuple[ShotTally, dict[str, int]]:
    cfg = load_brand(brand)
    by_url = load_targets(report, classes)
    if not by_url:
        raise SystemExit(f"no findings with a selector in {classes} — nothing to measure in {report}")

    urls = sorted(by_url)
    dropped = max(0, len(urls) - max_pages)
    urls = urls[:max_pages]
    total = sum(len(by_url[u]) for u in urls)
    print(f"{brand.upper()}  report {report.name}")
    print(f"  {total} selectors across {len(urls)} pages"
          + (f"  (CAPPED: {dropped} further pages not measured)" if dropped else ""))
    # A cap that is hit is REPORTED, never silently applied — the project's standing rule on
    # sampling. Silence here would read as full coverage.

    # What we INTENDED to measure, per class, before anything could fail. Without this a class
    # whose every page times out simply vanishes from the table and the run exits 0 — a silent pass
    # for something that was never tested.
    intended: dict[str, int] = {}
    for u in urls:
        for cls, _ in by_url[u]:
            intended[cls] = intended.get(cls, 0) + 1

    tally = ShotTally()
    errors = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for i, url in enumerate(urls, 1):
            led = SafetyLedger()
            page = browser.new_page(viewport=VIEWPORT)
            try:
                install(page, cfg.base_url, led)
                prove_attached(page, led)      # the canary, every page, no exceptions
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(600)
                for cls, sel in by_url[url]:
                    capture(page, sel, tally=tally, cls=cls)
            except Exception as e:                                   # noqa: BLE001
                # A page that will not load is NOT a locator failure and must not be scored as one;
                # it is a fetch problem, counted separately and reported.
                errors += 1
                print(f"  [{i}/{len(urls)}] FETCH FAILED {url} — {type(e).__name__}: {e}"[:150])
            finally:
                page.close()
            time.sleep(DELAY_S)
        browser.close()

    if errors:
        print(f"  {errors} page(s) could not be fetched — excluded from the rates, not counted "
              f"as misses")
    return tally, intended


def report_tally(tally: ShotTally, intended: dict[str, int]) -> int:
    print()
    print(f"  {'class':26s} {'n':>4s} {'captured':>9s} {'0x0':>5s} {'none':>5s} {'many':>5s} "
          f"{'dup':>5s} {'located':>8s} {'hit':>7s}  verdict")
    worst_ok = True
    for cls in sorted(set(tally.per_class) | set(intended)):
        if cls not in tally.per_class:
            # Requested, present in the report, and never actually attempted — every page carrying
            # it failed to load. Silence here would read as a pass, so it fails loudly instead.
            print(f"  {cls:26s} {intended.get(cls, 0):4d} {'-':>9s} {'-':>5s} {'-':>5s} {'-':>5s} "
                  f"{'-':>5s} {'-':>7s} {'-':>6s}  NOT MEASURED")
            worst_ok = False
            continue
        d = tally.per_class[cls]
        loc, hit = tally.located_rate(cls), tally.hit_rate(cls)
        ok = loc >= SHIP_FLOOR
        worst_ok &= ok
        print(f"  {cls:26s} {d['attempted']:4d} {d['captured']:9d} {d['uncapturable']:5d} "
              f"{d['none']:5d} {d['many']:5d} {d.get('via_duplicates', 0):5d} "
              f"{loc:7.0%} {hit:6.0%}  {'SHIPS' if ok else 'BELOW FLOOR'}")
    print(f"\n  floor is {SHIP_FLOOR:.0%} on the LOCATED rate (an element with no box is the "
          f"page's layout, not a locator failure).")
    print(f"  `dup` = resolved because the selector matched several BYTE-IDENTICAL copies of one "
          f"element\n     (same tag, href and text) and the marker picked the visible one. Counted "
          f"apart from the rates\n     on purpose: if it ever becomes most of a class, the CSS path "
          f"has stopped discriminating.")
    below = tally.below_floor()
    if below:
        print(f"  BELOW FLOOR, do not ship pictures for: {', '.join(below)}")
    missed = sorted(c for c in intended if c not in tally.per_class)
    if missed:
        print(f"  NOT MEASURED AT ALL (every page carrying them failed to load): {', '.join(missed)}"
              f"\n     That is not a pass. Re-run before reading anything into the table above.")
    # Also flag a class measured on materially fewer selectors than the report holds — a partial
    # denominator produces a rate that looks like coverage and is not.
    for cls in sorted(tally.per_class):
        got, want = tally.per_class[cls]["attempted"], intended.get(cls, 0)
        if want and got < want:
            print(f"  PARTIAL: {cls} measured {got} of {want} selectors "
                  f"({want - got} lost to failed pages)")
    return 0 if worst_ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("brand")
    ap.add_argument("--report", default=None, help="report dir (default: newest for the brand)")
    ap.add_argument("--classes", nargs="+", default=list(DEFAULT_CLASSES))
    ap.add_argument("--max-pages", type=int, default=60)
    args = ap.parse_args()

    if args.brand.lower() == "mhd":
        raise SystemExit("MHD is excluded: its origin 503s under concurrency and a render costs "
                         "far more requests than a text fetch. Not worth degrading a live site.")

    report = Path(args.report) if args.report else newest_report(args.brand)
    tally, intended = measure(args.brand, report, tuple(args.classes), args.max_pages)
    return report_tally(tally, intended)


if __name__ == "__main__":
    raise SystemExit(main())
