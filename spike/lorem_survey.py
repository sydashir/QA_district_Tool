"""Which brands and pages are showing lorem-ipsum placeholder text on production?

Surfaced as a side effect of mining vocabulary: Latin markers (enim, labore, magna, nostrud, tempor,
aliqua, cillum, incididunt) turned up on >= 2 brands' live pages. No check looked for it, so this
quantifies the real extent before it goes in the network rollup.

Read-only. Usage: python3 -m spike.lorem_survey [pages_per_brand] [brand ...]
"""
from __future__ import annotations

import asyncio
import sys

from auditor import crawl as C
from auditor.checks import placeholder
from auditor.config import load_brand
from auditor.css_cache import BrandCSS
from auditor.parse import parse_html

BRANDS = ["gl", "rr", "cad", "coc", "ah", "ar", "mhd", "tdrc", "dbh"]


async def survey(brand: str, n_pages: int) -> tuple[int, int, list[str]]:
    cfg = load_brand(brand)
    hits: list[str] = []
    pages = 0
    async with C.make_client(cfg.crawl) as client:
        urls, _b, _c, _f = await C.enumerate_sitemap(client, cfg.sitemap_url, max_retries=3)
        urls = C._apply_exclude(urls, cfg.crawl.exclude)
        if cfg.wp_rest and cfg.wp_rest.enabled and len(urls) < n_pages:
            rest, _a = await C.enumerate_wp_rest(client, cfg.wp_rest, max_retries=2)
            seen = set(urls)
            urls += [u for u in C._apply_exclude(rest, cfg.crawl.exclude) if u not in seen]
        if not urls:
            return 0, 0, []
        step = max(1, len(urls) // n_pages)
        css = BrandCSS()
        for u in urls[::step][:n_pages]:
            st, fin, html, _e, _h = await C._request(client, u, max_retries=2)
            if st != 200 or not html:
                continue
            pages += 1
            await css.load(client, html, fin or u, max_retries=2)
            p = parse_html(html, page_url=u, base_url=u, extra_css=css.css, css_status=css.status)
            if any(f.details.get("class") == "lorem_ipsum" for f in placeholder.run(p, cfg)):
                hits.append(u)
            await asyncio.sleep(cfg.crawl.delay_seconds)
    return pages, len(hits), hits


async def main(n_pages: int, brands: list[str]) -> None:
    total_pages = total_hits = 0
    print(f"{'brand':<6} {'sampled':>8} {'with lorem':>11}  rate")
    all_hits: dict[str, list[str]] = {}
    for b in brands:
        try:
            pages, n, hits = await survey(b, n_pages)
        except Exception as e:
            print(f"{b.upper():<6} FAILED: {type(e).__name__}: {e}")
            continue
        total_pages += pages
        total_hits += n
        all_hits[b] = hits
        rate = f"{n / pages:.0%}" if pages else "-"
        print(f"{b.upper():<6} {pages:>8} {n:>11}  {rate}")
    print(f"\nTOTAL  {total_pages:>8} {total_hits:>11}  "
          f"{total_hits / total_pages:.1%}" if total_pages else "")
    for b, hits in all_hits.items():
        if hits:
            print(f"\n{b.upper()} example pages:")
            for u in hits[:6]:
                print(f"   {u}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    asyncio.run(main(n, sys.argv[2:] or BRANDS))
