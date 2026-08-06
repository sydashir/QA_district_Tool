"""Mine the DOMAIN vocabulary a general English dictionary does not know.

`build_allowlist.py` mines PROPER NOUNS — capitalised tokens from titles and H1s. That is the wrong
shape for a spellchecker, because the words that will drown it are lowercase clinical terms in body
copy: buprenorphine, naloxone, benzodiazepine, comorbid, dialectical. Verified against
pyspellchecker's 160,572-word English dictionary — every one of those is "unknown" to it.

**Cross-brand frequency alone is NOT enough, and assuming it was produced a wrong answer.** The
first mine trusted `behavorial` because it appears on several brands — it is a misspelling of
"behavioral". Frequency assumes the brands are edited independently; they are not, they share
templates, so one author's typo propagates across sites and looks exactly like vocabulary.

Three signals instead, all derivable from the corpus we already have:

1. **Cross-brand presence** (>= 2 brands) — necessary, not sufficient.
2. **CONTEXT DIVERSITY.** Real vocabulary turns up in different sentences on different sites; a
   propagated template typo turns up in byte-identical surrounding text, because the whole block
   was copied. If every brand's context for a word is the same normalised window, frequency proves
   nothing — it is one author counted twice.
3. **CORRECTION CONFIDENCE.** Measured on known cases: real domain terms have NO nearby correction
   at all (buprenorphine, naloxone, benzodiazepine, acamprosate, naltrexone -> None), while typos
   correct at edit distance 1 to a frequent word (inpateint->inpatient, residental->residential).
   Edit distance 2 is ambiguous by itself — `behavorial->behavioral` (freq 2,932) and
   `comorbid->morbid` (freq 2,677) look identical on this signal — which is exactly why signal 2
   has to break the tie.

BRITISH SPELLINGS are deliberately NOT treated as typos here. `behavioural`, `counselling` and
`organisation` are correct English that happens not to be the US standard the client writes in.
They are collected separately so the check can raise them as a question rather than an error.

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
_WORD = re.compile(r"\b[a-z][a-z'\-]{3,}\b")


def _normalise(text: str) -> str:
    """Curly apostrophes are typography, not spelling: `it’s` must be tested as `it's`, or every
    contraction on the site reads as an unknown word."""
    return text.replace("\u2019", "'").replace("\u2018", "'")
CONTEXT_WORDS = 6        # words either side; enough to identify a copied block, short enough to vary
# Threshold set from measurement, not taste. Coincidental "corrections" of real clinical terms land
# at frequency 50 (breathwork->breastwork, suboxone->suboxide); genuine correction targets start at
# 117 (detoxifcation->detoxification) and 181 (inpateint->inpatient). 100 separates them.
MIN_CORR_FREQ = 100
# Distance-2 is affordable ONLY for short words: measured 995ms at 10 chars, 2.0s at 14, 5.3s at 23.
# The cases that need it are short — `behavorial` (10) and `comorbid` (8) — and long unknown words
# are essentially never typos of a dictionary word, so capping at 10 costs no real recall and keeps
# the analysis pass to roughly a second per candidate instead of several.
MAX_D2_LEN = 10
# Real vocabulary turns up in many different sentences: measured `angeles` 1,064 distinct contexts,
# `meth` 879, `adhd` 518. Copied junk sits at exactly 1 — every lorem-ipsum word in the corpus
# (enim, labore, magna, nostrud, tempor) scored 1. Three is a deliberately cautious line.
MIN_CONTEXTS = 3

# British -> US transformations. NOT suffix-only: "behavioural" is behaviour+al and "counselling"
# doubles the l, so the marker sits inside the word. Each is applied to the LAST occurrence and
# accepted only when it yields a word the dictionary knows.
_BRITISH_SUBS = (("our", "or"), ("ise", "ize"), ("isa", "iza"), ("yse", "yze"),
                 ("lling", "ling"), ("lled", "led"), ("ller", "ler"),
                 ("logue", "log"), ("mme", "m"), ("aemia", "emia"), ("oeu", "eu"), ("ae", "e"))


def _dictionary():
    from spellchecker import SpellChecker
    return SpellChecker(language="en")


def _contexts(text: str, words: set[str]) -> dict[str, set[str]]:
    """{word: {normalised +-N word window}} — the fingerprint of the sentence it sits in."""
    toks = _WORD.findall(text)
    out: dict[str, set[str]] = defaultdict(set)
    for i, t in enumerate(toks):
        if t in words:
            lo, hi = max(0, i - CONTEXT_WORDS), min(len(toks), i + CONTEXT_WORDS + 1)
            out[t].add(" ".join(toks[lo:i] + toks[i + 1:hi]))
    return out


def _looks_british(word: str, spell) -> str:
    """The US spelling this is a British variant of, or "" if it is not one.

    Only claims a variant when the transformed word IS in the dictionary and the original is not —
    so "detox" and "buprenorphine" cannot be mistaken for British spellings.
    """
    if spell.known([word]):
        return ""
    for brit, us in _BRITISH_SUBS:
        i = word.rfind(brit)
        if i < 0:
            continue
        cand = word[:i] + us + word[i + len(brit):]
        if cand != word and spell.known([cand]):
            return cand
    return ""


def _correction_signal(word: str, spell) -> tuple[str, int, int]:
    """(correction, edit distance, frequency). Empty when there is no distance-1 correction.

    EDIT DISTANCE 1 ONLY, for two reasons that agree with each other:

    * Speed, measured: ``spell.correction()`` explores distance 2, which is O(n^2) in word length
      and takes **10-41 SECONDS** for a long unknown word (`acamprosate` 10.3s,
      `about-the-asam-criteria` 41.3s). Across thousands of candidates that is hours. The
      distance-1 candidate set is O(n) and a set intersection.
    * Correctness. Distance 2 cannot tell a typo from a real term anyway — `behavorial->behavioral`
      and `comorbid->morbid` are indistinguishable on it. Those ties belong to the CONTEXT signal,
      which is the one that actually knows the difference.
    """
    dictionary = spell.word_frequency.dictionary

    def _best(cands):
        b, bf = "", 0
        for c in cands:
            if c == word:
                continue
            f = dictionary.get(c, 0)
            if f > bf:
                b, bf = c, f
        return b, bf

    best, freq = _best(spell.known(spell.edit_distance_1(word)))
    if best:
        return best, 1, freq
    # Distance 2 only for short words, and only when distance 1 found nothing. This is the path
    # that reaches `behavorial -> behavioral`; it is ambiguous on its own (`comorbid -> morbid`
    # scores the same), so the caller must weigh it against context diversity.
    if len(word) <= MAX_D2_LEN:
        best, freq = _best(spell.known(spell.edit_distance_2(word)))
        if best:
            return best, 2, freq
    return "", 0, 0


async def mine_brand(brand: str, n_pages: int, spell):
    """(unknown words on this brand, {word: {context windows}})."""
    cfg = load_brand(brand)
    found: set[str] = set()
    ctx: dict[str, set[str]] = defaultdict(set)
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
            text = _normalise(
                " ".join(filter(None, [p.visible_text, p.title, p.meta_description])).lower())
            unknown = set(spell.unknown(_WORD.findall(text)))
            found |= unknown
            for w, windows in _contexts(text, unknown).items():
                ctx[w] |= windows
            await asyncio.sleep(cfg.crawl.delay_seconds)
    print(f"  {brand.upper():5} {pages:>4} pages -> {len(found):>5} unknown lowercase words")
    return found, ctx


async def build(n_pages: int) -> dict:
    spell = _dictionary()
    per_term: dict[str, set[str]] = defaultdict(set)
    per_ctx: dict[str, dict[str, set[str]]] = defaultdict(dict)
    for brand in BRANDS:
        try:
            found, ctx = await mine_brand(brand, n_pages, spell)
        except Exception as e:
            print(f"  {brand.upper():5} FAILED: {type(e).__name__}: {e}")
            continue
        for term in found:
            per_term[term].add(brand)
            per_ctx[term][brand] = ctx.get(term, set())

    vocab, typos, british, template = [], [], [], []
    candidates = [(t, b) for t, b in sorted(per_term.items()) if len(b) >= MIN_BRANDS]
    print(f"\n  analysing {len(candidates)} cross-brand candidates "
          f"(distance-2 search runs on those <= {MAX_D2_LEN} chars)")
    for n, (term, brands) in enumerate(candidates, 1):
        if n % 200 == 0:
            print(f"    ...{n}/{len(candidates)}", flush=True)
        us = _looks_british(term, spell)
        if us:
            british.append({"word": term, "us_spelling": us, "brands": sorted(brands)})
            continue
        corr, ed, cfreq = _correction_signal(term, spell)
        # SIGNAL 2 decides, because it is the one that knows the difference. Real vocabulary is used
        # in many different sentences; a copied block is one author counted many times.
        windows = [w for b in brands for w in per_ctx[term].get(b, set())]
        distinct_ctx = len(set(windows))
        copied = distinct_ctx < MIN_CONTEXTS
        # SIGNAL 3 supports it. A one-letter fix to a common word is decisive on its own; a
        # two-letter fix is not — `behavorial->behavioral` and `comorbid->morbid` are identical on
        # that signal — so distance 2 only counts when context diversity is ALSO absent.
        correctable = bool(corr) and cfreq >= MIN_CORR_FREQ and (ed == 1 or copied)
        if correctable:
            typos.append({"word": term, "correction": corr, "edit_distance": ed,
                          "correction_frequency": cfreq, "brands": sorted(brands),
                          "distinct_contexts": distinct_ctx,
                          "why": ("one letter away from a common word" if ed == 1 else
                                  "two letters away from a common word AND used in fewer than "
                                  f"{MIN_CONTEXTS} distinct sentences — not independent usage")})
        elif copied and len(brands) >= MIN_BRANDS:
            template.append({"word": term, "brands": sorted(brands),
                             "note": "identical context on every brand — one template, not "
                                     "independent usage; not trusted as vocabulary"})
        else:
            vocab.append(term)

    single = sorted(t for t, bs in per_term.items() if len(bs) == 1)
    return {
        "meta": {
            "min_brands": MIN_BRANDS,
            "pages_per_brand": n_pages,
            "context_words": CONTEXT_WORDS,
            "min_correction_frequency": MIN_CORR_FREQ,
            "total": len(vocab),
            "rejected_as_typo": len(typos),
            "rejected_as_template_copy": len(template),
            "british_variants": len(british),
            "single_brand_excluded": len(single),
            "dictionary": "pyspellchecker en (160,572 words)",
        },
        "terms": vocab,
        # Everything rejected, WITH the reason. This is the audit trail for the heuristic: if it
        # ever mistrusts a real word, the evidence for that decision is here.
        "rejected_as_typo": typos,
        "rejected_as_template_copy": template,
        "british_variants": british,
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
    print(f"  rejected as typo: {m['rejected_as_typo']}")
    print(f"  rejected as template copy: {m['rejected_as_template_copy']}")
    print(f"  british variants (a question, not an error): {m['british_variants']}")
    print(f"  excluded (single-brand, where typos live): {m['single_brand_excluded']}")
    print(f"  sample vocabulary: {data['terms'][:20]}")
    for t in data["rejected_as_typo"][:10]:
        print(f"    TYPO  {t['word']:<18} -> {t['correction']:<18} {t['why']}")


if __name__ == "__main__":
    main()
