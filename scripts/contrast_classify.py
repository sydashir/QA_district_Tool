#!/usr/bin/env python3
"""Classify contrast findings against the PIXELS, to measure precision before publishing them.

WHY PIXELS. This project killed two spelling checks on precision (D9, D10) and the register never
adjudicated contrast — SESSION_STATE still reads "precision pending". Everything claimed for
contrast so far is STRUCTURAL: axe withholds when unsure (66.7% of elements), and every one of 949
nodes' arithmetic reproduces axe's own ratio. Neither says the finding is TRUE. Recomputing axe's
ratio from axe's own colours cannot fail — it only proves the arithmetic, which is exactly the
circularity D10 turned on.

So the check has to come from somewhere axe cannot reach. axe reads colours off the DOM, and its
known failure is claiming a background that is not what the browser actually paints — a background
image it could not sample, a gradient, an overlay, opacity on an ancestor. Screenshotting the
element and reading the ACTUAL PIXELS is independent of the DOM entirely: if axe says "#7a7a7a on
#ffffff" and the pixels say the text sits on a photograph, axe was wrong and the finding is a false
positive.

WHAT IT MEASURES, precisely: for each collapsed colour-pair finding, one example element is
photographed, the two dominant colours in that crop are recovered, and the WCAG ratio of the pixels
is compared with the ratio axe reported. Agreement within `TOLERANCE` is a true positive.

WHAT IT CANNOT DECIDE, and does not pretend to: whether a designer considers the text acceptable,
and anti-aliased text whose stroke core never reaches full colour at small sizes. Those land in
`unclear` and are reported separately rather than being counted as either.

Usage:
    python3 scripts/contrast_classify.py gl --pages 30
    python3 scripts/contrast_classify.py gl cad coc --pages 30 --out reports/_contrast
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from playwright.sync_api import sync_playwright

    from render.a11y import (_contrast_ratio, _hex_to_rgb, collapse_contrast, run_axe,
                             to_findings)
    from render.browser import ensure_chromium
    from render.safety import SafetyLedger, install, prove_attached
except ModuleNotFoundError as exc:  # pragma: no cover - container-only path
    raise SystemExit(
        f"{exc.name} is not available. This is a HOST-ONLY tool: the container ships neither "
        f"render/ nor playwright. Run it on the host, from the repo root. (A MISSING CHROMIUM "
        f"is no longer this error — render/browser.py installs it before the pass starts.)") from exc

from auditor.config import load_brand                                  # noqa: E402

VIEWPORT = {"width": 390, "height": 844}      # the viewport the contrast pass measures at
DELAY_S = 1.0                                  # politeness: live client pages
PAD = 2                                        # px trimmed from the crop edge, to miss the border

# How far the pixels may sit from axe's reported ratio and still count as agreement. Anti-aliasing
# lightens thin strokes, so the pixel ratio is systematically a little LOWER than the DOM's; 0.6 is
# wide enough to absorb that without absorbing a real disagreement (a mis-read background moves the
# ratio by whole integers, not by tenths).
TOLERANCE = 0.6

# A colour must own at least this share of the crop to count as one of its two dominant colours.
# Below it we are reading anti-aliasing fringe, not a colour decision.
MIN_SHARE = 0.02

SKIP = {"MHD"}


def _px_ratio(png_bytes: bytes) -> tuple[float, str, str] | None:
    """(ratio, background hex, text hex) recovered from the crop's own pixels."""
    from PIL import Image
    import io

    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    w, h = im.size
    if w <= PAD * 2 or h <= PAD * 2:
        return None
    im = im.crop((PAD, PAD, w - PAD, h - PAD))
    px = list(im.getdata())
    if not px:
        return None
    counts = Counter(px)
    total = sum(counts.values())
    common = [(c, n) for c, n in counts.most_common(40) if n / total >= MIN_SHARE]
    if not common:
        return None

    from render.a11y import _rel_luminance
    bg = common[0][0]                                   # the most-painted colour is the background
    bg_lum = _rel_luminance(bg)
    # The text is the qualifying colour furthest from the background in luminance. Taking the most
    # EXTREME rather than the second most common matters: on a button, the border can out-count the
    # glyphs.
    fg = max((c for c, _n in common), key=lambda c: abs(_rel_luminance(c) - bg_lum))
    if fg == bg:
        return None
    return (_contrast_ratio(fg, bg),
            "#%02x%02x%02x" % bg, "#%02x%02x%02x" % fg)


def sample_urls(brand: str, n: int) -> list[str]:
    """Reuse the accessibility pass's template-spread sampler — one way to pick pages."""
    import importlib.util as ilu
    spec = ilu.spec_from_file_location("_ap", ROOT / "scripts" / "accessibility_pass.py")
    ap = ilu.module_from_spec(spec)
    spec.loader.exec_module(ap)
    return ap.sample_urls(brand.lower(), n)


def classify_brand(browser, brand: str, pages: int) -> dict:
    cfg = load_brand(brand)
    urls = sample_urls(brand, pages)
    if not urls:
        print(f"  {brand.upper():5s} no URLs to sample — skipped")
        return {"brand": brand.upper(), "rows": [], "pages": 0, "fetch_failed": 0}

    rows: list[dict] = []
    findings: list = []
    failed = 0
    for i, url in enumerate(urls, 1):
        led = SafetyLedger()
        page = browser.new_page(viewport=VIEWPORT)
        try:
            install(page, cfg.base_url, led)
            prove_attached(page, led)          # the canary, every page, no exceptions
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(900)
            findings.extend(to_findings(run_axe(page), url, viewport="mobile"))
        except Exception as e:                                          # noqa: BLE001
            failed += 1
            print(f"    [{i}/{len(urls)}] page did not load: {url[:80]} — {type(e).__name__}")
        finally:
            page.close()
        time.sleep(DELAY_S)

    # Group the RAW findings by colour decision ourselves rather than using collapse_contrast:
    # the collapsed Finding is rebuilt from scratch and does not carry `selector`, so a collapsed
    # row cannot be located on a page. Grouping here keeps one real element per pair to photograph,
    # which is what makes the pixel check possible at all. The grouping key is the same one
    # collapse_contrast uses, so the pairs are the rows a client would see.
    groups: dict[tuple, list] = {}
    for f in findings:
        d = f.details or {}
        if d.get("class") != "color-contrast":
            continue
        groups.setdefault((d.get("fgColor"), d.get("bgColor"),
                           str(d.get("expectedContrastRatio"))), []).append(f)
    pairs = []
    for (fg, bg, req), members in groups.items():
        first = members[0]
        fd = first.details or {}
        pairs.append({"fgColor": fg, "bgColor": bg, "contrastRatio": fd.get("contrastRatio"),
                      "expectedContrastRatio": req, "selector": fd.get("selector"),
                      "example": first.url, "element_count": len(members),
                      "page_count": len({m.url for m in members})})
    print(f"  {brand.upper():5s} {len(urls)} pages -> {len(findings)} raw -> {len(pairs)} colour pairs")

    # Re-open one example page per pair and photograph the element, so the pixels are independent
    # of everything axe computed.
    for d in pairs:
        ex, sel = d.get("example"), d.get("selector")
        row = {"brand": brand.upper(), "fg": d.get("fgColor"), "bg": d.get("bgColor"),
               "axe_ratio": d.get("contrastRatio"), "required": d.get("expectedContrastRatio"),
               "elements": d.get("element_count"), "pages": d.get("page_count"),
               "example": ex, "verdict": "unclear", "px_ratio": None,
               "px_bg": None, "px_fg": None, "why": ""}
        if not ex or not sel:
            row["why"] = "no example element recorded"
            rows.append(row); continue
        page = browser.new_page(viewport=VIEWPORT)
        try:
            led = SafetyLedger()
            install(page, cfg.base_url, led)
            prove_attached(page, led)
            page.goto(ex, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(900)
            loc = page.locator(sel).first
            loc.scroll_into_view_if_needed(timeout=4000)
            shot = loc.screenshot(timeout=6000)
            got = _px_ratio(shot)
            if not got:
                row["why"] = "element too small or single-coloured to read pixels from"
            else:
                px, pbg, pfg = got
                row.update(px_ratio=round(px, 2), px_bg=pbg, px_fg=pfg)
                axe_r = float(d.get("contrastRatio") or 0)
                if abs(px - axe_r) <= TOLERANCE:
                    row["verdict"] = "true"
                    row["why"] = "pixels agree with the reported ratio"
                elif px > axe_r + TOLERANCE:
                    # The painted text is MORE readable than axe thought — axe mis-read the
                    # background. This is the false positive that matters to a client.
                    row["verdict"] = "false"
                    row["why"] = (f"pixels measure {px:.2f}:1, better than the reported "
                                  f"{axe_r}:1 — the real background is not {d.get('bgColor')}")
                else:
                    row["verdict"] = "true"
                    row["why"] = (f"pixels measure {px:.2f}:1, worse than reported {axe_r}:1 — "
                                  f"still a failure")
        except Exception as e:                                          # noqa: BLE001
            row["why"] = f"could not photograph the element: {type(e).__name__}"
        finally:
            page.close()
        time.sleep(DELAY_S)
        rows.append(row)
    return {"brand": brand.upper(), "rows": rows, "pages": len(urls), "fetch_failed": failed}


def report(results: list[dict], floor: float = 0.80) -> int:
    print(f"\n  {'brand':6s}{'pairs':>7s}{'true':>6s}{'false':>7s}{'unclear':>9s}{'precision':>11s}"
          f"   verdict")
    worst_ok = True
    for r in results:
        rows = r["rows"]
        t = sum(1 for x in rows if x["verdict"] == "true")
        f_ = sum(1 for x in rows if x["verdict"] == "false")
        u = sum(1 for x in rows if x["verdict"] == "unclear")
        decided = t + f_
        if decided == 0:
            print(f"  {r['brand']:6s}{len(rows):>7d}{t:>6d}{f_:>7d}{u:>9d}{'—':>11s}   NO VERDICT")
            worst_ok = False
            continue
        p = t / decided
        ok = p >= floor
        worst_ok &= ok
        print(f"  {r['brand']:6s}{len(rows):>7d}{t:>6d}{f_:>7d}{u:>9d}{p:>10.0%}   "
              f"{'CLEARS' if ok else 'BELOW ' + str(int(floor*100)) + '%'}")
    print(f"\n  Precision is TRUE / (TRUE + FALSE). `unclear` is excluded from the ratio and "
          f"reported\n  separately — counting it either way would be inventing an answer.")
    return 0 if worst_ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("brands", nargs="+")
    ap.add_argument("--pages", type=int, default=30)
    ap.add_argument("--out", default="reports/_contrast")
    args = ap.parse_args()

    codes = [c.upper() for c in args.brands if c.upper() not in SKIP]
    if not codes:
        raise SystemExit("MHD is excluded: its origin 503s under concurrency and a render costs "
                         "far more requests than a text fetch.")

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    ensure_chromium()          # never reach launch() without it (render/browser.py)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for code in codes:
                results.append(classify_brand(browser, code, args.pages))
        finally:
            browser.close()

    (out_dir / "classification.json").write_text(json.dumps(results, indent=1))
    rc = report(results)
    print(f"  rows written to {(out_dir / 'classification.json').relative_to(ROOT)}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
