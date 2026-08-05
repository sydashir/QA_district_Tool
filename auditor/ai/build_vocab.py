"""Mine the DOMAIN vocabulary a general English dictionary does not know.

`build_allowlist.py` mines PROPER NOUNS — capitalised tokens from titles and H1s. That is the wrong
shape for a spellchecker, because the words that will drown it are lowercase clinical terms in body
copy: buprenorphine, naloxone, benzodiazepine, comorbid, dialectical. Verified against
pyspellchecker's 160,572-word English dictionary — every one of those is "unknown" to it.

Same trust rule as the proper-noun allowlist, for the same reason: **a term must appear on >= 2
BRANDS to count as real vocabulary.** A typo is a typo on one site; a word that nine independently
edited sites all use is the industry's vocabulary, not a mistake. Single-brand unknowns are exactly
where real typos live, so admitting them would blind the check to the thing it is for.

Crawls a sample per brand because the page cache stores only titles and H1s — the body text this
needs was never kept.

Usage: python3 -m auditor.ai.build_vocab [pages_per_brand]
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from auditor import crawl as C
from auditor.config import load_brand
from auditor.css_cache import BrandCSS
from auditor.parse import parse_html

OUT = Path(__file__).resolve().parent / "domain_vocab.json"
BRANDS = ["gl", "rr", "cad", "coc", "ah", "ar", "mhd", "tdrc", "dbh"]
MIN_BRANDS = 2
DEFAULT_PAGES = 120

# Lowercase alphabetic words only. Digits, codes and capitalised proper nouns are handled elsewhere.
_WORD = re.compile(r"\b[a-z][a-z'’\-]{3,}\b")


def _dictionary():
    from spellchecker import SpellChecker
    return SpellChecker(language="en")


async def mine_brand(brand: str, n_pages: int, spell) -> set[str]:
    """Lowercase words on this brand that the English dictionary does not know."""
    cfg = load_brand(brand)
    found: set[str] = set()
    async with C.make_client(cfg.crawl) as client:
        urls, _b, _c, _f = await C.enumerate_sitemap(client, cfg.sitemap_url, max_retries=3)
        urls = C._apply_exclude(urls, cfg.crawl.exclude)
        if cfg.wp_rest and cfg.wp_rest.enabled and len(urls) < n_pages:
            rest, _a = await C.enumerate_wp_rest(client, cfg.wp_rest, max_retries=2)
            seen = set(urls)
            urls += [u for u in C._apply_exclude(rest, cfg.crawl.exclude) if u not in seen]
        if not urls:
            print(f"  {brand.upper():5} no urls — skipped")
            return found
        step = max(1, len(urls) // n_pages)
        sample = urls[::step][:n_pages]
        css = BrandCSS()
        pages = 0
        for u in sample:
            st, fin, html, _e, _h = await C._request(client, u, max_retries=2)
            if st != 200 or not html:
                continue
            pages += 1
            await css.load(client, html, fin or u, max_retries=2)
            p = parse_html(html, page_url=u, base_url=u, extra_css=css.css, css_status=css.status)
            text = " ".join(filter(None, [p.visible_text, p.title, p.meta_description]))
            found |= set(spell.unknown(_WORD.findall(text.lower())))
            await asyncio.sleep(cfg.crawl.delay_seconds)
    print(f"  {brand.upper():5} {pages:>4} pages -> {len(found):>5} unknown lowercase words")
    return found


async def build(n_pages: int) -> dict:
    spell = _dictionary()
    per_term: dict[str, set[str]] = defaultdict(set)
    for brand in BRANDS:
        try:
            for term in await mine_brand(brand, n_pages, spell):
                per_term[term].add(brand)
        except Exception as e:
            print(f"  {brand.upper():5} FAILED: {type(e).__name__}: {e}")

    trusted = sorted(t for t, bs in per_term.items() if len(bs) >= MIN_BRANDS)
    single = sorted(t for t, bs in per_term.items() if len(bs) == 1)
    return {
        "meta": {
            "min_brands": MIN_BRANDS,
            "pages_per_brand": n_pages,
            "total": len(trusted),
            "single_brand_excluded": len(single),
            "dictionary": "pyspellchecker en (160,572 words)",
        },
        "terms": trusted,
        # kept for inspection: this is where real typos live, and it is the list to eyeball when
        # tuning. NOT loaded by the check.
        "excluded_single_brand_sample": single[:400],
    }


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PAGES
    print(f"mining domain vocabulary, {n} pages/brand, >= {MIN_BRANDS} brands to trust")
    data = asyncio.run(build(n))
    OUT.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    m = data["meta"]
    print(f"\nwrote {OUT}")
    print(f"  trusted domain terms: {m['total']}")
    print(f"  excluded (single-brand, where typos live): {m['single_brand_excluded']}")
    print(f"  sample: {data['terms'][:20]}")


if __name__ == "__main__":
    main()
