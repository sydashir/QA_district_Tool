"""Run the markup-only accessibility rules and store the findings.

WHY THIS SCRIPT EXISTS: `render/markup.py` has been built, tested and measured since 2026-08-25,
and **not one accessibility finding had ever reached the database** — zero, across 325,031 rows.
`scripts/client_report.py` even carries report sections keyed to `accessibility:link-name` and
`accessibility:label`, waiting for findings that never arrived. The client raised the underlying
defect directly (ClickUp `86baawd2a`: pages had zero `<nav>` elements, so assistive technology had
no landmark at all), and the tool could answer it and did not.

It was never wired in because it does not fit `_PAGE_CHECKS`. Those checks are
`run(parsed, config) -> [Finding]`, per page, pure. This one needs a browser, and it collapses
ACROSS the brand: `link-name` alone produced 2,981 raw violations on 264 pages — about eleven per
page — which is one shared template, not 2,981 defects. So it is a separate pass, over a sample,
collapsed by the shape of the thing at fault.

**This is markup analysis, not rendering.** The HTML is fetched by `httpx` exactly as the text
auditor fetches it, then analysed in a browser with EVERY network request blocked and the block
proven by a canary. The client's servers never see a browser and no tracker can fire — the
distinction the client notice draws, and `render/markup.py`'s docstring explains at length.

Nothing here is hashed: `render/`, `scripts/` and `server/` are all outside `checks_version`, so
this costs no resume-cache invalidation.

Usage:  python3 scripts/accessibility_pass.py gl            # one brand
        python3 scripts/accessibility_pass.py --all -n 40   # every brand, 40 pages each
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auditor.checks.schema import off_brand               # noqa: E402
from auditor.report import canonical_url                  # noqa: E402
from auditor.config import load_brand                     # noqa: E402
from render.markup import audit_html                      # noqa: E402
from server.db import SessionLocal                        # noqa: E402
from server.models import Brand, Finding, Run             # noqa: E402
from server.models import fp_hash                         # noqa: E402
from sqlalchemy import select, text as sql                # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BRANDS = ["gl", "rr", "cad", "coc", "ah", "ar", "tdrc", "dbh", "mhd"]
UA = {"User-Agent": "Mozilla/5.0 (compatible; district-site-auditor/1.0)"}


def _family(url: str) -> str:
    segs = [s for s in urlparse(url).path.split("/") if s]
    return segs[0] if segs else "(home)"


def sample_urls(brand: str, n: int) -> list[str]:
    """Audited URLs from the resume cache, spread across template families.

    Spread matters more here than anywhere else: these findings are collapsed per TEMPLATE, so a
    sample concentrated in one template reports that template's problems as the site's.
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
    out: list[str] = []
    while len(out) < n and any(buckets[k] for k in order):
        for k in order:
            if buckets[k] and len(out) < n:
                out.append(buckets[k].pop())
    return out


def fetch_pages(urls: list[str], base_url: str) -> tuple[list[tuple[str, str]], int]:
    """Plain HTTP, one page at a time, no JavaScript — the same surface the text auditor uses.

    DROPS a page that redirects off the brand's own domain, and this is not hypothetical: the first
    run of this script analysed TDRC and reported GL's phone number and gratitudelodge.com links as
    TDRC's defects. TDRC's `/review-us/*` URLs are deliberate redirects to sister brands, `httpx`
    follows them, and the HTML that comes back belongs to the other brand.

    `auditor/audit.py` already guards this (`_off_brand_projection`); this script is new code and
    did not inherit it. Same trap, new place — which is exactly what ARCHITECTURE.md D15 says about
    a property of the design rather than an incident.
    """
    out: list[tuple[str, str]] = []
    off_brand_dropped = 0
    with httpx.Client(follow_redirects=True, timeout=30, headers=UA) as c:
        for u in urls:
            try:
                r = c.get(u)
            except Exception as e:
                print(f"    skip {u[:70]} ({type(e).__name__})")
                continue
            if r.status_code != 200 or not r.text:
                continue
            if off_brand(str(r.url), base_url):
                off_brand_dropped += 1
                continue
            # canonical_url, NOT the fetched URL. Page identity everywhere else in this project
            # is the requested URL with the trailing slash stripped (auditor/report.py), and these
            # sites serve the slashed form — so passing r.url stored 188 findings under a second
            # identity for pages that already existed. Two join keys for one page is exactly what
            # breaks a traffic join, and it silently halves the match rate.
            out.append((canonical_url(str(r.url)), r.text))
    return out, off_brand_dropped


def store(brand_code: str, findings: list) -> int:
    """Attach findings to the brand's latest ok run, skipping any fingerprint already there.

    Deliberately NOT a new run: these describe the same pages the latest run audited, and inventing
    a run row would put a second 'latest run' in front of every report and diff.
    """
    with SessionLocal() as s:
        brand = s.scalar(select(Brand).where(Brand.code == brand_code.upper()))
        run = s.scalar(select(Run).where(Run.brand_id == brand.id, Run.status == "ok")
                       .order_by(Run.started_at.desc()).limit(1))
        if run is None:
            print("    no completed run to attach to"); return 0
        existing = {r[0] for r in s.execute(sql(
            "select fingerprint from findings where run_id=:r"), {"r": run.id})}
        rows = []
        for f in findings:
            if f.fingerprint in existing:
                continue
            existing.add(f.fingerprint)
            rows.append(Finding(
                brand_id=brand.id, run_id=run.id,
                fingerprint=f.fingerprint, fingerprint_hash=fp_hash(f.fingerprint),
                url=f.url, check=f.check, severity=str(getattr(f.severity, "value", f.severity)),
                issue=f.issue, location=f.location, snippet=f.snippet, suggestion=f.suggestion,
                details=f.details or {}, status="new",
                page_count=int((f.details or {}).get("page_count") or 1),
                sources=(f.details or {}).get("sources")))
        s.bulk_save_objects(rows)
        s.commit()
        print(f"    stored {len(rows)} finding(s) on run {run.id}")
        return len(rows)


def status(brands: list[str]) -> int:
    """Which brands' accessibility findings belong to a crawl that is no longer the latest.

    This pass is not part of the crawl — it needs a browser — so every new audit silently leaves it
    behind. Without a way to ASK, the drift is discovered by someone reading a report months later
    and wondering why a section disappeared. Returns the number of stale brands, so it can gate a
    script or a CI step.
    """
    stale = 0
    print(f"  {'brand':<6}{'latest run':>11}{'acc findings':>14}   state")
    with SessionLocal() as s:
        for b in brands:
            brand = s.scalar(select(Brand).where(Brand.code == b.upper()))
            if brand is None:
                continue
            latest = s.scalar(select(Run).where(Run.brand_id == brand.id, Run.status == "ok")
                              .order_by(Run.started_at.desc()).limit(1))
            if latest is None:
                print(f"  {b.upper():<6}{'-':>11}{'-':>14}   no completed run")
                continue
            n_here = s.execute(sql("""select count(*) from findings
                                      where run_id=:r and "check"='accessibility'"""),
                               {"r": latest.id}).scalar()
            if n_here:
                print(f"  {b.upper():<6}{latest.id:>11}{n_here:>14}   current")
                continue
            last = s.execute(sql("""select f.run_id, count(*) from findings f
                                    where f.brand_id=:b and f."check"='accessibility'
                                    group by 1 order by 1 desc limit 1"""),
                             {"b": brand.id}).first()
            stale += 1
            where = f"last on run {last[0]} ({last[1]} findings)" if last else "never run"
            print(f"  {b.upper():<6}{latest.id:>11}{0:>14}   STALE — {where}")
    if stale:
        print(f"\n  {stale} brand(s) need `python3 scripts/accessibility_pass.py --all`.")
        print("  Their reports will say the checks were not run, rather than implying they passed.")
    else:
        print("\n  every brand's accessibility findings are on its latest run")
    return stale


def main(brands: list[str], n: int) -> None:
    total = 0
    for b in brands:
        cfg = load_brand(b)
        urls = sample_urls(b, n)
        if not urls:
            print(f"  {b.upper():<5} no cached URLs — run an audit first"); continue
        print(f"  {b.upper():<5} fetching {len(urls)} page(s)...", flush=True)
        pages, off = fetch_pages(urls, cfg.base_url)
        if off:
            print(f"    dropped {off} page(s) that redirect off {b.upper()}'s own domain")
        if not pages:
            print("    nothing fetched"); continue
        findings, ledger = audit_html(pages, cfg.base_url)
        print(f"    {len(pages)} page(s) analysed, {len(findings)} collapsed finding(s); "
              f"guard blocked {ledger.blocked} request(s)")
        total += store(b, findings)
    print(f"\n  TOTAL stored: {total}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("brands", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("-n", type=int, default=30, help="pages sampled per brand")
    ap.add_argument("--status", action="store_true",
                    help="report which brands' accessibility findings are on a superseded run")
    a = ap.parse_args()
    chosen = BRANDS if (a.all or not a.brands) else a.brands
    if a.status:
        raise SystemExit(1 if status(chosen) else 0)
    main(chosen, a.n)
