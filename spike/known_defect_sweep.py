"""Corpus-wide sweep for client-confirmed misspellings — SLUGS ARE FREE.

Every brand's sitemap gives us every public URL without fetching a single page, so slug
misspellings can be checked at 100% coverage at zero crawl cost. Body text needs a fetch, so that
part is sampled. Read-only.

Usage: python3 -m spike.known_defect_sweep
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter, defaultdict

from auditor import crawl as C
from auditor.checks import misspelling, scope
from auditor.config import load_brand
from auditor.parse import ParsedPage, parse_html

BRANDS = ["gl", "rr", "coc", "dbh", "ah", "ar", "tdrc", "cad", "mhd"]
BODY_SAMPLE = 15


async def sweep(brand: str) -> None:
    cfg = load_brand(brand)
    async with C.make_client(cfg.crawl) as client:
        urls, _b, _c, failed = await C.enumerate_sitemap(
            client, cfg.sitemap_url, max_retries=3)
        urls = C._apply_exclude(urls, cfg.crawl.exclude)
        if not urls:
            print(f"{brand.upper():5} no sitemap urls (partial={bool(failed)})", flush=True)
            return

        # --- SLUGS: free, 100% coverage ---
        slug_hits: Counter = Counter()
        slug_examples: dict[str, str] = {}
        for u in urls:
            for f in misspelling.run(ParsedPage(url=u), cfg):
                if f.details["class"] == "slug":
                    w = f.details["wrong"].lower()
                    slug_hits[w] += 1
                    slug_examples.setdefault(w, u)

        # --- BODY + SCOPE: sampled ---
        step = max(1, len(urls) // BODY_SAMPLE)
        body_hits: Counter = Counter()
        scope_hits = 0
        scope_ex = ""
        pages = 0
        for u in urls[::step][:BODY_SAMPLE]:
            st, _f, html, _e, _h = await C._request(client, u, max_retries=2)
            if st != 200 or not html:
                continue
            pages += 1
            p = parse_html(html, page_url=u, base_url=u)
            for f in misspelling.run(p, cfg):
                if f.details["class"] == "body":
                    body_hits[f.details["wrong"].lower()] += 1
            sf = scope.run(p, cfg)
            if sf:
                scope_hits += 1
                scope_ex = scope_ex or sf[0].snippet[:70]
            await asyncio.sleep(cfg.crawl.delay_seconds)

    print(f"{brand.upper():5} urls={len(urls):>6}  SLUG hits={sum(slug_hits.values()):>4} "
          f"{dict(slug_hits) or '{}'}", flush=True)
    for w, u in slug_examples.items():
        print(f"        e.g. [{w}] {u}", flush=True)
    print(f"        body sample={pages:>3} hits={dict(body_hits) or '{}'}  "
          f"county-for-country pages={scope_hits}"
          + (f"  e.g. {scope_ex!r}" if scope_ex else ""), flush=True)


async def main(brands: list[str]) -> None:
    for b in brands:
        try:
            await sweep(b)
        except Exception as e:
            print(f"{b.upper():5} ERROR {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or BRANDS))
