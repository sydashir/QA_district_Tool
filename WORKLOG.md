# District Site Auditor — Work Log

Continues from the session-1/2 summary (which ended at the crash-safe atomic history +
P1 NAP phones + P4 title bounds + the 845 enumeration check). Everything below is the work
after that point. File-anchored, newest work last. Local artifact (not committed).

## Dial-split — auto-classify display≠dial phone mismatches (commits 6e17b90, da39bbf, 386bc62)
- Wrote `auditor/nap.py` `brand_number_owners()` — maps every live canonical number → owning brand(s); best-effort, warn-on-failure
- Modified `auditor/config.py` — `BrandConfig.brand_numbers`; `load_brand` populates it from the NAP canon
- Modified `auditor/checks/phone.py` — split the mismatch: dialed ∈ own canon → benign CTM WARNING; ∈ another brand → `cross_brand_dial` ERROR (names owner); ∈ 2+ → ambiguous; ∈ nobody → unknown
- Modified `auditor/checks_version.py`, `auditor/diff.py` — `brand_numbers` as a phone-scoped check-version component
- Modified `auditor/checks/phone.py` + `tests/test_fingerprints.py` — put displayed number in the mismatch fingerprint (collision fix); updated dials-retired test
- Verified the canon is disjoint (71 numbers, 0 shared) before trusting cross-brand logic — built once, re-verified

## Union-scope — audit the live set (sitemap ∪ WP-REST), not just the sitemap (commit bb8c1c8)
- Modified `auditor/crawl.py` — `FetchResult.robots_header` (X-Robots-Tag), populated in `fetch_pages`
- Modified `auditor/parse.py` — `ParsedPage.is_noindex` (parses `<meta name=robots>`)
- Modified `auditor/audit.py` — `PageProjection.is_noindex` (meta OR header); `run_audit` audits the union deduped by canonical_url; `audit_scope` meta
- Rewrote `auditor/checks/enumeration.py` — `classify(noindex bool)` (dropped `_meta_noindex`/`_head_noindex`/html); `from_audit()` derives all enum findings from the single union fetch (no second crawl)
- Modified `auditor/cli.py` — prints `audit_scope` + enum stats; dropped the separate enum-probe param
- Modified `tests/test_enumeration.py` — `from_audit` + `classify(noindex)` tests; suite green at 98

## Verification sweep — which reports stand vs re-run
- Wrote `spike/union_delta.py` — read-only per-brand sitemap-vs-REST delta probe (no page fetch)
- Measured rest-only adds: GL +845, COC +1,213, DBH +483, TDRC +10, CAD +10, AR +8, AH +1
- Wrote `spike/added_page_findings.py` — isolates findings on the previously-unaudited rest-only pages
- Proved the re-scope worked: COC 1,479 / DBH 910 / GL 1,627 findings on pages the old audit never saw

## Union re-audits (all crawled brands, union + dial-split)
- DBH → 494/498 (was 15); 1 page dials RR (cross-brand ERROR)
- COC → 1,515/1,697 (was 484); auto-caught the motivating bug — 2 pages dial GL's 844-576-0144; 182 sitemap-dead 404s
- AH → 160/161; dial-split exposed 3 pages dialing RR (hidden in the old lumped classification)
- AR → 510/519; run self-caught the label_leak fingerprint collision
- TDRC → 19/21; CAD re-baselined → 1,236 (repaired the limit-5 smoke clobber), 2 `[acf field]` leak tokens validated
- Determinism confirmed: re-running DBH/COC reproduced the prior union baselines exactly (0 new / 0 resolved)

## label_leak fingerprint collision fix (commit 877d669)
- Modified `auditor/checks/structure.py` — keyed the fingerprint on `(level, occurrence)` so the same leaked label at two positions keeps distinct identities (Check #2 pathology avoided)
- Modified `tests/test_fingerprints.py` — updated assertion + collision-regression test; suite green at 99

## GL union audit — gate the Jake report
- Ran the full GL union audit → 3,574/3,591, 4,666 findings
- Split the 845 added pages: 13 indexable (20 findings, 1 err) vs 822 noindex (1,607 findings) → proved the sent report audited the right set
- Dial-split on GL's 88-page CTM question → `cross_brand_dial` NONE (benign); 2 pages dial unknown 888-861-1658
- No headline moved except 800-692-9850 blast radius (2,739 → 3,574, all extra pages noindex, same fix)

## GL report edits — send-ready (`reports/gl/REPORT.md`)
- 800-692-9850 count 2,739 → 3,574 (glance table, exec summary, finding 1a with the baseline-vs-full-inventory reconciliation)
- Folded the 2nd button-dial page into 1d (888-861-1658: 1 page → 2), kept the "confirm whether retired" framing, not cross-brand
- Added one ACF-section line for the 6 `[acf field]` tokens on noindex geo pages

## Link-probe timeout fix (commit 34c4856)
- Modified `auditor/config.py` — `CrawlRules.external_link_timeout_seconds` (default 5s)
- Modified `auditor/checks/links.py` — `_head_or_get(timeout)` explicit; probe picks full 20s for internal hosts, 5s for external
- Modified `tests/test_links.py` + `tests/test_fingerprints.py` — timeout-split test, fake-client `timeout` kwarg
- Wrote `spike/linkprobe_timing.py` — measured 108 min → 95 s (~68×), identical signal (7 broken, 10 unverified); suite green at 100

## RR + MHD crawl mechanics
- Modified `config/mhd.toml` (commit 4a991e9) — throttle-safe (concurrency 2, 1s delay, 30s timeout, 2 retries)
- Launched RR, caught 0.26 pg/s, stopped it rather than trust a throttled baseline
- Probed RR: serial (host healthy 0.3s) → concurrency 1/2/5 (cache-warming order) → cold-vs-warm (cold ~20s / warm 0.3s = cold-cache, not throttle) → concurrency 10 (safe 20/20, 0 5xx) → concurrency 16 (67% success, latency doubled → rejected per the hard rule)
- Modified `config/rr.toml` (commit 0970270) — cold-cache tuned (timeout 45s, retries 2, concurrency 10)
- Ran RR union at concurrency 10 → 7,721/7,827 fetched OK (98.6%), 14,034 findings, 209 errors; spot-checked 6/6 "unreachable" = slow cold-render alive (200 in 18–27s), not dead/throttle
- RR wall-clock ~19h but ~5–6h real crawl — the Mac slept twice; crawl suspended+resumed clean. Lesson: long crawls need `caffeinate -i`
- Launched MHD (last brand) under `caffeinate -i` at throttle-safe settings, union = all 8,761

## Network-level rollup findings (assembled from all reports)
- **Cross-brand call misrouting — 7 ERROR pages across 4 brands:** RR→GL (1, /ppc), COC→GL (2), DBH→RR (1, /contact-us), AH→RR (3, template-test pages). Calls dialing another District site's number. Owner + exact `tel:` captured per page.
- **Unknown-dial (10):** dominated by 949-782-7724 recurring across RR/DBH/AR/CAD on /our-facilities|/gallery pages — likely a real shared line missing from the NAP canon, not 4 separate bugs (a canon-completeness gap to confirm).
- **`[acf field]` leak — 176 tokens across 3 brands:** RR 168 (ALL under `/drug/rehab` = one template, one fix), GL 6 (noindex geo-rollups), CAD 2. Same defect class the Fetcher's pre-import Check #2 guards on the sheet — we catch it on the rendered page (closes the loop after import).

## Conventions
- All commits `sydashir <meetashirr@gmail.com>`, short/lowercase/no trailers; nothing pushed (local only)
- ClickUp + GeoData Fetcher strictly read-only throughout; never contacted Jake/HWA
- Self-caught and fixed: a pytest exit-code masking issue, a miscalibrated stall monitor, a wrong-field read on cross-brand details — verified before claiming each time

## 2026-08-01

- Modified auditor/parse.py — `_visible_text()` now preserves block boundaries as newlines instead of
  `get_text(" ")` welding `</p><h2>` into one sentence; treated as a **v1 bug, not a Phase 2 fix**,
  since every check reading visible_text was affected. Took two attempts: v1 used insert_before/after
  (bs4 tree mutation is O(n²) — 13.1s per page), v2 a naive recursion (4.1s), final a non-mutating
  `descendants` walk (0.012s). The pilot then caught a second weld I had introduced — dropping the
  inline separator fused sibling `<a>` links into "addictionDetox" and produced 24 bogus findings from
  one nav block; restored `str(el) + " "` and re-verified all three properties (commits 13cdc64, a740018)
- Measured the blast radius of that bug on existing reports before re-quoting anything — same 20 pages,
  before vs after: CAD 20% → 20% (identical), GL 15% → 15% (identical), **RR 20% → 15%**
  (double_preposition 3→2, truncated_word 3→2). RR's 20% was inflated by 2 weld artifacts; corrected
  rates to quote are CAD 20%, RR 15%, GL 15%. Also verified the check-version path fires correctly —
  simulated a vanished finding with `src:parse.py` changed and the page still audited+live → status
  `rule_changed`, not `resolved`. Correction to the earlier framing: `content_hash` is NOT affected,
  because `page_hash()` hashes `stable_markup(html)`, not visible_text
- Modified auditor/checks/empty_slot.py + tests — added a 5th deterministic pattern `missing_unit`
  ("within 15 of Costa Mesa" — the number survived, the unit did not), ERROR severity, with 6 negative
  fixtures proving "within 30 days" / "within 24 hours" / "within 5 mi" stay silent. Moved it out of the
  AI tier deliberately: it was 4 of the 6 real AI findings and it is a regex, not a judgement (commit ec0ff31)
- Wrote auditor/ai/build_allowlist.py + allowlist.json — 2,488 proper nouns (1,359 mined from the crawl
  cache requiring presence in ≥2 brands, 915 counties, 416 from the brand guides); validated the Phase 2
  cost model with the real count_tokens endpoint rather than an estimate: 3,280 tok/page deduped →
  101.8M network input → **$51 Haiku batch / $102 Sonnet / $255 Opus**
- Wrote spike/seeded_control.py and ran the decisive recall control — corrupted one word per real GL block
  and measured whether the model finds the word we broke, in two groups: **A = Connor's confirmed
  misspellings** (which misspelling.py already catches by exact list) and **B = novel typos on no list
  anywhere** (the model's actual job). Result on BOTH Haiku 4.5 and Sonnet 5: **A 4/4 = 100%, B 9/9 = 100%**.
  Verified all 15 seeded words were absent from the allowlist first, or the prompt would have suppressed them
- Ran the GL precision pilot on Sonnet 5 (same 60 blocks) and hand-classified all 18 findings: **11 real,
  3 model errors, 4 artifacts of my own pilot block-splitter** — `blocks()` collapses the newlines
  parse.py just emitted, re-welding separate `<li>`/heading blocks into fake run-on sentences. Verified
  each against the live page rather than guessing: "symptom management Comprehensive" and "rehabilitation
  Generally" are separate blocks (artifact), "Family Therapy Attended West Chester" is genuinely one block
  (real defect). Precision 61% raw / **79% excluding my harness bug** vs Haiku's 50% — the 80% bar is not
  cleared yet and the next fix is mine, not the model's

## 2026-08-01 (evening)

- Fixed a MANUFACTURED-DEFECT bug in `auditor/parse.py` — a space was emitted after every inline text
  node, so live GL's correct `<strong>phone rings</strong>.` came out as "phone rings ." and the AI
  layer reported a punctuation error that is not on the page. Verified against the raw live HTML, not
  inferred. The ORIGINAL `get_text(" ", strip=True)` had the same flaw, so this is in every report
  shipped to date, not a regression. Fix inserts a separator only where BOTH sides of the boundary are
  alphanumeric; keeps the "addictionDetox" weld fixed and still surfaces a genuine " ," the client
  really typed. New tests/test_parse.py pins all three failure modes. Measured: space-before-punct
  occurrences on 20 GL pages 91 -> 8 (commit 51fecd1)
- RETRACTION on my own earlier report: the findings I called REAL — 'rings .', 'needs ,', 'gestures ,',
  'Estates ,' — were this artifact. Sonnet's round drops 11 real -> 6 (61% -> ~33%), Haiku's 6 -> 4
  (50% -> ~33%). Also corrected a commit hash I quoted that never existed (`ec0ff31`); the missing_unit
  commit had never landed because the command timed out before `git commit` ran — now 59dd02f
- Cached `nap.grid_from_xlsx` — `load_brand()` parsed the same workbook TWICE (canonical numbers +
  third-party hotlines) at ~25s each, making it a 51s call. Now 14.1s cold / 0.15s warm, and
  **tests/test_audit.py went 132s -> 3.4s**; full suite 176 green in 4.7s. Regression test asserts the
  cached grid equals a fresh parse (commits fa7479f, + test)
- Fixed the pilot's block splitter to split on block boundaries before sentences — it was re-collapsing
  the newlines parse.py emits, welding headings into paragraphs. Side effect: the cost model fell 34%
  ($51 -> $33 Haiku / $67 Sonnet) because welded nav+copy blocks were unique per page and defeated dedup
- Tightened `missing_unit` after seeing it fire on "symptoms subside within 3 to 5 days" — the unit of a
  range sits after the range's last number, and the engine backtracked to the shortest match. Now uses an
  atomic group plus a range separator; "within 3 to 5 days", "within 5-7 days", "within 30, 60, and 90
  days" all stay silent while "within 15 of Lake Forest" still fires (commit 2bc05d6)
- Ran the full 2,404-block corpus through both models and classified all 473 findings with a 12-agent
  workflow (6 independent classifiers against a fixed rubric, then an adversarial refute pass over every
  REAL). **Sonnet 5 beats Haiku 4.5 about 2:1 on both precision and yield** — strict precision 55% vs 28%
  after removing the parser artifacts, 100 real findings vs 48. Neither clears the 80% bar as a general
  grammar pass

## 2026-08-01 (late) — parse ground-truth gate

- Built the mechanical gate for `parse.py` — `tests/fixtures/parse/` holds 7 REAL HTML fragments saved
  from live GL/CAD pages, each with a `.source.txt` recording brand and URL, paired with an
  `.expected.txt` captured from a BROWSER via Playwright `innerText`. The expected values come from
  something other than the code under test, which is the whole point; the module docstring forbids
  regenerating them from `parse_html`, since that would only assert the code still does whatever it
  currently does
- Proved the gate works by injecting each historical bug and watching it fail — the original
  `get_text(" ", strip=True)` trips 4 fixtures, the unconditional inline space trips 2. It is not a
  test that would have passed either bug (commit 72401e0)
- Covered every case that has bitten: `<strong>x</strong>.` gains no space, heading/paragraph boundary
  preserved, adjacent `<a>` links separated not welded, inline markup mid-sentence intact, consecutive
  `<li>` separate, and a genuine client-typed " ," still surfaces. That last fixture is labelled
  CONSTRUCTED because a 50-page GL/CAD sample contained ZERO client-typed stray spaces in body copy —
  which is itself the evidence that every " ," the AI reported was a parser artifact
- Restricted the Phase-2 prompt to the six mechanical defect types that survived adversarial review,
  and banned word choice, rewrites and hyphenation outright — hyphenation split the judges
  ("one-on-one" and "top-notch" survived, "same-day" and "inpatient-level" did not), and a class we
  cannot adjudicate is a class we should not ship (commit 1998743)
- Added `is_person_or_review()` to drop staff bios and customer testimonials from the corpus, then
  measured it rather than trusting it: 10 of 5,367 blocks excluded (0.2%), every one genuinely a bio
  or review on inspection, zero over-exclusion — but those 10 blocks had produced 28 of the judged findings
- Caught a cost-model error of my own while re-running: `count_tokens` is model-specific, and the same
  2,395 blocks are 66.8M tokens to Haiku but 111.2M to Sonnet. **Sonnet is $111 for the network, not
  the $67 I quoted** — I had applied Haiku's token count to Sonnet's price
