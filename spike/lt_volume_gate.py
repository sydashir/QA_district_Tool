"""D11 volume gate — LanguageTool, GRAMMAR ONLY, on real GL pages.

The cheap question first: does a rule-based grammar engine say ANYTHING on this corpus? Precision
is expensive to establish (every finding hand-classified, no sampling), so it is not paid for until
the check has proven it fires at all.

**Gate: under ~0.05 findings/page the check is too quiet to ship and D11 stops here.**

Configuration, per the 2026-08-11 research:
  * `MORFOLOGIK_RULE_EN_US` **disabled** — LanguageTool's spellchecker reproduces D10 exactly
    (it flags isotonitazene, solriamfetol, buprenorphine). Grammar only is the whole point.
  * STYLE / REDUNDANCY / COLLOCATIONS disabled — word-choice editorialising is precisely what
    killed D9. Measure with them OFF, not on.
  * `QB_NEW_EN*` filtered client-side — LanguageTool's own experimental "there might be a mistake
    here" rules, which CodeRabbit disables by default as a known false-positive source.
  * `/v2/check` over httpx. `language_tool_python` is **GPL-3.0** and must never be imported into
    client work.

Text is our own region-aware `body_text` (nav/header/footer excluded), so the pilot sees exactly
what the shipped checks see rather than a friendlier corpus.

Usage: python3 -m spike.lt_volume_gate [n_pages] [port]
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
from collections import Counter

import httpx

from auditor import crawl as C
from auditor.config import load_brand
from auditor.css_cache import BrandCSS
from auditor.parse import parse_html

SEED = 0xD15                      # same seed as the audit's sampler -> reproducible page set
DISABLED_RULES = "MORFOLOGIK_RULE_EN_US"
DISABLED_CATEGORIES = "STYLE,REDUNDANCY,COLLOCATIONS,TYPOGRAPHY,CASING"
NOISY_PREFIX = "QB_NEW_EN"
GATE = 0.05                       # findings per page below which we stop
MAX_CHARS = 40_000                # LT rejects very large payloads; body text is chunked to this


async def _lt_check(client: httpx.AsyncClient, port: int, text: str) -> list[dict]:
    matches: list[dict] = []
    for i in range(0, len(text), MAX_CHARS):
        chunk = text[i:i + MAX_CHARS]
        if not chunk.strip():
            continue
        r = await client.post(
            f"http://127.0.0.1:{port}/v2/check",
            data={"text": chunk, "language": "en-US",
                  "disabledRules": DISABLED_RULES,
                  "disabledCategories": DISABLED_CATEGORIES},
            timeout=120.0)
        r.raise_for_status()
        matches.extend(r.json().get("matches", []))
    # LanguageTool's own experimental rules; excluded by rule ID, not by category.
    return [m for m in matches
            if not str(m.get("rule", {}).get("id", "")).startswith(NOISY_PREFIX)]


async def main() -> None:
    n_pages = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8763

    cfg = load_brand("gl")
    async with C.make_client(cfg.crawl) as crawler:
        urls, _b, _c, _f = await C.enumerate_sitemap(
            crawler, cfg.sitemap_url, max_retries=3)
        urls = C._apply_exclude(urls, cfg.crawl.exclude)
        urls = sorted(random.Random(SEED).sample(urls, min(n_pages, len(urls))))
        print(f"GL: {len(urls)} pages (seed {SEED:#x})", flush=True)

        css = BrandCSS()
        st, fin, home, _e, _h = await C._request(crawler, cfg.base_url, max_retries=2)
        if home:
            await css.load(crawler, home, fin or cfg.base_url, max_retries=2)

        by_rule: Counter = Counter()
        per_page: list[tuple[str, int]] = []
        examples: dict[str, tuple[str, str, str]] = {}
        pages = chars = 0

        async with httpx.AsyncClient() as lt:
            for n, u in enumerate(urls, 1):
                stt, _f2, html, _e2, _h2 = await C._request(crawler, u, max_retries=1)
                if stt != 200 or not html:
                    continue
                p = parse_html(html, page_url=u, base_url=u,
                               extra_css=css.css, css_status=css.status)
                text = p.body_text
                if not text.strip():
                    continue
                pages += 1
                chars += len(text)
                try:
                    ms = await _lt_check(lt, port, text)
                except Exception as e:                     # a dead server must not look like a clean corpus
                    print(f"  LT ERROR on {u}: {type(e).__name__}: {e}", flush=True)
                    raise
                per_page.append((u, len(ms)))
                for m in ms:
                    rid = m.get("rule", {}).get("id", "?")
                    by_rule[rid] += 1
                    if rid not in examples:
                        ctx = m.get("context", {})
                        snippet = ctx.get("text", "")
                        off, ln = ctx.get("offset", 0), ctx.get("length", 0)
                        examples[rid] = (u, m.get("message", ""),
                                         f"{snippet[:off]}[[{snippet[off:off+ln]}]]{snippet[off+ln:]}"[:150])
                if n % 25 == 0:
                    tot = sum(c for _u, c in per_page)
                    print(f"  {n}/{len(urls)} pages — {tot} findings so far "
                          f"({tot / max(1, pages):.3f}/page)", flush=True)

    total = sum(c for _u, c in per_page)
    rate = total / max(1, pages)
    print("\n" + "=" * 78)
    print(f"pages checked      : {pages}")
    print(f"body chars         : {chars:,} (mean {chars // max(1, pages):,}/page)")
    print(f"findings           : {total}")
    print(f"FINDINGS PER PAGE  : {rate:.4f}")
    print(f"gate               : {GATE}  ->  {'CLEARS — measure precision' if rate >= GATE else 'TOO QUIET — D11 stops here'}")
    print(f"pages with >=1     : {sum(1 for _u, c in per_page if c)} of {pages}")
    print("=" * 78)
    print("\nBY RULE (hand-classify these if the gate cleared):")
    for rid, n in by_rule.most_common(30):
        u, msg, ctx = examples[rid]
        print(f"  {n:>4}  {rid}")
        print(f"        {msg[:96]}")
        print(f"        {ctx}")

    out = {"pages": pages, "findings": total, "per_page": rate,
           "by_rule": dict(by_rule), "examples": {k: list(v) for k, v in examples.items()},
           "config": {"disabled_rules": DISABLED_RULES,
                      "disabled_categories": DISABLED_CATEGORIES,
                      "noisy_prefix_filtered": NOISY_PREFIX}}
    path = "spike/lt_volume_gate_result.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    asyncio.run(main())
