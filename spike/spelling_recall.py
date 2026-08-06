"""Recall control for the dictionary spellcheck — does it catch typos, or is it just quiet?

Precision alone cannot answer that. CAD's spelling findings fell 606 -> 46 once HTML comments were
excluded, and aggressive filtering can look exactly like accuracy: a checker that reports nothing
scores perfect precision and is worthless. So this seeds Connor's SEVEN confirmed misspellings into
real GL page text and asks whether the dictionary check finds them.

Deliberately tested against `spelling.py` ALONE, with `misspelling.py` out of the picture. The
known-list check catches these seven by construction — that proves nothing about whether the
dictionary would catch the EIGHTH typo, which is the entire reason this check exists.

Each seeded word is verified absent from every vocabulary layer first; a term sitting in the
allowlist would be suppressed by design and the trial would be meaningless.

Usage: python3 -m spike.spelling_recall [pages]
"""
from __future__ import annotations

import asyncio
import re
import sys

from auditor import crawl as C
from auditor.checks import misspelling, spelling
from auditor.config import load_brand
from auditor.css_cache import BrandCSS
from auditor.parse import ParsedPage, parse_html

# Connor's confirmed set, from checks/misspelling.py — the ground truth we already trust.
SEEDS = dict(misspelling.KNOWN)
SEEDS.update(misspelling.KNOWN_LOWER_ONLY)


def _vocab_clean(word: str, cfg) -> bool:
    """True when no vocabulary layer already knows the word (so the trial is meaningful)."""
    return bool(spelling._unknown({word}, cfg))


async def main(n_pages: int) -> None:
    cfg = load_brand("gl")
    async with C.make_client(cfg.crawl) as client:
        urls, _b, _c, _f = await C.enumerate_sitemap(client, cfg.sitemap_url, max_retries=3)
        urls = C._apply_exclude(urls, cfg.crawl.exclude)
        step = max(1, len(urls) // n_pages)
        css = BrandCSS()
        pages: list[tuple[str, str]] = []
        for u in urls[::step][:n_pages]:
            st, fin, html, _e, _h = await C._request(client, u, max_retries=2)
            if st != 200 or not html:
                continue
            await css.load(client, html, fin or u, max_retries=2)
            p = parse_html(html, page_url=u, base_url=u, extra_css=css.css, css_status=css.status)
            if len(p.visible_text or "") > 800:
                pages.append((u, p.visible_text))
            await asyncio.sleep(cfg.crawl.delay_seconds)

    print(f"seeding into {len(pages)} real GL pages\n")
    print("PRE-CHECK — every seeded word must be unknown to all vocabulary layers:")
    for typo in SEEDS:
        print(f"   {'clean ' if _vocab_clean(typo, cfg) else 'IN VOCAB (invalid trial!)'} {typo}")

    print("\nRECALL — dictionary check only (misspelling.py deliberately not consulted):")
    hits = 0
    for i, (typo, correct) in enumerate(sorted(SEEDS.items())):
        url, text = pages[i % len(pages)]
        # corrupt a real word so the surrounding sentence stays authentic
        target = correct.split()[0]
        pat = re.compile(rf"\b{re.escape(target)}\b", re.IGNORECASE)
        seeded = pat.sub(typo, text, count=1) if pat.search(text) else f"{text} We offer {typo} care."
        fs = spelling.run(ParsedPage(url=url, visible_text=seeded), cfg)
        found = [f for f in fs if f.details.get("word", "").lower() == typo.lower()]
        got_fix = found[0].details.get("suggestion") if found else ""
        hits += bool(found)
        print(f"   {'HIT ' if found else 'MISS'} {typo:<14} -> suggested {str(got_fix) or '(none)':<16}"
              f" (correct answer: {correct})")
    print(f"\nRECALL: {hits}/{len(SEEDS)} = {hits / len(SEEDS):.0%}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 12))
