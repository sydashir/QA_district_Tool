"""Read-only union-delta probe: for each already-crawled brand, enumerate the sitemap and
reconcile against WP-REST (URL lists only — NO page fetch), and report how many live pages the
union would ADD over the sitemap. Answers Syed's "which existing reports stand vs need re-run":
rest_only_added ≈ 0 -> sitemap healthy, coverage stable; large -> broken sitemap, re-run needed.

Usage: python3 -m spike.union_delta gl ar ah tdrc cad
"""
from __future__ import annotations

import asyncio
import sys

from auditor import crawl as C
from auditor.checks import enumeration
from auditor.config import load_brand
from auditor.report import canonical_url


async def probe(brand: str) -> dict:
    cfg = load_brand(brand)
    async with C.make_client(cfg.crawl) as client:
        sitemap_urls, blocked, _child, _failed = await C.enumerate_sitemap(
            client, cfg.sitemap_url, max_retries=cfg.crawl.max_retries)
        sitemap_urls = C._apply_exclude(sitemap_urls, cfg.crawl.exclude)
        recon = None
        if cfg.wp_rest and cfg.wp_rest.enabled:
            recon = await enumeration.reconcile(client, cfg, sitemap_urls)
    sitemap_set = {canonical_url(u) for u in sitemap_urls}
    rest_only = 0
    if recon:
        rest_only = sum(1 for u in recon["rest_urls"] if canonical_url(u) not in sitemap_set)
    return {
        "brand": cfg.brand, "sitemap": len(sitemap_urls), "blocked": blocked,
        "wp_rest_pages": recon["wp_rest_pages_total"] if recon else None,
        "rest_only_added": rest_only if recon else None,
        "union": len(sitemap_set) + rest_only if recon else len(sitemap_set),
    }


async def main(brands: list[str]) -> None:
    for b in brands:
        try:
            r = await probe(b)
            pct = (100.0 * r["rest_only_added"] / r["union"]) if r["union"] and r["rest_only_added"] is not None else 0.0
            print(f"{r['brand']:>5}: sitemap={r['sitemap']:>5}  wp_rest={r['wp_rest_pages']}  "
                  f"rest_only_added={r['rest_only_added']}  union={r['union']}  (+{pct:.1f}%)  blocked={r['blocked']}")
        except Exception as e:  # a brand whose REST is unreachable shouldn't sink the sweep
            print(f"{b:>5}: ERROR {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["gl", "ar", "ah", "tdrc", "cad"]))
