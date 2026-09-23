#!/usr/bin/env python3
"""Publish the two RENDERED checks — contrast and broken images — gated on the pixels.

WHAT THIS IS FOR. `render/a11y.py` and `render/images.py` have been built, tested and measured
since 2026-08-25 and **nothing in the product ever published them**: `details ? 'class' in
('color-contrast','broken_image')` matched 0 of 376,370 rows. The only caller was
`scripts/render_measure.py`, which writes a JSON file and never touches Postgres. So Section D went
on telling clients the audit cannot see colour and contrast while the code to see it sat in the
repo. This is the caller.

IT IS NOT THE ACCESSIBILITY PASS AND CANNOT BE. `scripts/accessibility_pass.py` analyses MARKUP:
`render/markup.py` injects HTML with `set_content()` and aborts every request, so it has no
stylesheet and no layout box — its own comment says contrast answers there "would be fiction".
That exclusion is correct and stays. This pass loads pages FIRST-PARTY instead (third parties and
trackers blocked, the block proven by a canary on every page), which is the only way colour can be
judged at all.

THE PIXEL GATE, and why it is a gate rather than a caveat. Contrast was hand-classified against the
pixels on 2026-09-08 across eight brands: **58 decided, 54 true, 4 false — 93%**. Every one of the
four false positives was the same failure: axe reported a background that is not what the browser
paints (it attributed a blue button's background to text sitting on white, 2.76:1 claimed against
9.59:1 measured). That class is detectable, so it is REMOVED rather than shipped:

  * pixels AGREE with axe          -> publish, marked verified
  * pixels DISAGREE materially     -> DROP. Never published, counted and reported.
  * pixels cannot decide           -> publish, marked unclear. Silently dropping a third of real
                                      findings is worse than labelling them.

TAP TARGETS ARE NOT PUBLISHED. 16 findings on one brand, two footer links, no collapse built, and
never precision-tested. `DEFAULT_RULES` includes `target-size` because contrast and tap-target come
from one axe run, but its findings are discarded here.

EVERYTHING IS A SAMPLE. 30 pages per brand cannot support a per-page count, a site total, or
"no problems here" — the report says so next to the findings.

MHD is excluded: its origin 503s under concurrency and one render costs ~90 origin requests
against 1 for a text fetch.

Usage:
    python3 scripts/render_pass.py gl --pages 30
    python3 scripts/render_pass.py --all
    python3 scripts/render_pass.py gl --dry-run
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from playwright.sync_api import sync_playwright

    from render.browser import ensure_chromium
    from render.a11y import collapse_contrast, coverage_finding, run_axe, to_findings
    from render.images import find_broken, scroll_to_load_everything
    from render.images import to_findings as images_to_findings
    from render.safety import SafetyLedger, install, prove_attached
except ModuleNotFoundError as exc:  # pragma: no cover - container-only path
    raise SystemExit(
        f"{exc.name} is not available. HOST-ONLY tool: the container ships neither render/ nor "
        f"playwright. Run it on the host, from the repo root. (A MISSING CHROMIUM is no longer "
        f"this error — render/browser.py installs it before the pass starts.)") from exc

from sqlalchemy import select                                          # noqa: E402
from sqlalchemy import text as sql                                     # noqa: E402

from auditor.config import load_brand                                  # noqa: E402
from server.db import SessionLocal                                     # noqa: E402
from server.models import Brand, Finding, Run, fp_hash                 # noqa: E402

VIEWPORT = {"width": 390, "height": 844}      # the viewport contrast was measured and classified at
DELAY_S = 1.0                                  # politeness: these are live client pages
PAGES_DEFAULT = 30
SKIP = {"MHD"}

# Classes this pass publishes. `target-size` is produced by the same axe run and deliberately
# discarded — see the module docstring.
PUBLISH = {"color-contrast", "contrast_coverage", "broken_image"}


def _classifier():
    """The pixel check, imported from the tool that measured it — one implementation, not two."""
    import importlib.util as ilu
    spec = ilu.spec_from_file_location("_cc", ROOT / "scripts" / "contrast_classify.py")
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sample_urls(brand: str, n: int) -> list[str]:
    """Reuse the accessibility pass's template-spread sampler — one way to pick pages."""
    import importlib.util as ilu
    spec = ilu.spec_from_file_location("_ap", ROOT / "scripts" / "accessibility_pass.py")
    ap = ilu.module_from_spec(spec)
    spec.loader.exec_module(ap)
    return ap.sample_urls(brand.lower(), n)


def gate_contrast(browser, cfg, cc, pairs: list, findings_by_pair: dict) -> tuple[list, int, int]:
    """Verify each colour pair against the pixels. Returns (kept, dropped, unclear)."""
    kept, dropped, unclear = [], 0, 0
    for f in pairs:
        d = f.details or {}
        key = (d.get("fgColor"), d.get("bgColor"), str(d.get("expectedContrastRatio")))
        member = findings_by_pair.get(key)
        if not member:
            f.details = {**d, "pixel_check": "unclear",
                         "pixel_note": "no example element was recorded to photograph"}
            kept.append(f); unclear += 1
            continue
        sel = (member.details or {}).get("selector")
        verdict, note, px = "unclear", "", None
        page = browser.new_page(viewport=VIEWPORT)
        try:
            led = SafetyLedger()
            install(page, cfg.base_url, led)
            prove_attached(page, led)
            page.goto(member.url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(900)
            loc = page.locator(sel).first
            loc.scroll_into_view_if_needed(timeout=4000)
            got = cc._px_ratio(loc.screenshot(timeout=6000))
            if got:
                px, pbg, pfg = got
                axe_r = float(d.get("contrastRatio") or 0)
                if abs(px - axe_r) <= cc.TOLERANCE:
                    verdict, note = "verified", f"pixels measure {px:.2f}:1, matching the report"
                elif px > axe_r + cc.TOLERANCE:
                    verdict = "refuted"
                    note = (f"pixels measure {px:.2f}:1 on {pbg}, not the {d.get('bgColor')} this "
                            f"was reported against")
                else:
                    verdict, note = "verified", f"pixels measure {px:.2f}:1, worse than reported"
            else:
                note = "element too small or single-coloured to read pixels from"
        except Exception as e:                                          # noqa: BLE001
            note = f"could not photograph the element ({type(e).__name__})"
        finally:
            page.close()
        time.sleep(DELAY_S)

        if verdict == "refuted":
            # DROPPED, not softened. This is the entire measured false-positive class.
            dropped += 1
            continue
        if verdict == "unclear":
            unclear += 1
        f.details = {**d, "pixel_check": verdict, "pixel_note": note,
                     "pixel_ratio": round(px, 2) if px else None}
        kept.append(f)
    return kept, dropped, unclear


def run_brand(browser, cc, brand: str, pages: int) -> list:
    cfg = load_brand(brand)
    urls = _sample_urls(brand, pages)
    if not urls:
        print(f"  {brand.upper():5s} no URLs to sample — skipped")
        return []

    raw_contrast, images, failed = [], [], 0
    assessed = withheld = 0
    # Why axe could not judge an element — the coverage caveat names the dominant one, so the
    # client is told "we could not see most of this" rather than being handed a small number that
    # reads as "we looked and found little".
    reasons: dict[str, int] = {}
    for i, url in enumerate(urls, 1):
        led = SafetyLedger()
        page = browser.new_page(viewport=VIEWPORT)
        try:
            install(page, cfg.base_url, led)
            prove_attached(page, led)       # the canary, every page, no exceptions
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(900)
            axe = run_axe(page)
            raw_contrast.extend(to_findings(axe, url, viewport="mobile"))
            assessed += sum(len(v.get("nodes") or []) for v in axe.get("violations", []))
            for group in axe.get("incomplete", []):
                for n in group.get("nodes") or []:
                    withheld += 1
                    r = n.get("reason", "unknown")
                    reasons[r] = reasons.get(r, 0) + 1
            scroll_to_load_everything(page)
            images.extend(images_to_findings(
                find_broken(page, blocked_urls=led.blocked_urls), url, viewport="mobile"))
        except Exception as e:                                          # noqa: BLE001
            failed += 1
            print(f"    [{i}/{len(urls)}] page did not load: {url[:78]} — {type(e).__name__}")
        finally:
            page.close()
        time.sleep(DELAY_S)

    # One member per colour decision, kept so the gate has a real element to photograph.
    by_pair = {}
    for f in raw_contrast:
        d = f.details or {}
        if d.get("class") != "color-contrast":
            continue
        by_pair.setdefault((d.get("fgColor"), d.get("bgColor"),
                            str(d.get("expectedContrastRatio"))), f)

    pairs = [f for f in collapse_contrast(raw_contrast)
             if (f.details or {}).get("class") == "color-contrast"]
    kept, dropped, unclear = gate_contrast(browser, cfg, cc, pairs, by_pair)

    out = kept + images
    cov = coverage_finding(brand.upper(), assessed=assessed, withheld=withheld, reasons=reasons)
    if cov:
        out.append(cov)
    print(f"  {brand.upper():5s} {len(urls) - failed}/{len(urls)} pages · "
          f"{len(pairs)} colour pairs -> {len(kept)} kept ({unclear} unclear), "
          f"{dropped} DROPPED by the pixel gate · {len(images)} broken image(s)"
          + (f" · {failed} page(s) failed to load" if failed else ""))
    return [f for f in out if (f.details or {}).get("class") in PUBLISH]


def store(brand_code: str, findings: list) -> int:
    """Attach to the brand's latest ok run — never invent a run row, which would put a second
    'latest run' in front of every report and diff."""
    with SessionLocal() as s:
        brand = s.scalar(select(Brand).where(Brand.code == brand_code.upper()))
        run = s.scalar(select(Run).where(Run.brand_id == brand.id, Run.status == "ok")
                       .order_by(Run.started_at.desc()).limit(1))
        if run is None:
            print("    no completed run to attach to"); return 0
        existing = {r[0] for r in s.execute(
            sql("select fingerprint from findings where run_id=:r"), {"r": run.id})}
        rows = []
        for f in findings:
            if f.fingerprint in existing:
                continue
            existing.add(f.fingerprint)
            rows.append(Finding(
                brand_id=brand.id, run_id=run.id,
                fingerprint=f.fingerprint, fingerprint_hash=fp_hash(f.fingerprint),
                url=f.url, check=f.check,
                severity=str(getattr(f.severity, "value", f.severity)),
                issue=f.issue, location=f.location, snippet=f.snippet, suggestion=f.suggestion,
                details=f.details or {}, status="new",
                page_count=int((f.details or {}).get("page_count") or 1),
                sources=(f.details or {}).get("sources")))
        s.bulk_save_objects(rows)
        s.commit()
        print(f"    stored {len(rows)} finding(s) on run {run.id}")
        return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("brands", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--pages", type=int, default=PAGES_DEFAULT)
    ap.add_argument("--dry-run", action="store_true", help="measure and gate, store nothing")
    args = ap.parse_args()

    if args.all:
        with SessionLocal() as s:
            codes = [b.code for b in s.scalars(select(Brand).order_by(Brand.code)).all()]
    else:
        codes = [c.upper() for c in args.brands]
    codes = [c for c in codes if c not in SKIP]
    if not codes:
        raise SystemExit("name at least one brand, or pass --all (MHD is always excluded)")

    cc = _classifier()
    total = 0
    ensure_chromium()          # never reach launch() without it (render/browser.py)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for code in codes:
                found = run_brand(browser, cc, code, args.pages)
                if args.dry_run:
                    print(f"    dry run — {len(found)} finding(s), nothing written")
                else:
                    total += store(code, found)
        finally:
            browser.close()
    print(f"\n  {total} finding(s) published across {len(codes)} brand(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
