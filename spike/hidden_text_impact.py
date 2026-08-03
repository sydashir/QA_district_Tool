"""Measure what the hidden-text bug did to EVERY check that reads visible_text.

Until commit 7d8c759, ``visible_text`` included content the rendered page does not show
(``display:none`` spans, ``[hidden]`` elements). On GL that was 38.8% of the extracted text.

Six checks read ``visible_text``: blank, placeholder, scope, misspelling, phone, empty_slot. The
blank check is the one that matters most — it is the client's #1 stated need (catch a silently
empty ACF section) and it is a pure length threshold, so padding the text with invisible content
is exactly the way to make an empty section look populated.

This runs each check twice per page — once on the OLD polluted text, once on the corrected text —
and reports the delta per brand, plus the visible-length distribution that MIN_VISIBLE_CHARS was
tuned against.

Read-only. No reports written.

Usage: python3 -m spike.hidden_text_impact [n_pages] [brand ...]
"""
from __future__ import annotations

import asyncio
import dataclasses
import sys
from collections import Counter

from bs4 import BeautifulSoup

from auditor import crawl as C
from auditor import parse as P
from auditor.checks import blank, empty_slot, misspelling, phone, placeholder, scope
from auditor.config import load_brand

CHECKS = [("blank", blank), ("empty_slot", empty_slot), ("placeholder", placeholder),
          ("misspelling", misspelling), ("scope", scope), ("phone", phone)]
BRANDS = ["gl", "rr", "cad", "coc", "ah", "ar", "mhd", "tdrc", "dbh"]


def old_visible_text(html: str) -> str:
    """visible_text as it was BEFORE hidden content was stripped."""
    soup = BeautifulSoup(html, "lxml")
    P.strip_volatile(soup)
    return P._visible_text(soup)


async def measure(brand: str, n_pages: int) -> None:
    cfg = load_brand(brand)
    async with C.make_client(cfg.crawl) as client:
        urls, _b, _c, _f = await C.enumerate_sitemap(client, cfg.sitemap_url, max_retries=3)
        urls = C._apply_exclude(urls, cfg.crawl.exclude)
        if not urls:
            print(f"{brand.upper():5} no urls enumerated — SKIPPED")
            return
        step = max(1, len(urls) // n_pages)
        sample = urls[::step][:n_pages]

        pages = 0
        before: Counter = Counter()
        after: Counter = Counter()
        blank_before = blank_after = 0
        lens_before: list[int] = []
        lens_after: list[int] = []

        for u in sample:
            st, _f2, html, _e, _h = await C._request(client, u, max_retries=2)
            if st != 200 or not html:
                continue
            pages += 1
            new = P.parse_html(html, page_url=u, base_url=u)
            old = dataclasses.replace(new, visible_text=old_visible_text(html))
            lens_before.append(len(old.visible_text))
            lens_after.append(len(new.visible_text))
            for name, mod in CHECKS:
                try:
                    b = mod.run(old, cfg)
                    a = mod.run(new, cfg)
                except Exception as e:               # a check that throws is itself a finding
                    print(f"    {name} raised on {u}: {e}")
                    continue
                before[name] += len(b)
                after[name] += len(a)
                if name == "blank":
                    blank_before += bool(b)
                    blank_after += bool(a)
            await asyncio.sleep(cfg.crawl.delay_seconds)

    if not pages:
        print(f"{brand.upper():5} no pages fetched — SKIPPED")
        return
    tb, ta = sum(lens_before), sum(lens_after)
    print(f"{brand.upper():5} pages={pages:3}  visible chars {tb:,} -> {ta:,} "
          f"({(tb - ta) / max(1, tb):.1%} was hidden)")
    print(f"      shortest page: {min(lens_before):,} -> {min(lens_after):,} chars   "
          f"(MIN_VISIBLE_CHARS = {blank.MIN_VISIBLE_CHARS})")
    print(f"      pages FLAGGED blank/thin: {blank_before} -> {blank_after}")
    moved = [f"{n} {before[n]}->{after[n]}" for n, _ in CHECKS if before[n] != after[n]]
    same = [f"{n} {before[n]}" for n, _ in CHECKS if before[n] == after[n]]
    print(f"      CHANGED: {', '.join(moved) if moved else 'none'}")
    print(f"      unchanged: {', '.join(same)}")


async def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    brands = sys.argv[2:] or BRANDS
    for b in brands:
        try:
            await measure(b, n)
        except Exception as e:
            print(f"{b.upper():5} FAILED: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
