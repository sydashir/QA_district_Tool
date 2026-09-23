"""Per-brand volume + evidence for the three rendered checks, against the 80% precision bar.

Renders a sample of real pages per brand with the network guard proven attached, runs the axe
contrast and tap-target rules and the broken-image probe, and writes one JSON row per finding with
the evidence needed to hand-classify it (measured contrast ratio, element size, image URL).

DELIBERATELY EXCLUDES MHD. Its origin 503s under concurrency — max_concurrency is a locked ceiling
of 2 — and rendering costs far more requests per page than a text fetch. Nothing here is worth
degrading a client's site for.

Usage:  python3 scripts/render_measure.py gl cad          # one or more brand codes
        python3 scripts/render_measure.py --all
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# HOST-ONLY TOOL. `scripts/` is COPYed into the image but `render/` deliberately is not (keeping it
# out of `checks_version` is the whole reason it is a separate package — CLAUDE.md), and playwright
# is in neither requirements file. So inside a container these imports raise ModuleNotFoundError
# with no hint as to why a script that is right there refuses to start. Say it plainly instead.
try:
    from playwright.sync_api import sync_playwright

    from render.browser import ensure_chromium
    from render.a11y import run_axe, to_findings
    from render.images import find_broken, scroll_to_load_everything
    from render.safety import SafetyLedger, install, prove_attached
    from render.shots import ShotTally, attach_shots
except ModuleNotFoundError as exc:  # pragma: no cover - container-only path
    raise SystemExit(
        f"{exc.name} is not available, so this measurement cannot run here.\n"
        f"scripts/render_measure.py is a HOST-ONLY tool: the container image ships neither the "
        f"render/ package (kept out of checks_version on purpose) nor playwright. Run it on the "
        f"host, from the repo root:  python3 scripts/render_measure.py <brand>") from exc

from auditor.config import load_brand

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "_render_measure"
BRANDS = ["gl", "rr", "cad", "coc", "ah", "ar", "tdrc", "dbh"]     # MHD excluded on purpose
PAGES_PER_BRAND = int(os.getenv("RENDER_SAMPLE", "8"))
VIEWPORT = {"width": 390, "height": 844}          # mobile: where tap targets actually matter


def _family(url: str) -> str:
    """The page's template family, approximated by its first path segment.

    Crude on purpose — it needs no per-brand configuration and it captures the thing that actually
    matters: sites here are built from a handful of templates, and one template can be 92% of the
    URLs. DBH is 530 `/location-served/*` against 46 everything-else.
    """
    from urllib.parse import urlparse
    segs = [x for x in urlparse(url).path.split("/") if x]
    return segs[0] if segs else "(home)"


def sample_urls(brand: str, n: int) -> list[str]:
    """Real audited URLs from the resume cache, STRATIFIED across template families.

    A uniform random sample of a site that is 92% one template lands 92% in that template — which
    is what happened to DBH: eight of eight pages were `/location-served/*`, and the resulting
    "2.1% of contrast assessable" described one template while reading as a statement about the
    site. Round-robin across families instead, so a minority template that is 8% of the URLs still
    appears. Same lesson as first-N sampling: a sample that is not spread describes the sample.
    """
    cache = ROOT / "cache" / brand / "resume.done.jsonl"
    if not cache.exists():
        return []
    urls = []
    with open(cache) as fh:
        for i, line in enumerate(fh):
            if i > 4000:
                break
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("status") == 200 and row.get("url"):
                urls.append(row["url"])
    rng = random.Random(31)
    rng.shuffle(urls)

    buckets: dict[str, list[str]] = {}
    for u in urls:
        buckets.setdefault(_family(u), []).append(u)
    order = sorted(buckets, key=lambda k: -len(buckets[k]))
    total = sum(len(buckets[k]) for k in order)

    # PROPORTIONAL, with a floor of one for the runner-up. Equal-weight round-robin was the first
    # attempt and it is the opposite mistake: it gave DBH's dominant template — 92% of the site —
    # one page out of eight, so the sample would have described a 1% template as loudly as the one
    # almost every visitor sees. Weight by real share, then guarantee the second-largest family at
    # least one page so a minority template cannot vanish entirely.
    quota = {k: max(0, round(n * len(buckets[k]) / total)) for k in order}
    if len(order) > 1 and quota.get(order[1], 0) == 0:
        quota[order[1]] = 1
        quota[order[0]] = max(1, quota[order[0]] - 1)

    out: list[str] = []
    for k in order:
        out.extend(buckets[k][-quota.get(k, 0):] if quota.get(k) else [])
        del buckets[k][len(buckets[k]) - quota.get(k, 0):]
    # top up from the largest family if rounding left us short
    i = 0
    while len(out) < n and i < len(order):
        k = order[i]
        if buckets[k]:
            out.append(buckets[k].pop())
        else:
            i += 1
    return out[:n]


def measure_brand(browser, brand: str, tally=None) -> list[dict]:
    cfg = load_brand(brand)
    rows: list[dict] = []
    for url in sample_urls(brand, PAGES_PER_BRAND):
        led = SafetyLedger()
        page = browser.new_page(viewport=VIEWPORT)
        try:
            install(page, cfg.base_url, led)
            prove_attached(page, led)
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            scroll = scroll_to_load_everything(page, settle_ms=1200)
            axe = run_axe(page)
            broken = find_broken(page, blocked_urls=led.blocked_urls)
        except Exception as e:
            rows.append({"brand": brand, "url": url, "error": f"{type(e).__name__}: {e}"[:160]})
            page.close()
            continue

        # Shots are taken from the SAME open page, after axe has finished — no extra page load
        # (measured: 40-250ms per shot against 10.1s for a fresh render), and no outline present
        # while anything is being measured. attach_shots de-duplicates by colour pair, so a page
        # with 57 contrast nodes yields a handful of images rather than 57 of the same colour.
        findings = to_findings(axe, url, viewport="mobile")
        shots_taken = attach_shots(page, findings, tally=tally)
        by_selector = {(f.details or {}).get("selector"): (f.details or {}).get("shot")
                       for f in findings if (f.details or {}).get("shot")}

        for v in axe["violations"]:
            for node in v["nodes"]:
                # `run_axe` flattens axe's `n.any[0].data` onto the node itself — read it there.
                data = node.get("data") or {}
                rows.append({
                    "brand": brand, "url": url, "rule": v["id"],
                    "selector": (node.get("target") or ["?"])[0],
                    "html": (node.get("html") or "")[:200],
                    "ratio": data.get("contrastRatio"),
                    "fg": data.get("fgColor"), "bg": data.get("bgColor"),
                    "font": data.get("fontSize"), "weight": data.get("fontWeight"),
                    "minSize": data.get("minSize"),
                    "message": (node.get("message") or "")[:200],
                    "expected": data.get("expectedContrastRatio"),
                    "shot_bytes": len(by_selector.get((node.get("target") or ["?"])[0]) or ""),
                })
        for b in broken:
            if b.get("src"):
                rows.append({"brand": brand, "url": url, "rule": "broken-image",
                             "src": b["src"], "html": b["html"][:200]})
        # Store WHY axe could not decide, not just how many. "331 withheld" is unactionable;
        # "331 withheld, all bgImage" says the site paints text over pictures and no tool can judge
        # it from computed styles alone.
        reasons: dict[str, int] = {}
        for v in axe["incomplete"]:
            for n in v["nodes"]:
                key = f"{v['id']}:{n.get('reason', 'unknown')}"
                reasons[key] = reasons.get(key, 0) + 1
        rows.append({"brand": brand, "url": url, "rule": "_page",
                     "shots_taken": shots_taken,
                     "incomplete": {v["id"]: len(v["nodes"]) for v in axe["incomplete"]},
                     "incomplete_reasons": reasons,
                     "reached_bottom": scroll["reached_bottom"],
                     "blocked": led.blocked, "allowed": led.allowed})
        page.close()
        time.sleep(0.6)                            # polite: these are live client sites
    return rows


def main(brands: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ensure_chromium()          # never reach launch() without it (render/browser.py)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for b in brands:
            tally = ShotTally()
            rows = measure_brand(browser, b, tally=tally)
            (OUT / f"{b}.json").write_text(json.dumps(rows, indent=1))
            pages = sum(1 for r in rows if r.get("rule") == "_page")
            errs = sum(1 for r in rows if r.get("error"))
            counts: dict[str, int] = {}
            for r in rows:
                if r.get("rule") and r["rule"] != "_page":
                    counts[r["rule"]] = counts.get(r["rule"], 0) + 1
            shots = sum(r.get("shots_taken") or 0 for r in rows if r.get("rule") == "_page")
            print(f"  {b.upper():<5} pages={pages:<3} errors={errs:<3} "
                  f"shots={shots:<4} "
                  f"contrast={counts.get('color-contrast', 0):<5} "
                  f"target={counts.get('target-size', 0):<4} "
                  f"broken_img={counts.get('broken-image', 0)}", flush=True)
            print(f"        locator: {tally.summary()}", flush=True)
        browser.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    main(BRANDS if (not args or args[0] == "--all") else args)
