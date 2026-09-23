#!/usr/bin/env python3
"""Photograph the element behind each stored finding, and write the picture into the database.

WHY THIS EXISTS AS ITS OWN PASS. `render/shots.py` has been built, measured and tested, and **not
one picture had ever reached the database** — `details ? 'shot'` matched 0 of 376,370 rows. The two
callers of `attach_shots` are `scripts/render_measure.py`, which writes JSON to a file and never
touches Postgres, and the test suite. So the report's image markup, the absence wording and the
99%/82% locator measurement all described a feature that nothing in the product ever ran.

**IT IS NOT THE ACCESSIBILITY PASS, and it cannot be.** `scripts/accessibility_pass.py` analyses
markup: `render/markup.py` calls `page.route("**/*", block)` -> `route.abort("blockedbyclient")` and
injects the HTML with `set_content`, so nothing leaves that browser, ever. That is deliberate and
correct for markup analysis — the client's servers never see a browser. But it means the page has no
stylesheet, no images and no fonts, and a screenshot of it would be unstyled black-on-white text
that looks nothing like what a visitor sees. Shipping that to a client as "the element this finding
is about" would be worse than shipping no picture: it would read as evidence the site is broken.

So this pass loads the page FIRST-PARTY, the same way `scripts/locator_measure.py` does — the guard
in `render/safety.py` blocks third parties and trackers and proves the block with a canary on every
page, while the brand's own CSS and images load. What is photographed is what a visitor sees.

Writes `details.shot` (a PNG data URI) or `details.shot_absent` (a reason word that
`scripts/client_report.py` renders as a sentence) onto findings of the brand's LATEST OK RUN.

DELIBERATELY NOT MHD: its origin 503s under concurrency, a render costs far more requests than a
text fetch, and run 130 was refused for reaching only 37.1% of its pages. Nothing here is worth
degrading a live site.

Usage:
    python3 scripts/shot_pass.py gl                 # latest ok run for GL
    python3 scripts/shot_pass.py --all
    python3 scripts/shot_pass.py gl --max-pages 40 --dry-run
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# HOST-ONLY: `render/` is deliberately not copied into the image (that is what keeps it out of
# checks_version) and playwright is in neither requirements file.
try:
    from playwright.sync_api import sync_playwright

    from render.browser import ensure_chromium
    from render.safety import SafetyLedger, SafetyNotArmed, install, prove_attached
    from render.shots import ShotTally, attach_shots
except ModuleNotFoundError as exc:  # pragma: no cover - container-only path
    raise SystemExit(
        f"{exc.name} is not available, so this pass cannot run here.\n"
        f"scripts/shot_pass.py is a HOST-ONLY tool: the container ships neither the render/ package "
        f"nor playwright. Run it on the host, from the repo root.") from exc

from sqlalchemy import select                                          # noqa: E402

from auditor.config import load_brand                                  # noqa: E402

# Imported from the REPORT on purpose: it owns the decision about what the client sees, and this
# pass exists only to photograph that. Importing a script is unusual; duplicating the selection
# logic would be worse, because the copies would drift and the drift would be invisible.
import importlib.util as _ilu                                          # noqa: E402

_spec = _ilu.spec_from_file_location("_client_report", ROOT / "scripts" / "client_report.py")
_cr = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_cr)
selected_fingerprints = _cr.selected_fingerprints
from server.db import SessionLocal                                     # noqa: E402
from server.models import Brand, Finding, Run                          # noqa: E402

VIEWPORT = {"width": 1280, "height": 900}

# Politeness. This re-fetches live client pages and is not covered by the crawler's own rate limit.
DELAY_S = 1.0

# Per-page cap inside attach_shots; a pathological page cannot dominate a brand.
MAX_SHOTS_PER_PAGE = 20

SKIP = {"MHD"}


def latest_ok_run(session, code: str) -> Run | None:
    brand = session.scalar(select(Brand).where(Brand.code == code.upper()))
    if brand is None:
        return None
    return session.scalars(
        select(Run).where(Run.brand_id == brand.id, Run.status == "ok")
        .order_by(Run.started_at.desc()).limit(1)).first()


def targets(session, code: str, run: Run) -> dict[str, list[Finding]]:
    """The findings THE REPORT WILL SHOW, grouped by the page they are on.

    THE REPORT DECIDES; THIS PASS FOLLOWS. The first version photographed anything carrying a
    selector and hoped it overlapped what the client sees. It did not: on GL the shot pass wrote 61
    pictures and the report displayed ZERO, because the report shows the top rows per section and
    those were a different population entirely. Asking `client_report` for its own selection makes
    the two impossible to drift apart — there is one piece of code deciding what the client sees,
    and this reads it rather than guessing at it.
    """
    wanted = set(selected_fingerprints(session, code))
    if not wanted:
        return {}
    rows = session.scalars(
        select(Finding).where(Finding.run_id == run.id,
                              Finding.fingerprint.in_(wanted))).all()
    by_url: dict[str, list[Finding]] = defaultdict(list)
    for f in rows:
        # `_selector_for` also derives one for broken images from their src, so ask the real
        # function rather than testing for details.selector and quietly missing that class.
        from render.shots import _selector_for
        if _selector_for(f):
            by_url[f.url].append(f)
    return by_url


def run_brand(session, browser, code: str, max_pages: int, dry_run: bool,
              tally: ShotTally) -> dict:
    run = latest_ok_run(session, code)
    if run is None:
        print(f"  {code.upper():5s} no completed run — nothing to photograph")
        return {"pages": 0, "shots": 0, "absent": 0}

    by_url = targets(session, code, run)
    if not by_url:
        print(f"  {code.upper():5s} run {run.id}: nothing the report shows names an element")
        return {"pages": 0, "shots": 0, "absent": 0}

    cfg = load_brand(code)
    urls = sorted(by_url)
    dropped = max(0, len(urls) - max_pages)
    urls = urls[:max_pages]
    n_findings = sum(len(by_url[u]) for u in urls)
    print(f"  {code.upper():5s} run {run.id}: {n_findings} findings the report shows, "
          f"on {len(urls)} pages"
          + (f"  (CAPPED: {dropped} more pages not visited)" if dropped else ""))
    # A cap that is hit is REPORTED, never silently applied.

    shots = errors = 0
    # What the network guard actually did, summed over every page of the brand. The first version
    # built a ledger for each page and reported none of it, so "third parties were blocked" was a
    # claim about the configuration rather than a measurement of the run.
    guard = {"pages": 0, "canary_pages": 0, "blocked": 0, "allowed": 0,
             "blocked_hosts": Counter(), "unrecognised": Counter()}
    for i, url in enumerate(urls, 1):
        led = SafetyLedger()
        page = browser.new_page(viewport=VIEWPORT)
        try:
            install(page, cfg.base_url, led)
            # The canary, every page, BEFORE the client page — and deliberately OUTSIDE the
            # page-error handling below. An unproven guard is not "a page that did not load": the
            # first version caught SafetyNotArmed along with every other error, printed that, and
            # went on to the next client page. Now it propagates and the pass stops.
            prove_attached(page, led)
            guard["canary_pages"] += 1
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(700)
                shots += attach_shots(page, by_url[url], tally=tally,
                                      max_shots=MAX_SHOTS_PER_PAGE)
            except Exception as e:                                      # noqa: BLE001
                # A page that will not load is not a locator failure. Say so on the findings rather
                # than leaving them silently blank, which the report would have nothing to explain.
                errors += 1
                for f in by_url[url]:
                    f.details = dict(f.details or {})
                    f.details.setdefault("shot_absent", "none")
                print(f"    [{i}/{len(urls)}] page did not load: {url[:90]} — {type(e).__name__}")
            guard["pages"] += 1
        finally:
            page.close()
            guard["blocked"] += led.blocked
            guard["allowed"] += led.allowed
            guard["blocked_hosts"].update(led.blocked_hosts)
            guard["unrecognised"].update(led.unblocked_third_parties)
        time.sleep(DELAY_S)

    hosts = ", ".join(f"{h} x{n}" for h, n in guard["blocked_hosts"].most_common(6))
    unrec = ", ".join(f"{h} x{n}" for h, n in guard["unrecognised"].most_common(8))
    print(f"    guard: canary blocked on {guard['canary_pages']} of {len(urls)} pages, each before "
          f"the client page loaded; blocked {guard['blocked']} tracker request(s)"
          + (f" ({hosts})" if hosts else " (none were requested)")
          + f"; allowed {guard['allowed']}; unrecognised third parties allowed: {unrec or 'none'}")

    absent = sum(1 for u in urls for f in by_url[u] if (f.details or {}).get("shot_absent"))
    if dry_run:
        session.rollback()
        print(f"    dry run — {shots} shot(s), {absent} explained absence(s), nothing written")
    else:
        session.commit()
        print(f"    wrote {shots} picture(s) and {absent} explained absence(s)"
              + (f"; {errors} page(s) failed to load" if errors else ""))
    return {"pages": len(urls), "shots": shots, "absent": absent,
            "guard": {**guard, "blocked_hosts": dict(guard["blocked_hosts"]),
                      "unrecognised": dict(guard["unrecognised"])}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("brands", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--max-pages", type=int, default=60)
    ap.add_argument("--dry-run", action="store_true",
                    help="photograph but write nothing, to see the counts first")
    args = ap.parse_args()

    codes = ([b.code for b in SessionLocal().scalars(select(Brand).order_by(Brand.code)).all()]
             if args.all else [c.upper() for c in args.brands])
    codes = [c for c in codes if c not in SKIP]
    if not codes:
        raise SystemExit("name at least one brand, or pass --all (MHD is always excluded)")

    tally = ShotTally()
    totals = {"pages": 0, "shots": 0, "absent": 0}
    grand = {"canary_pages": 0, "blocked": 0, "allowed": 0,
             "blocked_hosts": Counter(), "unrecognised": Counter()}
    try:
        ensure_chromium()          # never reach launch() without it (render/browser.py)
        with SessionLocal() as session, sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                for code in codes:
                    got = run_brand(session, browser, code, args.max_pages, args.dry_run, tally)
                    for k in totals:
                        totals[k] += got[k]
                    g = got.get("guard")
                    if g:
                        for k in ("canary_pages", "blocked", "allowed"):
                            grand[k] += g[k]
                        grand["blocked_hosts"].update(g["blocked_hosts"])
                        grand["unrecognised"].update(g["unrecognised"])
            finally:
                browser.close()
    except SafetyNotArmed as e:
        print(f"\nREFUSED — the network guard could not be proven, so no further client page was "
              f"loaded: {e}")
        return 2

    print(f"\n{totals['shots']:,} picture(s), {totals['absent']:,} explained absence(s), "
          f"{totals['pages']:,} page(s) visited")
    print(f"guard: canary proven before {grand['canary_pages']:,} client page load(s); blocked "
          f"{grand['blocked']:,} tracker request(s) "
          f"({', '.join(f'{h} x{n}' for h, n in grand['blocked_hosts'].most_common(8)) or 'none'}); "
          f"allowed {grand['allowed']:,}; unrecognised third-party hosts allowed: "
          f"{', '.join(f'{h} x{n}' for h, n in grand['unrecognised'].most_common(12)) or 'none'}")
    print(f"locator: {tally.summary()}")
    if tally.below_floor():
        print("  A class below the floor still gets its ABSENCE explained in the report — the "
              "finding stands on the page's text either way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
