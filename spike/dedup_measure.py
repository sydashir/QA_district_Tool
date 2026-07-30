"""Measure how much of each brand's page text is repeated boilerplate.

Phase-2 cost hinges on this: if most text is templated, AI-checking every page re-reads the same
nav/footer/CTA blocks. We check each DISTINCT block once and fan the verdict to every page carrying
it. GL measured 62% boilerplate on 60 pages; this re-measures across brands so the cost model isn't
built on one site. Read-only, small samples, polite (per-brand crawl settings).

Usage: python3 -m spike.dedup_measure gl rr coc cad ar mhd
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import sys
from collections import Counter

from auditor import crawl as C
from auditor.config import load_brand
from auditor.parse import parse_html

SAMPLES = {"mhd": 12}          # throttling host -> smaller sample
DEFAULT_N = 25
_WS = re.compile(r"\s+")


def blocks(text: str) -> list[str]:
    """Split visible text into comparable blocks. parse_html's visible_text is SPACE-NORMALIZED
    (essentially no newlines), so paragraph splitting yields one block per page and measures 0%
    boilerplate — a measurement artifact. Split on sentence boundaries instead; runs of nav/footer
    text without terminal punctuation stay as one long block, which is exactly what we want (they
    repeat verbatim across pages and should register as boilerplate)."""
    out = []
    for raw in re.split(r"(?<=[.!?])\s+", text):
        b = _WS.sub(" ", raw).strip()
        if len(b) >= 40:                      # ignore scraps; they're noise either way
            out.append(b)
    return out


async def measure(brand: str) -> None:
    cfg = load_brand(brand)
    n = SAMPLES.get(brand, DEFAULT_N)
    async with C.make_client(cfg.crawl) as client:
        urls, blocked, _c, _f = await C.enumerate_sitemap(
            client, cfg.sitemap_url, max_retries=3)
        urls = C._apply_exclude(urls, cfg.crawl.exclude)
        if not urls:
            print(f"{brand.upper():5} no sitemap urls"); return
        step = max(1, len(urls) // n)
        sample = urls[::step][:n]             # spread across the whole site, not the head
        pages = []
        for u in sample:
            st, _f2, html, err, _h = await C._request(client, u, max_retries=2)
            if st == 200 and html:
                pages.append(parse_html(html, page_url=u, base_url=u).visible_text)
            await asyncio.sleep(cfg.crawl.delay_seconds)

    if not pages:
        print(f"{brand.upper():5} no pages fetched"); return
    per_page = [blocks(t) for t in pages]
    counts: Counter = Counter()
    for bl in per_page:
        for b in set(bl):                     # per-page presence, not repetition within a page
            counts[hashlib.sha1(b.encode()).hexdigest()] += 1
    total_chars = sum(len(b) for bl in per_page for b in bl)
    seen: set[str] = set()
    unique_chars = 0
    boiler_chars = 0
    for bl in per_page:
        for b in bl:
            h = hashlib.sha1(b.encode()).hexdigest()
            if counts[h] >= max(2, 0.3 * len(pages)):   # on >=30% of pages -> boilerplate
                boiler_chars += len(b)
            if h not in seen:
                seen.add(h)
                unique_chars += len(b)
    pct_boiler = 100 * boiler_chars / total_chars if total_chars else 0
    cut = 100 * (1 - unique_chars / total_chars) if total_chars else 0
    uniq_per_page = unique_chars / len(pages)
    print(f"{brand.upper():5} pages={len(pages):>3} total={total_chars:>8,}ch  "
          f"boilerplate={pct_boiler:5.1f}%  dedup_saves={cut:5.1f}%  "
          f"unique_after_dedup={unique_chars:>8,}ch  (~{unique_chars/4/len(pages):,.0f} tok/page)")


async def main(brands: list[str]) -> None:
    for b in brands:
        try:
            await measure(b)
        except Exception as e:
            print(f"{b.upper():5} ERROR {type(e).__name__}: {e}")
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["gl", "rr", "coc", "cad", "ar", "mhd"]))
