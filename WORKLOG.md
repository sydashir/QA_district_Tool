# District Site Auditor — Work Log

Continues from the session-1/2 summary (which ended at the crash-safe atomic history +
P1 NAP phones + P4 title bounds + the 845 enumeration check). Everything below is the work
after that point. File-anchored, newest work last. Tracked in git; SESSION_STATE.md holds the
working detail this summarises.

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

## 2026-08-03 — wrapper design; service-account limits established
- Wrote `docs/plans/2026-08-03-wrapper-design.md` — VM + systemd timer, one spreadsheet (Summary append + per-brand new/open), three-layer failure story
- Probed the service account read-only: it can edit 20+ shared sheets but **cannot create one anywhere** (`storageQuota.limit = 0`, no Shared Drives) — a human must create the sheet and share it
- Verified `NAP_SHEET_ID 1AU_wNukif…` live: "NAP Phone numbers / UTM Codes / DBAs", tab "NAP (Current)"

## 2026-08-04/05 — full nine-brand acceptance run
- Run `20260804T171824`, `python3 -m auditor.cli all`. 8 brands, 15,525 pages, 46,441 findings, 8h20m; RR alone 5h44m
- Fixed `replace_tab` trim (commit c02020b) — clearing from row N+1 on an N-row grid returned HTTP 400; every brand over 1,000 findings would have failed identically (AR at 1,184, DBH at 2,280)
- Resilience confirmed as asked: the run continued past both failures, resume printed the same run id, retries UPDATED rather than appended, **zero 429s** across everything
- Operational finding: `caffeinate -i` does not stop lid-close sleep — the run stalled 13.5h with the process alive. Into the README
- Wrote the 2026-08-05 handover block in `SESSION_STATE.md` (what's shipped, what's broken, what's open)

## 2026-08-05/06 — spelling check, and a fourth `parse.py` content bug
- Built `auditor/checks/spelling.py` on **pyspellchecker 0.9.0** (chosen by measurement: 12ms `unknown()` on 8,000 words); layered English dict + `allowlist.json` + mined `domain_vocab.json` + brand words; wired all three as spelling-scoped check-version components in `diff.py`
- **HTML comments were being read as page text** — bs4's `Comment` subclasses `NavigableString`, so the descendants walk collected them. Measured share of `visible_text` that was comment text: GL 9.5%, CAD 27.0%. Fixed (8997e86) + `html_comment` fixture in the ground-truth gate (4e8bf36)
- Measured the fix's blast radius per brand: **no existing check moved**; only the new spelling check did (CAD 606→46, GL 325→18) — the comment bug was generating ~90% of its findings
- Mining heuristic calibrated from data, not taste (8fd99ac): `MIN_CONTEXTS = 3` (lorem-ipsum words all score 1), edit-distance-1 to a freq≥100 word decisive, distance 2 only without context diversity, `MAX_D2_LEN = 10`

## 2026-08-06 — client defect coverage table
- Read all 7 client PDFs; wrote `docs/plans/2026-08-06-client-reported-defect-coverage.md`: 12 classes already covered, 11 deterministic gaps (B1–B11) ranked by how often the client reported them, plus judgement-only and browser-only sections
- Root cause found for B1: `parse.py`'s `find_all("a", href=True)` discarded hrefless anchors before any check ran — a dead button was never findable

## 2026-08-06/07 — B1–B8 shipped
- B2 `auditor/checks/placeholder.py` generalised into patterns (`empty_state`, `shortcode`, `variable_name`), B7 `has_double_slash` in `links.py` (09ccffc, 8e07d25, c7af060)
- **`fused_url` found while verifying B7** — CAD's footer LinkedIn href is two URLs concatenated, on 40/40 pages, never reported by the client
- B1 + B8 in a new `auditor/checks/actions.py`; `parse.py` now captures an `Actionable` for every `<a>` and `<button>`. 2,664 clickable elements over 9 pages → 22 `dead_cta` + 7 `social_misrouted`, all hand-classified, no false positives left (da0e7f6, 2a096fd, 20480b6)
- Four false-positive classes fixed by measuring first: GL mega-menu shape, TDRC amenity labels, `<button>` excluded (its behaviour is JS we do not execute), RR mega-menu column headings
- B6 `brands.py`, B3/B5 `duplication.py`, B4 `empty_row.py` — one batched `parse.py` commit gave all four what they needed (`Block`, `ParsedPage.blocks`, region/group on `Actionable`). `audit.py::_collapse_repeats` turns template-wide faults into one row: GL actions 141→3 (ad5e71e, 822462f, 17227be, 550f56e, 128aa22)
- CLAUDE.md gained the parse.py batching rule

## 2026-08-08 — B9/B10/B11 settled; nine-brand re-run
- B10 `cruft_link` + B11 `feed_link` in `links.py`; scanned all 16,564 cached pages before building. B10 real on 5 brands (RR `rehab-admissions-old/` linked from 56 pages, GL `adderall-detox-delete/` from 1,204); **B11 zero everywhere** — WordPress declares feeds, it does not link them. B9 left in section C with the evidence
- Wrote `docs/WHAT_THE_AUDIT_DOES_NOT_CHECK.md` (client-facing boundary) and `scripts/post_run_report.py`
- Adversarial review before the crawl caught a severe one: `Actionable.group` keyed on the immediate parent, so B5 could never fire for the case the client reported. Grouped by the enclosing list/table/nav instead
- Added the gh-drift rule to CLAUDE.md (three accounts; active silently flips to dev778d)

## 2026-08-10/12 — republish, three self-inflicted bugs, handover
- Shape-collapse extended to `empty_slot`/`misspelling`/`scope`: 50,438 rows → 31,793, nothing lost
- **Collapsed rows read as FIXED** — `_CHECK_COMPONENT` was never extended, so the live sheet said "AH: 171 fixed" when nothing was. Fixed + an invariant test (`tests/test_collapse_component_wiring.py`)
- **The DBH static-list fallback was in the wrong function and the tests still passed** — implemented in `crawl.enumerate_pages`, which `run_audit` never calls. Moved into `run_audit` and verified end to end (`union=576`). Lesson: test the path the product takes
- AR publish failed on **clock skew**, not credentials — the machine was 21h behind after a long sleep
- Final republish: COC untriaged errors 2,783 → 117, GL 5,291 → 506. DBH, invisible two days earlier, immediately found a dead "Learn More" on 524 of 561 pages and 17 pages dialling RR
- Handover artefacts: `docs/WHAT_THIS_TOOL_IS.md`, CLAUDE.md §13, `docs/incidents/2026-08-08-dbh-dns-outage.md`, `docs/2026-08-10-network-rollup.md`

## 2026-08-19/22 — the product could not do what the CLI could
- `POST /api/brands/{code}/runs` took no cap, so it queued a full 11,439-page MHD census and parked RR behind ~54h of it. Added `Brand.default_sample_size` (MHD=900, evidence-based: the largest sample ever published), `Run.max_pages`, `Run.cancel_requested`, a `cancelled` status distinct from `failed`/`refused`, and `POST /api/runs/{id}/cancel` (commit 9de75c0, migration 0002)
- A running crawl does **not** abort mid-flight and the API says so plainly — polling a flag inside `run_audit` is an `auditor/` edit and a full cache invalidation. Deliberate limitation
- Migration applied and **downgrade tested for real** on the live DB; all 98 pre-existing rows got `cancel_requested = false`
- Found a real race (a689347): `jobs.py`'s docstring had promised `lock="brand:<code>"` since day one while neither `lock` nor `queueing_lock` was ever passed to `defer` — 6 concurrent POSTs produced two concurrent audits of one live origin
- **Runbook fails on Debian 12** — bookworm has python 3.11 and postgresql 15, so §2B dies on its first install line. Ubuntu 24.04 is the only supported target; `deploy/README.md` corrected with the measured evidence
- MHD run 99 recovered from disk after Postgres died mid-run: the crawl writes to `reports/`, so `load_report_into_run` rebuilt the row. Third time the lesson landed — the report on disk is the source of truth

## 2026-08-23/24 — fourth spelling attempt: detect CORRUPTION, not incorrectness
- The reframe (Syed's): D9/D10/D11 all tried to judge whether text is *correct*. Build checks that find text that is *broken*, the way `empty_slot` already does
- Shipped 6 families in `empty_slot.py` + 12 mined typos in `misspelling.py::MINED` (dca1be4). **59/60 hand-classified true (98%)** on 876 GL pages
- The rarity filter is load-bearing: substituting "common in the dictionary" for "common in THIS corpus" collapses precision 92% → 42%. That is exactly why D10 died at 9% and this did not
- Rejected with numbers in ARCHITECTURE.md D12: `space_before_punct` (2,315 hits, 0% — parser artifact), `mid_sentence_capital`, `stranded_fragment`, `unterminated_block`, `verbless_sentence`
- Per-brand mine across all nine (fa01f94, 45916d2, b6368fc, b8002e0): AH 100% · GL 92% · COC 89% · DBH 89% · AR 86% · CAD 86% · RR 82% · MHD suppressed · TDRC below minimum corpus **by construction** (4,455 words, zero occur 100+ times)
- Headline finds: `Distric Behavioral Health` (DBH's own name, ×1535), `Graditude`/`Renaisance`, `Alcohol Rehab Calfornia`, GL's publicly live lorem-ipsum dev page
- 5 of 9 brands write British English — shared writer or content source; client standard is US

## 2026-08-25/27 — rendering layer: design, safety, axe, markup a11y
- `docs/plans/2026-08-25-rendering-layer-design.md`. Research changed the design twice: axe-core's `incomplete` outcome already solves the contrast-over-background-image false positive, and `target-size` is 24×24 AA (not the 44×44 asked for) and passes small targets with adequate spacing
- Gathered the tracker deny-list from live HTML rather than guessing — CTM serves from the account-numbered `418804.tctm.xyz`, and **VWO was on 8 pages and nobody listed it** (rendering would have enrolled fake visitors into live A/B tests)
- Built `render/safety.py` + 22 tests (ff19cfd), then `render/a11y.py` and `render/images.py` against local fixtures (aa061bf). `render/` is a top-level package on purpose: `checks_version` hashes all of `auditor/checks/`
- **Markup-only a11y shipped** (`render/markup.py`, d606a8b) — HTML fetched by httpx, analysed in a browser with every request aborted. 2,274 raw violations collapse to 185 rows. The guard now proves itself with a **canary** (a request to a non-existent domain) rather than by counting blocks, because DBH's headless rebuild legitimately blocks zero
- Schema ground truth over 318 JSON-LD blocks: DBH and MHD publish **no schema.org markup at all**; COC has one invalid block. Caught a false cross-brand finding before reporting it — TDRC's `/review-us/*` URLs are deliberate 301s to sister brands, so check `final_url`, never the requested URL

## 2026-08-28/31 — NAP union, schema checks, first production render, and the container that vanished
- Build list items 1–3 on branch `buildlist` in a worktree while MHD 108 crawled (e46ee4b, 6fb168a, 856d9ea, 703c2a2, 9ff7c30), merged to main as 2cc07a4
- NAP is a **union, not a replacement**: phones stay from "NAP (Current)"; address + name come from the new transposed tab, because that tab is a strict subset on phones. Reason recorded in the module and pinned by a test
- `schema.business_name_internal` — GL carries a second `LocalBusiness` node named with the internal CMS label (`[NoIndexed] Kratom (Plant) (DrugInfo Blog)`). 9/10 and 11/12 on GL, **zero on RR/CAD/COC/AH/AR** (the control)
- Off-domain redirect fix: `_project` returns `_off_brand_projection` when `final_url` leaves the brand's registrable domain, and the skipped page is itself an INFO finding. Blast radius measured from the caches: 20 of 20,300 pages, all `/review-us/*`
- **First production render** (6b258c8, 9777053): GL's homepage blocked zero requests because its trackers are consent-gated. Then `scroll_to_load_everything` was found to be barely scrolling — themes set `scroll-behavior: smooth`, so repeated `scrollTo` restarts an animation and the loop reached **1,181px of 6,862**. Every lazy image below that was a candidate to be called broken. Fixed + `confirm_broken` re-fetches each candidate; 10 would-be false positives killed across 24 pages, 0 real broken images
- **The Postgres container was gone** on 2026-08-31 — the data survived in an orphaned anonymous volume. Adopted into the compose stack by pg_dump/pg_restore, verified by content checksum (`5e093aec…` identical before and after), backup schedule finally installed (`deploy/auditor-backup*.timer`, `deploy/backup-verify.sh` proven to fail on a truncated dump)
- `scripts/accessibility_pass.py` — accessibility findings had been **zero in the database since the check was built**, because the module exposes a batch `audit_html` rather than the per-page `run()` that `_PAGE_CHECKS` calls. 224 findings stored across nine brands. It caught its own bug on first run (TDRC reporting GL's phone number — the same off-brand redirect trap, new place)
- Hashed batch landed in one invalidation (16f6b62): `broken_links:redirected_internal`, `meta:collision_slug`, and **a CSS selector on findings** — the text layer always knew which anchor it flagged and was discarding it
- **The image is not the repo.** Runs 109–118 executed an image built 3h37m before the batch commit; proved from the data (0 `redirected_internal`, 0 `collision_slug`, 0 selectors). `scripts/check_image_current.py` now gates the sequence

## 2026-09-01/02 — traffic v1, two throttling incidents, and the schedule claim
- Traffic ranking v1 on CSV exports: migration 0003 `page_traffic`, `scripts/traffic_import.py`, `--match-report`. Every CSV row stored `value_state='unknown'` — Google writes missing values as zeros, so no CSV number can be called a measurement
- **GL, RR and MHD refused in 2–3 seconds each** — our own queueing started three enumerations in the 8 seconds after a 400-request link-probe burst. The client-facing message said their site was unreachable. It was not
- **RR recorded `ok` after reaching 77 of 8,029 pages**, producing 7,777 `sitemap_unreachable` findings — a claim that 97% of the client's site was down. The guard was `pages_audited == 0`. Added `crawl_verdict` in `server/jobs.py` (unhashed): refuses below a **50% fetch floor**, measured across 106 historical runs (median 99.7%, exactly one run below 50%), plus blocked-vs-empty wording and a 90s cooldown. Deliberately did not add a relative "collapse from this brand's norm" test — at 0.50 it can never fire, and a guard that cannot trigger is worse than none
- ARCHITECTURE.md D17: *a fast negative is a suspicious negative*
- Product screenshots (f87f5e0) and `scripts/seed_demo.py` (4f932a7) — the seeder refuses a database holding real findings, every URL on `*.demo.invalid`
- **The dashboard claimed audits run overnight.** `ensure_brands` stamped `"0 2 * * *"` on every non-MHD brand unconditionally while nothing schedules anything. Removed; all nine crons NULL; installing a timer is now a required documented step (30254ad)

## 2026-09-03 — three reboots, a Postgres PANIC, and the locator
- `PANIC: replication checkpoint has wrong magic 0` — `pg_logical/replorigin_checkpoint` was 8 bytes of zeros after a hard reset. Verified at source level (PG16 treats a missing file as a silent no-op and rewrites it at the next checkpoint) before moving the file aside; `pg_resetwal` rejected throughout. **Zero data loss**, all nine brands matched their pre-crash counts
- Standing risk recorded: the storage layer lost an fsync'd write, and `data_checksums = off` on this cluster
- `scripts/locator_measure.py` — replays a finished report's selectors through the same `capture()` the feature uses. First GL result put `display_dial_mismatch` at 27%, but 15 of 22 misses were **byte-identical mobile/desktop copies of one anchor**; the equivalence fix took it to 95% while `dead_cta` stayed put (the control)
- Adversarial review, 18 raised / 7 confirmed / 7 fixed (7127478). Two of them invalidated numbers already reported: the measurement dropped every occurrence-suffixed class (`display_dial_mismatch#2`) with a silent `continue`, so n=22 was really 70; and `boxed()` scored PNGs of the **wrong place** as hits, because off-canvas twins pass size and visibility tests and usually precede the desktop copy in the DOM
- Honest full-population numbers: `dead_cta` 41 selectors, 90% located / 61% captured; `display_dial_mismatch` 70, 99% / 51%. The absence is now explained per finding (`render/shots.py::ABSENCE_REASONS`, `details.shot_absent`) with one standing sentence: **"Some findings have no picture, and that does not make them less certain."**
- `MIN_N_FOR_VERDICT = 10` — `cross_brand_dial` at n=1 printed "0% BELOW FLOOR", which is a statement about one element's layout, not the locator
- RR lost 9h50m to **Low Power Sleep on a flat battery** — `caffeinate -i` holds only idle sleep. A long flat gap in `fetch pages` looks exactly like a host throttle; the tell is that throughput resumes at the old rate
- GL's resume cache (`pages.json` 55 MB, `resume.done.jsonl` 75 MB) vanished 34 minutes after run 128 finished. Disk space, the other eight brands, the auditor's own logic and the test suite all ruled out. **Cause never established.** Consequence fixed instead: `accessibility_pass.sample_urls` falls back to the newest report's URLs, so a missing cache can no longer make a brand silently skip its checks
- `scripts/shot_pass.py` — the picture feature had **no production path at all** (`details ? 'shot'` matched 0 of 376,370 rows). It now loads pages first-party with the canary-proven block and writes `details.shot`

## 2026-09-04/08 — tool complete; full nine-brand run
- `tests/test_shot_pass.py` closed the last engineering item: it photographs only what the report shows, a page that will not load leaves an explained absence, MHD is never rendered. Each property proved able to fail by mutating the behaviour out
- Runs 131–140: 15,341 pages over eight brands. **RR's poisoned baseline cleared** — it would have reported 11,259 new, it reported 46; COC 1,334 → 145. MHD refused itself on zero enumeration without touching its baseline
- Two environmental incidents: clamshell sleep on battery killed RR run 138 at 6,200 pages (third sleep loss, third mechanism), and macOS purged `~/Library/Caches/ms-playwright/` mid-pass
- Unexplained, not guessed at: RR run 140 reused 70 cached pages when 6,282 were banked and no hashed file had changed
- Syed's six review points: Section D is now **derived** from which classes exist in source and in the run, so a check that exists can no longer be listed as not-done; phone numbers humanised at display time only (`phone.py` is hashed); "certain" defined where it appears; sparklines labelled per-brand-normalised; the web app shows the pictures

## 2026-09-14 — Search Console CSVs, traffic ranking, and Postgres out of Docker
- Imported seven brands' UI exports. **Five are exactly 1,000 rows** — the export cap, not a matching fault: GL/CAD/COC/DBH match at or within 3% of their computable ceiling
- "Keep the first row per page" loses impressions where jump-link anchors overlap (AR 26%, CAD 20%). Right rule: **sum clicks, keep the max-impression row** — clicks are exclusive, impressions are not
- **DBH's headless CMS host is public, self-canonical and index/follow** — 301 URLs, 78 clicks, 47,273 impressions in three months going to `cms.districtbehavioralhealth.com` instead of the real site, and its robots.txt advertises a staging sitemap. Confirmed live; a real client defect, not a tool artefact
- Built `server/traffic.py` as the one definition shared by report and API (a2c82e8): `url_key`, `aggregate`, `NO_SEARCH_BY_DESIGN`, union-not-sum reach, five no-data states. Harm first — traffic orders only *within* a severity. 13 deliberate mutations, all caught
- Found live: the dashboard compared impressions with visits inside one severity, putting two GL schema findings above the site-wide phone fault with 34,974 visits. One metric per list now
- **Postgres moved out of Docker** (7edf80a) to Homebrew `postgresql@16` on port 55432 — the port every existing default already used, so no config changed. pg_restore in 17s, row counts identical on all 12 tables

## 2026-09-15 — shot pass, login agents, the API importer, and the redirect join
- Shot pass reported nothing about its own guard and treated a failed canary as "page did not load". Now per-brand blocked/allowed/unrecognised totals, and an unproven canary raises out with exit 2 (17ec995). Live: canary blocked before all 32 page loads, 84 tracker requests blocked (VWO 32, GTM 19, CTM 20…), 1,882 allowed
- `deploy/macos/login_agents.sh` — launchd agents for the API (8099) and web app (5173), RunAtLoad + KeepAlive, proven by `kill -9` (c6b664f). The API does not reload code: `launchctl kickstart -k gui/$(id -u)/local.district-auditor.api`
- **`crm.zoho.com` resolved** rather than logged: a guarded re-render with Zoho blocked showed one parser-initiated `<script src>` on RR's homepage only; read `zcga.js` itself (Google Ads attribution — gclid cookie, hidden `zc_gad` fields, no XHR or beacon); grepped every render pass for click/fill/type/submit. A render cannot create a CRM record. Blocked anyway
- **Search Console API importer** (3e31b0d): `pick_property` from `sites.list` (never a URL-prefix on another host — the zero-row trap), `fetch_pages` paging by `startRow` until 0 rows, 429/5xx retried, API rows replace CSV for the same period. Verified against the CSV: impressions identical on every page for all seven brands, clicks identical except 5 RR pages. Unweighted findings fell RR 81%→12%, GL 74%→49%, CAD 47%→11%, COC 37%→13%
- www/bare twins folded only when the twin **permanently** redirects (c9ccb26): TDRC's homepage traffic (392 clicks) was on `www.`, which 301s; DBH's does not, so its 23 rows stay unmatched
- **GL's 49% answered directly, not inferred** (dd176b2): all 452 indexable unmatched pages returned 0 rows when queried one at a time (597 requests, 0 failures; controls 25/25 exact). Real zero impressions, not Google's tail-dropping. One dated evidence sentence in GL's report
- **Redirect join** (8795ae1, 56d0b6b, 7efbf27) — migration 0005 `page_redirects`, rebuilt from each brand's crawl cache but only when it holds at least as many distinct URLs as the latest ok run audited. Two review workflows drove three corrections: a page counts once however many of its addresses a finding names; **only the destination's own rows count** (alias traffic had added an old address's pre-redirect history — COC's homepage gained 50,773 impressions from an Orange County page); enumeration findings never follow redirects; and the dashboard sorts the real page above a redirect copy. Weighting gained: RR +207, GL +86, COC +22, AR +19, CAD +15
- Known limitation left documented: the report's "on N pages" counts addresses, so a group whose addresses all land on one page can say "on 5 pages". No traffic number depends on it

## 2026-09-16 — engineering closed
- Syed: "Nothing to change. That's the last engineering item." Last commit `7efbf27`, suite 1,047, nine reports rebuilt with 14 pictures and 24 reasons across 428 shown findings
- What remains is operating the tool, not extending it. The open decisions are his and are listed in CLAUDE.md §13: AR's real scope (474 pages or ~11,000), whether to pay a full re-crawl to fix the poisoned diff baseline, and whether anything should schedule the tool
