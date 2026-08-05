"""Measure the dictionary spellcheck's precision on real GL pages before it goes anywhere near
the other eight brands.

The bar Syed set: this must be QUIETER than the AI layer, not louder. The AI layer scored 4-6%
precision and was shelved. If this flags thousands of terms per brand it is noise and we have built
the same failure with a different engine.

Output is deliberately raw — every distinct flagged word with its count, its suggested correction
and one example page — because the number that matters comes from hand-classifying those, not from
anything this script asserts.

Usage: python3 -m spike.spelling_precision [brand] [pages]
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter, defaultdict

from auditor import crawl as C
from auditor.checks import spelling
from auditor.config import load_brand
from auditor.css_cache import BrandCSS
from auditor.parse import parse_html


async def measure(brand: str, n_pages: int) -> None:
    cfg = load_brand(brand)
    per_word: Counter = Counter()
    example: dict[str, tuple[str, str, str]] = {}
    by_class: Counter = Counter()
    pages = 0
    pages_with = 0

    async with C.make_client(cfg.crawl) as client:
        urls, _b, _c, _f = await C.enumerate_sitemap(client, cfg.sitemap_url, max_retries=3)
        urls = C._apply_exclude(urls, cfg.crawl.exclude)
        if cfg.wp_rest and cfg.wp_rest.enabled and len(urls) < n_pages:
            rest, _a = await C.enumerate_wp_rest(client, cfg.wp_rest, max_retries=2)
            seen = set(urls)
            urls += [u for u in C._apply_exclude(rest, cfg.crawl.exclude) if u not in seen]
        step = max(1, len(urls) // n_pages)
        sample = urls[::step][:n_pages]
        css = BrandCSS()
        for u in sample:
            st, fin, html, _e, _h = await C._request(client, u, max_retries=2)
            if st != 200 or not html:
                continue
            pages += 1
            await css.load(client, html, fin or u, max_retries=2)
            p = parse_html(html, page_url=u, base_url=u, extra_css=css.css, css_status=css.status)
            fs = spelling.run(p, cfg)
            if fs:
                pages_with += 1
            for f in fs:
                w = f.details["word"].lower()
                per_word[w] += 1
                by_class[f.details["class"]] += 1
                example.setdefault(w, (f.details.get("suggestion") or "", f.location, u))
            await asyncio.sleep(cfg.crawl.delay_seconds)

    total = sum(per_word.values())
    print(f"\n{brand.upper()}  pages={pages}  findings={total}  distinct words={len(per_word)}")
    print(f"  pages with >=1 finding: {pages_with} ({pages_with / max(1, pages):.0%})")
    print(f"  findings per page: {total / max(1, pages):.1f}")
    print(f"  by class: {dict(by_class)}")
    print("\nEVERY DISTINCT WORD (hand-classify these):")
    print(f"  {'count':>6}  {'word':<24} {'suggested':<24} where")
    for w, n in per_word.most_common():
        sug, loc, url = example[w]
        print(f"  {n:>6}  {w:<24} {sug or '-':<24} {loc}")
    print("\nexample pages for the top 10:")
    for w, _n in per_word.most_common(10):
        print(f"  {w:<24} {example[w][2]}")


if __name__ == "__main__":
    brand = sys.argv[1] if len(sys.argv) > 1 else "gl"
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    asyncio.run(measure(brand, pages))
