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
import concurrent.futures as cf
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import anthropic

from auditor import crawl as C
from auditor.config import load_brand
from auditor.css_cache import BrandCSS
from auditor.parse import parse_html

ALLOWLIST = Path(__file__).resolve().parent.parent / "auditor" / "ai" / "allowlist.json"
MODEL = os.getenv("PILOT_MODEL", "claude-haiku-4-5")   # the high-volume spelling tier (§D6)
_WS = re.compile(r"\s+")


def blocks(text: str) -> list[str]:
    """Split on BLOCK BOUNDARIES first, then sentences within each block.

    parse.py emits one newline per block element. Splitting on sentence punctuation alone and then
    collapsing all whitespace threw that boundary away, re-welding a heading into the paragraph
    below it and consecutive <li> items into each other. The model then correctly reported the
    run-on it was shown — "symptom management Comprehensive treatment" — for a defect that does not
    exist on the page. Verified against live GL pages: those two are separate blocks; a third
    ("Family Therapy Attended West Chester") really is one block and really is missing a period.
    """
    out = []
    for line in text.split("\n"):
        for raw in re.split(r"(?<=[.!?])\s+", line):
            b = _WS.sub(" ", raw).strip()
            if len(b) >= 40:
                out.append(b)
    return out


# Words the CLIENT DID NOT WRITE. Staff bios and customer reviews are quoted or personal voice: a
# grammar "defect" there is real English pedantry the client will never action, and the judged runs
# put 28 findings in this bucket. Excluding them is not hiding defects, it is scoping the audit to
# the marketing copy the client actually controls.
_CREDENTIALS = re.compile(
    r"\b(LMFT|AMFT|LCSW|ACSW|LPCC|APCC|MFTI|CADC|RADT|CATC|PsyD|MSW|LVN|CDCA|"
    r"Licensed Marriage and Family Therapist|Clinical Director|Program Director)\b")
_BIO_NARRATIVE = re.compile(
    r"\b(Attended\s+[A-Z]|graduated from|graduate of|Originally from|earned (his|her)|"
    r"received (his|her)|holds a (BA|MA|BS|MS|degree)|alma mater|years of dedicated experience)\b")
_REVIEW_VOICE = re.compile(
    r"\b(highly recommend|would recommend|recommend this place|saved my life|living proof|"
    r"best decision|thank you so much|the staff (is|are|was|were)|staff really|really cared|"
    r"changed my life|forever grateful)\b", re.IGNORECASE)
_FIRST_PERSON = re.compile(r"\b(I|I’m|I'm|I’ve|I've|my|me)\b")
_EVALUATIVE = re.compile(
    r"\b(amazing|wonderful|helpful|caring|incredible|awesome|great|beyond helpful|top notch|"
    r"grateful|blessed|life[- ]changing)\b", re.IGNORECASE)


# The content filter alone cannot catch a bio with no credential in it — "Jack Petti comes from a
# unique background, having struggled with addiction from the very early age of twelve years old"
# reads like ordinary prose. But the PAGE it lives on gives it away, so the two signals are combined.
_PERSONAL_PAGE = re.compile(
    r"/(about-us|about|our-team|team|staff|leadership|meet-the-team|testimonial|testimonials|"
    r"reviews|alumni)(/|$)", re.IGNORECASE)


def is_personal_page(url: str) -> bool:
    return bool(_PERSONAL_PAGE.search(url))


def is_person_or_review(b: str) -> bool:
    """True when a block is a staff bio or a customer testimonial rather than client-authored copy."""
    if _CREDENTIALS.search(b) or _BIO_NARRATIVE.search(b) or _REVIEW_VOICE.search(b):
        return True
    # a short first-person evaluative sentence is a review even without a stock phrase
    return len(b) < 260 and bool(_FIRST_PERSON.search(b)) and bool(_EVALUATIVE.search(b))


async def collect(n_pages: int) -> tuple[list[str], int, int, int]:
    """Unique NON-BOILERPLATE blocks. A block present on >=30% of pages is nav/footer/CTA chrome:
    it is not page copy, it is the largest source of false positives (headings run into paragraphs,
    menus arrive as one 'sentence'), and re-checking it is pure cost. Excluding it fixes precision
    and spend together."""
    cfg = load_brand("gl")
    async with C.make_client(cfg.crawl) as client:
        urls, _b, _c, _f = await C.enumerate_sitemap(client, cfg.sitemap_url, max_retries=3)
        urls = C._apply_exclude(urls, cfg.crawl.exclude)
        step = max(1, len(urls) // n_pages)
        sample = urls[::step][:n_pages]
        per_page: list[tuple[str, list[str]]] = []
        brand_css = BrandCSS()
        for u in sample:
            st, _f2, html, _e, _h = await C._request(client, u, max_retries=2)
            if st != 200 or not html:
                continue
            # One fetch per brand. GL links zero stylesheets (all inlined) so this is a no-op here,
            # but the same collector runs for the other eight brands, six of which link 12-47.
            await brand_css.load(client, html, u, max_retries=2)
            parsed = parse_html(html, page_url=u, base_url=u,
                                extra_css=brand_css.css, css_status=brand_css.status)
            per_page.append((u, blocks(parsed.visible_text)))
            await asyncio.sleep(cfg.crawl.delay_seconds)

    pages = len(per_page)
    freq: dict[str, int] = {}
    for _u, bl in per_page:
        for h in {hashlib.sha1(b.encode()).hexdigest() for b in bl}:
            freq[h] = freq.get(h, 0) + 1
    cutoff = max(2, int(0.30 * pages))

    seen: set[str] = set()
    uniq: list[str] = []
    total = 0
    boiler = 0
    personal = 0
    for u, bl in per_page:
        page_is_personal = is_personal_page(u)
        for b in bl:
            total += 1
            h = hashlib.sha1(b.encode()).hexdigest()
            if freq[h] >= cutoff:
                boiler += 1
                continue
            if page_is_personal or is_person_or_review(b):
                personal += 1
                continue
            if h not in seen:
                seen.add(h)
                uniq.append(b)
    return uniq, total, pages, boiler, personal


# Restricted to the classes that SURVIVED adversarial review on the 473-finding judged run. The
# earlier open-ended "spelling, grammar, punctuation" brief produced mostly word-choice rewrites —
# "relapse probability" -> "relapse risk", "has tons of fun" -> "offer many fun" — which the client
# would reject and which buried the real defects. Hyphenation is deliberately excluded too: it split
# the judges ("one-on-one" and "top-notch" survived, "same-day" and "inpatient-level" did not), and
# a class we cannot adjudicate is a class we should not ship.
SYSTEM = """You are a proofreader for a US behavioral-health website network. US English, AP style.

You are given ONE block of page text. Report ONLY these SIX mechanical defect types. Nothing else
is a finding, no matter how much better you could write the sentence.

1. MISSING SPACE after sentence-ending punctuation — "Athletic Fund.Originally from Boston"
2. SPACE BEFORE punctuation — "hard times ." or "closely with you , showing"
3. MISSPELLING — a genuinely misspelled English word, e.g. "programing" -> "programming"
4. BROKEN WORD — a word split by a stray space, e.g. "rehab program s" -> "programs"
5. SUBJECT-VERB DISAGREEMENT that is unambiguous — "His journey ... have put him" -> "has put him"
6. MISSING POSSESSIVE APOSTROPHE — "a Masters in Marriage" -> "a Master's in Marriage"

HARD PROHIBITIONS — these are NOT findings and reporting them is an error:
- WORD CHOICE or synonyms of any kind. If the original word is a real word used correctly, leave it.
- Rewriting, restructuring, shortening, or "improving" a sentence.
- HYPHENATION. Never add or remove a hyphen. Never report "one on one", "same day", "top notch".
- Commas that are optional: Oxford commas, commas around asides, commas after introductory phrases.
- Capitalisation, heading style, sentence length, list formatting, colons before lists.
- Anything about license numbers, ID codes, or alphanumeric identifiers.
- Singular/plural choices that read naturally, and American-vs-British variants correct in US English.
- Person-first language ("people with addiction") is REQUIRED and is never an error.
- "near {{City}}" ("Rehab near Cerritos") is this network's DELIBERATE house phrasing for geo pages.

The ALLOWLIST below is real proper nouns this business uses — cities, counties, facilities, brands,
drugs, clinical terms. NEVER report an allowlisted term as a misspelling.

Every finding MUST be a short exact substring that is wrong plus a short exact replacement. If you
cannot express the fix as a short replacement string, it is not one of the six types — omit it.
If the block has no defect of these six types, return an empty findings list. Most blocks will.

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
    uniq, total, pages, boiler, personal = asyncio.run(collect(n_pages))
    allow = json.loads(ALLOWLIST.read_text())["terms"]
    client = anthropic.Anthropic()

    system = SYSTEM.format(allowlist=", ".join(allow))
    tok_sys = client.messages.count_tokens(
        model=MODEL, system=system,
        messages=[{"role": "user", "content": "x"}]).input_tokens
    body = client.messages.count_tokens(
        model=MODEL, messages=[{"role": "user", "content": "\n\n".join(uniq)}]).input_tokens

    print(f"=== 1. cost model validation ({pages} GL pages) ===")
    print(f"  blocks: {total} total | {boiler} boilerplate | {personal} bio/testimonial | "
          f"{len(uniq)} unique client-authored blocks sent")
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
    from collections import Counter as _C
    dropped = _C()

    def _one(b):
        r = client.messages.create(
            model=MODEL, max_tokens=1000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": b}])
        try:
            return b, json.loads(next(x.text for x in r.content if x.type == "text")), r
        except Exception:
            return b, {"findings": []}, r

    # Concurrency: measuring precision needs ENOUGH findings to classify, and with correctly-split
    # (smaller) blocks a 60-block sample yields 1-3. Serial calls made a large sample take an hour;
    # the spend is pennies either way, so the sample size was limited by wall-clock, not cost.
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_one, sample))
    for i, (b, out, r) in enumerate(results, 1):
        for f in out.get("findings", []):
            wrong, correct = (f.get("wrong") or "").strip(), (f.get("correct") or "").strip()
            # FIX 2a: hard drop degenerate output where the "correction" is the same string
            if not wrong or not correct or wrong.lower() == correct.lower():
                dropped["identical"] += 1
                continue
            # FIX 2b: length guard — a real correction is a short replacement, not prose advice
            if len(correct) > 80 or len(wrong) > 80:
                dropped["prose_not_correction"] += 1
                continue
            # FIX 3: never assert a corrected identifier from a model guess; surface it for a human.
            # NARROW: an identifier is an alphanumeric CODE (digits AND letters fused, e.g. 190119BP).
            # "any digit" was too broad — it swallowed "Copyright (c) 2026" and real prose defects
            # like "within 20 of Los Alamitos", hiding genuine findings behind "verify this".
            if re.search(r"\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9-]{5,}\b", wrong):
                findings.append({"wrong": wrong, "correct": "(verify against the source record)",
                                 "kind": "identifier", "confidence": "low", "block": b[:160]})
                continue
            findings.append({"wrong": wrong, "correct": correct, "kind": f.get("kind", "?"),
                             "confidence": f.get("confidence", "?"), "block": b[:160]})
        if i % 200 == 0:
            print(f"  ...{i}/{len(sample)}  cache_read={r.usage.cache_read_input_tokens}", flush=True)

    print(f"\n  blocks checked: {len(sample)}   findings: {len(findings)}   filtered_out: {dict(dropped) or 0}")
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
