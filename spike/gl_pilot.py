"""Phase-2 GL pilot: validate the cost model with count_tokens, then measure the FALSE-POSITIVE
rate of a real spelling/grammar pass with the allowlist.

Two questions, in order:
  1. Is the §D5 cost model right?  -> count_tokens on real deduped GL blocks ($0).
  2. Is the allowlist good enough to make the output usable? -> run the pass on N blocks and
     hand-classify every finding. The client's standard is over-flag but never cry wolf, so the
     number that matters is how many findings are junk.

Nothing here writes a report or touches v1. Read-only against the site; one batch of model calls.

Usage: python3 -m spike.gl_pilot [n_pages] [n_blocks]
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import anthropic

from auditor import crawl as C
from auditor.config import load_brand
from auditor.parse import parse_html

ALLOWLIST = Path(__file__).resolve().parent.parent / "auditor" / "ai" / "allowlist.json"
MODEL = os.getenv("PILOT_MODEL", "claude-haiku-4-5")   # the high-volume spelling tier (§D6)
_WS = re.compile(r"\s+")


def blocks(text: str) -> list[str]:
    out = []
    for raw in re.split(r"(?<=[.!?])\s+", text):
        b = _WS.sub(" ", raw).strip()
        if len(b) >= 40:
            out.append(b)
    return out


async def collect(n_pages: int) -> tuple[list[str], int, int]:
    cfg = load_brand("gl")
    async with C.make_client(cfg.crawl) as client:
        urls, _b, _c, _f = await C.enumerate_sitemap(client, cfg.sitemap_url, max_retries=3)
        urls = C._apply_exclude(urls, cfg.crawl.exclude)
        step = max(1, len(urls) // n_pages)
        sample = urls[::step][:n_pages]
        seen: set[str] = set()
        uniq: list[str] = []
        total = 0
        pages = 0
        for u in sample:
            st, _f2, html, _e, _h = await C._request(client, u, max_retries=2)
            if st != 200 or not html:
                continue
            pages += 1
            for b in blocks(parse_html(html, page_url=u, base_url=u).visible_text):
                total += 1
                h = hashlib.sha1(b.encode()).hexdigest()
                if h not in seen:
                    seen.add(h)
                    uniq.append(b)
            await asyncio.sleep(cfg.crawl.delay_seconds)
    return uniq, total, pages


SYSTEM = """You are a copy-editor for a US behavioral-health website network. US English, AP style.

You are given ONE block of page text. Report ONLY:
- spelling errors
- clear grammar errors
- punctuation errors that change meaning

CRITICAL RULES:
- The ALLOWLIST below contains real proper nouns used by this business: city names, county names,
  facility names, brand names, drug and medication names. NEVER report an allowlisted term as a
  misspelling.
- Do NOT report style preferences, tone, capitalisation choices in headings, or American-vs-British
  variants that are correct in US English.
- Do NOT report a term as misspelled unless you are confident it is wrong. Marketing copy for this
  industry contains many clinical and place names.
- Person-first language ("people with addiction") is REQUIRED and is never an error.
- If the block has no error, return an empty findings list.

ALLOWLIST (proper nouns — never flag these):
{allowlist}
"""

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["findings"],
    "properties": {"findings": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["wrong", "correct", "kind", "confidence"],
        "properties": {
            "wrong": {"type": "string"}, "correct": {"type": "string"},
            "kind": {"type": "string", "enum": ["spelling", "grammar", "punctuation"]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        }}}},
}


def main(n_pages: int, n_blocks: int) -> None:
    uniq, total, pages = asyncio.run(collect(n_pages))
    allow = json.loads(ALLOWLIST.read_text())["terms"]
    client = anthropic.Anthropic()

    system = SYSTEM.format(allowlist=", ".join(allow))
    tok_sys = client.messages.count_tokens(
        model=MODEL, system=system,
        messages=[{"role": "user", "content": "x"}]).input_tokens
    body = client.messages.count_tokens(
        model=MODEL, messages=[{"role": "user", "content": "\n\n".join(uniq)}]).input_tokens

    print(f"=== 1. cost model validation ({pages} GL pages) ===")
    print(f"  blocks: {total} total -> {len(uniq)} unique  (dedup saves {100*(1-len(uniq)/max(1,total)):.1f}%)")
    print(f"  deduped body tokens: {body:,}  -> {body/max(1,pages):,.0f} tok/page")
    print(f"  cached prefix (system+allowlist): {tok_sys:,} tokens  [min cacheable: 4096 Haiku / 1024 Sonnet 5 / 512 Opus 5]")
    est = body / max(1, pages) * 31037
    print(f"  => projected network input (31,037 pages): {est/1e6:,.1f}M tokens")
    for name, rate in (("Haiku 4.5", 1.0), ("Sonnet 5 (intro)", 2.0), ("Opus 5", 5.0)):
        print(f"     {name:18} batch input ~= ${est/1e6*rate*0.5:,.0f}")

    print(f"\n=== 2. GL pilot: {n_blocks} unique blocks through {MODEL} ===")
    step = max(1, len(uniq) // n_blocks)
    sample = uniq[::step][:n_blocks]
    findings = []
    for i, b in enumerate(sample, 1):
        r = client.messages.create(
            model=MODEL, max_tokens=1000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": b}])
        try:
            out = json.loads(next(x.text for x in r.content if x.type == "text"))
        except Exception:
            continue
        for f in out.get("findings", []):
            findings.append({**f, "block": b[:160]})
        if i % 20 == 0:
            print(f"  ...{i}/{len(sample)}  cache_read={r.usage.cache_read_input_tokens}", flush=True)

    print(f"\n  blocks checked: {len(sample)}   findings: {len(findings)}")
    from collections import Counter
    print(f"  by kind: {dict(Counter(f['kind'] for f in findings))}")
    print(f"  by confidence: {dict(Counter(f['confidence'] for f in findings))}")
    print("\n  ALL FINDINGS (hand-classify these):")
    for f in findings:
        print(f"    [{f['kind']}/{f['confidence']}] {f['wrong']!r} -> {f['correct']!r}")
        print(f"        ...{f['block']}...")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 25,
         int(sys.argv[2]) if len(sys.argv) > 2 else 60)
