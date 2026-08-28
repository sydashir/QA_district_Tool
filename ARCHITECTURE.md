# ARCHITECTURE.md — District Site Auditor

> Authored 2026-07-13 (architecture design pass). **Part A** documents the system as-built and
> working (M0/M1), with the reasons each shape is what it is. **Part B** designs the parts not yet
> built (M2 reporting, multi-brand scaling, canonical-phone sourcing, Phase-2 AI). **Part C** records
> the R&D that grounds Part B (real code/data, not guesses). Companion to `CLAUDE.md` (the mandate +
> per-brand facts) and `SESSION_STATE.md` (working memory). Nothing here is built beyond M0/M1 — it is
> a design for approval.

The one hard line through the whole design: **the deterministic layer (v1) never imports, waits on,
or is gated by the AI layer (Phase 2).** Phase 2 consumes v1's crawl + parse output and emits its own
findings into the same `Finding` model. See §B4.

---

## PART A — AS-BUILT (M0 + M1, working on GL)

Package lives at the repo root (`auditor/`). Pipeline: **enumerate → reconcile → fetch → parse →
run checks → emit findings**. One CLI entry (`auditor/cli.py`, Typer): `audit --brand gl`.

### A1. Crawl layer (`auditor/crawl.py`) — M0

- **Enumeration is sitemap-first, index-aware, WP-REST-fallback.** `enumerate_sitemap` recursively
  expands nested `<sitemapindex>` entries (GL: `/sitemap_index.xml` → 15 child sitemaps → 2,746 page
  URLs), depth-bounded. If the sitemap is blocked/empty, `enumerate_wp_rest` paginates
  `/wp-json/wp/v2/{pages,posts}` on `X-WP-TotalPages`. *Why:* the sitemap is the most complete public
  URL set across post types; WP-REST is the authenticated/fallback path (§C6 confirmed it's public on
  8/9 brands).
- **Async fetcher, ported verbatim from the session-1 spike.** `httpx.AsyncClient` + a
  `Semaphore(max_concurrency)`, a fixed `delay_seconds` floor, one retry on transient errors, a
  browser-like User-Agent, follow-redirects. *Why these settings:* the spike proved them against GL's
  Cloudflare with a 50/50 fetch success and no challenge; M0 re-verified at 250/250. The browser UA is
  load-bearing — the default python-httpx UA is more likely to be challenged.
- **Content-hash cache** (`cache/<brand>/pages.json`, gitignored): sha256 of the *normalized* markup
  + length + status + timestamp per page. *Why normalized, not raw:* M0 found the raw-HTML hash is
  unstable across fetches because Cloudflare rotates `data-cfemail` / `/cdn-cgi/l/email-protection`
  hex per request. The store is M0; the *diff* is M2 (§B1).

### A2. Parse layer + shared normalizer (`auditor/parse.py`) — M0/M1

- `parse_html(html, url) → ParsedPage` extracts primitives only (title, meta description, heading
  list, deduped absolute links, visible text). No pass/fail judgement — that's the checks' job.
- **Cross-cutting helper `strip_volatile` / `stable_markup`** removes `<script>/<style>/<noscript>` +
  Cloudflare cfemail/`/cdn-cgi/` before visible-text extraction and before hashing. *Why one shared
  helper:* two consumers must never drift — the placeholder check (which scans visible text) and M2's
  content-hash diff. The spike's single ACF "hit" was a false positive *inside a `<script>`*; scanning
  raw HTML made that check pure noise. `visible_text` is now script-free by construction.
- **bs4 + lxml, deliberately not selectolax (yet).** Kept simple; only revisit if RR/MHD-scale perf
  demands it (§C5 says it doesn't, at the current design).

### A3. The five deterministic checks (`auditor/checks/`) — M1

Pluggable: each pure check is a module exposing `run(parsed: ParsedPage, config) → list[Finding]`; the
orchestrator (`auditor/audit.py`) calls them per page. The link check is the one cross-page pass. Each
ships **with the false-positive tuning the spike/M1 proved necessary** — a check isn't "done" until it
has fired on real content and its misfires are handled.

| Check | File | What fires | Tuning baked in (why) |
|---|---|---|---|
| Broken links | `checks/links.py` | 4xx/5xx, timeouts, redirect→≥400, per unique target (cross-page dedup) | Exclude `/cdn-cgi/`; **bot-hostile-host allowlist** (LinkedIn 999 / FB 400 / LegitScript+JointCommission 403 = "reachable but bot-blocks", a non-finding). M1: 4 real 404s, 0 FP on 250 pages. |
| Heading structure | `checks/structure.py` | multiple `<h1>`, skipped levels, empty headings, **template-label leaks** ("Contact Us (Pillar)") | **Scoped to H1–H3** (client says H4/H5 are SEO-noise; spike's H4/H5 hits were noise). No strict H2/H3 rules — that's open question #5, unwritten. |
| Placeholder / ACF | `checks/placeholder.py` | visible `[acf field=…]` + `{{var}}` tokens | **Visible-text only** (scripts stripped). Imports the **real** `extract_acf_tokens` from the Fetcher in isolation. M1: 0 findings on GL (spike's 21 were 100% script FPs, now gone). |
| Phone | `checks/phone.py` | tel-vs-display mismatch (flagship); non-canonical vs config | `phonenumbers` lib, not a hand regex. CTM client-side swap is out of static scope per [i]. Non-canonical dimension is **not yet trustworthy** — see §B3/§C1. |
| Blank/thin + meta | `checks/blank.py`, `checks/meta.py` | thin text, missing h1; missing/dup/oversized title + missing desc + malformed slug + **repeated title segment** | Thresholds tuned on real GL (min visible = 9,930 chars → 500 is safe). Title-length 70 flags 77% → reported as client-dependent, not silently re-picked. |

### A4. Result models (`auditor/report.py`) — pydantic, M1

`Finding` (url, check, severity, issue, location, snippet, suggestion, details) → `PageAudit` (per-page
+ `content_hash`) → `AuditReport` (per-brand run). *Why now, writers later:* the checks emit `Finding`
today; the CSV/JSON writers are M2 (§B1). **Do not redesign these models** — M2 and Phase 2 both target
them, which is exactly what keeps the two layers decoupled.

### A5. Reuse from the GeoData Fetcher (read-only; see CLAUDE.md §8)

- **In use:** `geo_field_validator.extract_acf_tokens` — loaded via `importlib` with a stubbed
  `services.sheets_service` so we get the *real* regex (single source of truth) without gspread and
  without copy-paste. The Fetcher stays strictly read-only.
- **Designed for reuse (not yet imported):** `WordPressService` auth+pagination (adapt to httpx async
  for the WP-REST enumeration path), `SheetsService` (one-shot NAP-sheet read — §B3), `LOCATION_MAP`
  (Phase-2 allowlist seed — §C4). `config/settings.py` env-var names + Cloudways staging endpoints.

### A6. Enumeration reconciliation (`audit.reconcile_enumeration`) — M1

Compares the sitemap URL set against WP-REST `/pages` and emits the delta as a `Finding` (not just a
fallback). GL: sitemap 2,746 vs WP-REST pages 3,591 → **845 live pages missing from the sitemap**
(largely `*-ppc` + `*-delete/-copy/OLD` cruft). Surfaced as **open question #8**; audit defaults to
sitemap-scope. *Why surface-not-decide:* whether the 845 are in scope is a product call, not an
engineering one.

---

## PART B — DESIGN (not yet built)

### B1. Reporting layer (M2) — low-risk, fully specified

**Scope:** `auditor/report.py` writers + a run-diff. No new models.

- **Writers:** `write_csv(report, path)` and `write_json(report, path)` → `reports/<brand>/<date>/`.
  CSV columns = the `Finding` fields flattened (url, check, severity, issue, location, snippet,
  suggestion, + `details` as a JSON string) — one row per finding, which is what a human triages in a
  sheet. JSON = the full `AuditReport.model_dump()` (lossless, for tooling). Both derive from the same
  `AuditReport`, so they can't disagree.
- **Severity rollup:** per-brand counts by `check × severity`, plus a top-N issues table, written as a
  `summary.json` + a human `summary.md`. This is the CLI summary M1 already prints, persisted.
- **Run-diff (the incremental engine):** keyed on the M0 content-hash cache, but the hash MUST be
  `content_hash(stable_markup(html))` (already wired in `PageAudit.content_hash`) so Cloudflare cfemail
  churn doesn't mark every page "changed" (the M0 finding). Diff = three sets: **new** (url not in prior
  cache), **changed** (hash differs), **unchanged** (skip re-audit unless `--full`). Output a
  `diff.md`: findings introduced / resolved / carried since the last dated run. *Why this shape:* the
  plan's whole point is that "first run is the big one; re-runs are cheap" — the diff is what makes a
  nightly 2–5am run affordable and what lets us prove "the fix landed."
- **Risk:** low. It's serialization over models that already exist and a set-diff over a store that
  already exists. Recommend building M2 next.

### B2. Multi-brand scaling — config-per-brand + a crawl-access abstraction

**The problem the design must solve:** 9 brands are **not uniform**. §C6 confirmed access modes differ,
and §C1 confirmed phone data is per-brand/per-location. The check code must stay brand-agnostic — no
`if brand == "RR"` anywhere in `checks/`.

- **Config model already generalizes.** `BrandConfig` (pydantic, `auditor/config.py`) + one
  `config/<brand>.toml` per brand is the right shape; M0 proved it on GL. Populate the other 8 from
  §C6 (base URL, `/sitemap_index.xml`, WP-REST base) + §C1 (phones). DBH stays absent until Asif
  supplies a URL (open question #3).
- **Add an explicit access-mode enum to `BrandConfig`**, so the crawler picks a strategy from config
  instead of special-casing:

  | `access_mode` | Enumerate | Fetch | Brands (from §C6) |
  |---|---|---|---|
  | `public_sitemap` | sitemap → WP-REST fallback | unauth httpx + browser UA | GL, AH, COC, CAD, MHD, TDRC |
  | `public_cloudflare` | sitemap → WP-REST fallback | unauth httpx, **watch for challenge under load** | AR (CF present, pass-through) |
  | `wp_rest_auth` | WP-REST `/pages` (creds from env) | httpx + Basic auth | available for RR/GL if a public crawl is ever blocked |
  | `unknown` | — | — | DBH (no URL) |

  *Why an enum, not per-brand code:* the crawler branches once on `config.access_mode`; adding a brand
  is a TOML file, never a code change. The auth path reuses `WordPressService`'s `_get_headers` logic
  (ported to httpx), reading creds from env only — never stored in the repo (matches the M0 probe).
- **Crawl-access abstraction:** a small `CrawlStrategy` (one class per mode) behind
  `enumerate_pages(config)` / `fetch_pages(config)`. M0's functions already are this shape; formalize
  it as a strategy selected by `access_mode`. Checks receive a `ParsedPage` and are blind to how it was
  fetched.
- **Rate-limit / politeness per brand:** `CrawlRules` already per-brand; MHD (8,761 pages) and RR
  (7,827) get the same polite defaults but a longer wall-clock window (§C5).

### B3. Canonical-phone sourcing — from the NAP sheet, per-brand/per-location (unblocks check #4)

**Today (M1):** `canonical_phones` in `config/gl.toml` = 2 national numbers; everything else flags →
over-flags GL's legitimate local numbers. §C1 read the real NAP layout and it *does* support a generic
model. Design:

- **New model `CanonicalNumbers`** (per brand): `{ national: [ {number, channel: SEO|PPC} ],
  per_location: { location_key: number }, stale_retired: [number] }`.
- **Source = the live NAP sheet** (plan ID `1AU_wNukif…`), read once per run via the reused
  `SheetsService` (§A5). Column mapping from §C1: current number = col **I** on brand-header rows
  (RR/GL carry two — SEO Target + PPC Target — via col **H** label; all others one); per-location = col
  **I** on location rows; **stale_retired = col BD ("OLD Hardcoded 800 #s")** + the BJ–CZ legacy-string
  header list as a brand-agnostic blocklist. Brand grouping is **positional** (forward-fill the last
  brand token in col E) — encode that, don't assume a brand column.
- **Classification the phone check emits** (replaces bare "non-canonical"): each found number →
  **clean** (matches national or a per_location number), **stale-retired** (matches col BD → an
  ERROR: a retired number still live), or **unknown** (not in any set → WARNING for review).
- **This is what turns GL's noise into signal.** §C1 shows **800-692-9850 is GL's OLD number** — the
  site-wide flag M1 produced is a *real stale-number finding*, and the 949/562 numbers are GL's
  *current* per-location numbers (→ clean).
- **As-built (P1, `23dfac6`/`43f9653`):** the phone check classifies against the NAP snapshot
  (`nap.canon_for`), collapses each site-wide number to ONE finding (identity = number, sources =
  pages — the broken_links shape), and `nap.py` is a phone-scoped check-version component. On the GL
  250-sample this cut 489 undifferentiated flags → ~250 stale-retired (real) + 239 clean/suppressed.
- **KNOWN LIMITATION — per-location numbers are validated BRAND-WIDE, not per-page.** A per_location
  number is treated as clean anywhere on the brand, so **a valid number rendered on the wrong
  location's page (e.g. Long Beach's 562 on an Orange County page) will NOT be flagged.** The full fix
  needs a page→location map (slug/geo inference — §C1/`LOCATION_MAP`), which is not a cheap/reliable
  add today (most pages aren't location pages; city→location-number isn't 1:1; the auditor doesn't
  read the sheet's geo columns yet). Named and deferred, not forgotten — a limitation we've named is
  honest; one we've forgotten is the Check #2 trap. This caveat also ships in the report (summary.json
  `phone_scope_caveat`).
- **Freshness:** the classification uses a snapshot of the **verified** live sheet (`NAP_SHEET_ID`
  confirmed 2026-08-03 — it reads as "NAP Phone numbers / UTM Codes / DBAs" with the `NAP (Current)`
  tab this module parses),
  carried in every finding's suggestion and in summary.json. The live-sheet read (D2) swaps
  `grid_from_xlsx` → `grid_from_sheet` with **no parse edit** (so no phone check-version churn); an
  identical-numbers swap is a version no-op because the ruler hashes canonical VALUES, not the source.

### B4. Phase-2 AI layer — bolts on, never contaminates v1

**Composition boundary (the hard line):** Phase 2 is a separate package `auditor/ai/` that **consumes**
`ParsedPage` + `AuditReport` and **emits** `Finding` (severity always advisory: a review queue with
confidence, never an auto-verdict). v1 has zero imports from `auditor/ai/`. The CLI gains
`audit --brand gl --ai` (off by default); with it off, the tool is byte-for-byte the v1 tool.

Pipeline (`auditor/ai/`):
1. **Block extraction + dedup (mandatory, per §C2):** split each page's normalized visible text into
   blocks; hash each; **check each distinct block once, then fan the verdict back to every page that
   contains it.** §C2 measured **62% of GL content is cross-page boilerplate** → dedup cuts AI volume
   ~65%. Without this, Phase 2 spends two-thirds of its tokens re-reading the same 168 nav/footer/CTA
   blocks. This is the single design decision that makes Phase 2 affordable.
2. **Proper-noun allowlist (kills false positives):** built from `LOCATION_MAP` (§C4, ~30 CA
   proper nouns + 28 counties) **+** the county CSV in the Fetcher (15 states) **+** live-site
   slugs/H1s **+** the brand guides (brands, execs, clinical terms). US English / AP style. Hard rules
   from the brand guide: banned phrase "District Behavioral Health Network"/"network"-near-DBH; banned
   word "addicts"; person-first constructions must NOT be flagged.
3. **Claude pass (Batch API + prompt caching):** each unique block → spelling / grammar / punctuation /
   context-scope ("national page says county"). System prompt + allowlist are the **stable cached
   prefix** (prompt caching, ~0.1× on reads); the block is the volatile suffix. Structured output
   (`output_config.format`) → `{issue, confidence, snippet, suggestion}`. Model tiering: **Sonnet 4.6
   or Haiku 4.5 for the high-volume spelling/grammar pass; escalate to Opus 4.8 only for context-scope
   judgement** (the county-vs-country class). Decision D3.
4. **Context-scope check:** the page declares its own scope (H1 / meta / URL say "national" or name a
   city); compare the body's geographic claims against that scope — catches "county" where "country"
   is meant *from the live page alone*, no sheet needed. The near-certain version is the optional
   **sheet↔live diff** (compare rendered page to the source gSheet cell) — a cheap deterministic catch,
   gated on the gSheet connector (Decision D2).
5. **Output = review queue only.** Confidence-scored `Finding`s (severity INFO/WARNING), never
   auto-applied. Client preference is **over-flag** [n].

**Cost model (real numbers, from §C2 + §C3):** deterministic v1 = **$0 AI**. Phase-2 *full first-pass
across the 8 confirmed brands* (~25–30k pages), with block-dedup + Batch API (50% off) + prompt-cached
system/allowlist:

| Model tier (batch) | ~Input cost | ~Output cost | ~Full-run total | Note |
|---|---|---|---|---|
| Haiku 4.5 | ~$18 | ~$20 | **~$40** | viable for bulk spelling/grammar with a strong allowlist |
| Sonnet 4.6 | ~$52 | ~$58 | **~$110** | recommended default for the bulk pass |
| Opus 4.8 | ~$87 | ~$97 | **~$185** | reserve for context-scope cases |

Order-of-magnitude, ~35M deduped input tokens; **without dedup it's ~3× these numbers.** Incremental
re-runs (changed blocks only, via §B1 diff) are a small fraction. Verify with `count_tokens` on a real
sample before committing (do not ship on these estimates). **Verdict: Phase 2 is affordable; block
dedup is what makes it comfortable, and it is a go on cost.**

> **SUPERSEDED by §D (2026-07-31).** The sketch above was written from a single-brand sample before
> the 9-brand crawl existed. §D is the real design: measured dedup on 6 brands, real page counts,
> current pricing, and the allowlist sized against actual sources. Where the two disagree, §D wins.

### B5. Cache-skip / incremental runs — design pass (no code; decisions needed)

**The correction that reframed this:** the compact projection *cannot* serve a check-version bump —
recomputing structure/phone/placeholder needs page content (full headings, `raw_html`, `visible_text`)
the projection deliberately drops. Persisting content to fix that = the multi-GB problem the whole
architecture avoids. So "cache everything, refetch nothing" was never on the table. What *is* on the
table is narrower and honest.

**Live probe result (drives the whole design):** GL returns **no `ETag`** but a **`Last-Modified`** on
every page, and a conditional GET (`If-Modified-Since`) returns **`304`** (Cloudflare+WP honors it,
`cf-cache-status` HIT and MISS both). A 304 is headers-only — **change detection is server-provided and
near-free; we don't download-and-hash to know a page changed.** This supersedes content-hash as the skip
signal *where the host supports it* (content_hash stays as the report/diff key + fallback + a cross-check).

**Q1 — what's actually cacheable, and what invalidates it:**

| Item | Cacheable? | Invalidated by |
|---|---|---|
| `link_urls` (which links a page contains) | **Yes** | page content change (304→200) |
| `title` / `meta_description` / `h1_text` (dup-barrier inputs) | **Yes** | page content change |
| `content_hash`, `Last-Modified`, `status`, `final_url` | **Yes** | page content change / server |
| Intrinsic findings (structure/meta/phone/blank/placeholder) | **Yes, but** reusable only if page unchanged **AND** that check's version unchanged | content change **or** check-version bump (two invalidators) |
| Enumeration set (sitemap vs REST = the 845) | **No — recompute** (cheap: ~16 sitemap + paginated REST). We *want* to recompute; detecting drift is the check's job | n/a |
| **Link liveness** (probe 200/404) | **No — fundamentally not** (extrinsic; a target rots on its own schedule) | time — must re-probe every run |

The load-bearing line: **link *targets* are cacheable; link *liveness* is not.** And intrinsic findings
carry *two* invalidators, so they're only reusable when both hold.

**Q2 — link-rot cadence (real, not fake):** a **link-only run** = read cached `link_urls` across all pages,
dedupe, probe each — **zero page fetches**. That's ~3,959 probes vs a full crawl, genuinely cheap, viable
weekly. Limit: it re-probes *known* targets only, so it catches **rot** of existing links, not **new**
links added since the last crawl. So the pairing is: full crawl finds new links + rot; link-only catches
rot fast between crawls.

**Q3 — conditional GET vs content-hash: 304 wins on GL (probed).** Incremental = conditional GET all pages;
`304` (+ same check-version) → reuse cached projection; `200` → re-parse+check. Risks to note, not ignore:
(a) `cf-cache-status: HIT` means Cloudflare may serve a cached `Last-Modified` — a 304 could say "unchanged"
if Cloudflare hasn't refreshed though the origin changed; but we audit the **live (Cloudflare-served)** page,
so that version is arguably the truth. (b) `Last-Modified` semantic reliability (does it actually move on a
content edit?) needs one confirmation on a known-edited page before we trust it as the sole signal —
belt-and-suspenders: keep `content_hash` and periodically re-hash to catch a lying `Last-Modified`.

**Q4 — run taxonomy (three modes, genuinely different cost, not over-structuring):**
1. **Full crawl** — GET all, full checks + enumeration + link probe. Finds everything incl. new pages/links. The baseline + periodic ground-truth.
2. **Incremental** — conditional GET all; `304`+same-version → reuse cached projection; `200` → re-check; re-run enumeration (cheap); re-probe links. "What changed since last run."
3. **Link-only** — probe cached targets, no page fetch. Cheapest; catches rot; misses new links.
A **check-version bump forces mode 1** (bodies needed; the 304 shortcut is invalid for the changed check).

**Q5 — Jake's cadence is the question that decides whether we build ANY of this.** If the tool runs
**monthly / on-demand-before-launch** (the likely shape for content QA), incrementality is **not needed** —
~5,000 polite requests once a month is nothing, and the correct design is "the full crawl *is* the tool;
YAGNI the cache-skip." If it runs **nightly**, incrementality matters and modes 2–3 earn their keep. The
design hinges on this **a lot** — it's the difference between building three run-modes and building none.
**This is an escalation for Syed → Jake, not a guess.**

**Decisions needed (in order):**
1. **Jake's cadence** (escalation). If monthly/on-demand → build none of B5; the re-baseline just persists the
   light projection below for optional future use and we stop. If nightly → build modes 2–3.
2. **Re-baseline cache schema (my recommendation):** persist the **light projection** —
   `{content_hash, last_modified, status, final_url, link_urls, title, meta_description, h1_text}` —
   **but NOT `intrinsic_findings`.** This is forward-compatible for *both* incremental (via `Last-Modified`/304)
   and link-only (via `link_urls`), stays small, and avoids the stale-findings trap (caching findings with no
   read-side version check is the "partial persist worse than none" case). Findings-cache only if we commit to
   mode-2 nightly. Confirm the schema.
3. **`Last-Modified` reliability**: accept `content_hash` as the cross-check, or hold for a confirmation probe
   on a known-recently-edited page first?

**Does this design need the baseline's output?** No — it's driven by the 304 probe (done) + code analysis +
Jake's cadence (escalation). The only empirical input (`Last-Modified` reliability) is a 2-page probe, not a
full crawl. So design-first (option 2) holds; the re-baseline then persists the approved schema.

**`last_modified` is more than a cache key — it's a future FINDING (flagged, not v1).** A rehab facility page
whose `Last-Modified` is years old is *stale content on a healthcare SEO site whose entire business is those
pages ranking*. So `last_modified` earns its place in the projection regardless of which cadence Jake picks —
even if cache-skip is never built, a "stale content" check reads it. Out of v1 scope; noted here so it isn't
rediscovered later.

**Status:** design decided (Syed) — cadence **held pending Jake** (may make cache-skip moot); schema = **light
projection** (persisted by the re-baseline via `audit.write_projection_cache`, no findings); `Last-Modified`
trusted for 304-skip with `content_hash` as the periodic cross-check. No skip/read logic built until cadence
is known.

---

## PART C — R&D (evidence behind Part B)

### C1. NAP sheet real structure (read `jake_sites.xlsx` "NAP (Current)", 2026-07-02 snapshot)
Per-brand blocks: one national header row (col E = brand token, col I = number) + per-location rows
(col I = local number). **RR & GL uniquely carry two national rows** (SEO Target + PPC Target, marked
in col H). Col **BD = "OLD Hardcoded 800 #s"** (per-row retired). BJ–CZ = header-only legacy
find-replace strings (global blocklist). Brand grouping is positional (forward-fill col E). **A generic
`{national[], per_location{}, stale_retired[]}` model is buildable** (§B3). Confirmed: GL's site-wide
**800-692-9850 is a col-BD OLD number** → the M1 phone flag is a real stale-number finding; 949/562 are
current per-location numbers.

### C2. Templated-content dedup (measured on 60 real GL pages)
**62.1%** of character volume is cross-page boilerplate (blocks on ≥30% of pages), **29.8%** unique.
Block-level dedup cuts AI-check volume **65.3%** (913K → 317K chars for 60 pages). Median unique content
= 2,565 chars/page. Top repeaters are exactly nav CTAs + footer + license strings. → **Block dedup is
mandatory for Phase-2 affordability** (§B4). Conservative floor — deeper geo pages are more templated.

### C3. Phase-2 pricing/limits (via the claude-api skill, current)
Batch API = **50% of standard**; prompt-cache reads ~**0.1×** input, min cacheable prefix 4,096 tok
(Opus). Per-1M input: Opus 4.8 **$5**, Sonnet 4.6 **$3**, Haiku 4.5 **$1** (output 5×). Structured
outputs supported for the review-queue JSON. → cost model in §B4.

### C4. Proper-noun allowlist source (`scripts/geo_location_mapper.py`)
`LOCATION_MAP` is a thin static index (5 entries, CA-only) yielding ~30 proper nouns + 28 CA county
names + 15 state abbrevs; a `city_county_mappings` dict adds ~10 cities. **Real breadth is in the
Fetcher's `data/integrated_all_drugs_county.csv`** (national counties, 15 wired states). → allowlist =
LOCATION_MAP seed + that CSV + live slugs/H1s + brand guides (§B4).

### C5. RR/MHD-scale performance (reasoned)
Scale ceiling is **MHD at 8,761 pages** (bigger than RR's 7,827), ~3.2× GL. The crawler is async +
bounded concurrency, so wall-clock scales ~linearly with a fixed politeness floor → a full brand fits
the 2–5am window at safe concurrency; no architectural change needed to *fetch*. **The real 3× pressure
is memory + the link probe:** M1 holds all `ParsedPage`s + findings in memory and probes a capped 400
links. At 8.8k pages that's fine for findings but **the incremental cache (§B1) becomes mandatory, not
optional** — you don't re-fetch+re-parse 8.8k pages nightly; you diff. Recommend: stream pages through
checks (don't retain every `ParsedPage`), and lift the link-probe cap with the full unique-link set
(dedup already collapses ~1,579 uniques from 250 pages). bs4 stays adequate; revisit selectolax only if
a full MHD parse profiles as the bottleneck.

### C6. Crawl access for the 7 unconfirmed brands (read-only probe)
**8 of 9 brands are confirmed crawlable** (public sitemap + public WP-REST): AH (71 pages), COC (1,504),
CAD (1,233), AR (472, Cloudflare present but pass-through — watch under load), MHD (8,761), TDRC (19),
plus RR/GL. **DBH is the only unknown — no base URL in the sheet (open question #3).** → §B2 access-mode
table; the "all 9 crawlable" assumption is *not* made — DBH stays `unknown`.

---

## Open questions still gating (see CLAUDE.md §9 + new)
Unchanged: #3 DBH URL, #4 PPC subdomain URLs, #5 written H2/H3 rules, #6 AR canonical phone confirm,
#7 [m] do-not-flag terms. New from this pass: **#8** the 845 sitemap/WP-REST delta scope. Plus the
three decisions below.

---

# Part D — Phase 2 (spelling / grammar / context), made real

> Design pass, 2026-07-31. **No code written.** Supersedes the §B4 sketch. Every number below was
> measured this session against the live sites or read from an authoritative source — nothing is
> carried over from the earlier estimate. Decisions Syed still owes are collected in §D8.

## D1. What Phase 2 is actually for — grounded in the client's own examples

Read the ClickUp thread (`86baawd2a`) before designing anything: Jake's reported defects are the
spec. They fall into **three classes, and only one of them needs AI.**

| Jake's actual words | Class | Who catches it |
|---|---|---|
| *"spelled Heroin wrong"* | **spelling** | **AI (Phase 2)** |
| *"homepage meta description doesn't mention Tennessee, only CA and FL"* | **context/scope** | **AI (Phase 2)** |
| *"Among 9861 people in 2023, overdose outcomes d compared to 2022 by 5.96%"* — *"a word is missing… reads as broken merge-field text"* | empty-variable artifact | **deterministic** (§D2) |
| *"There are at least outpatient drug rehab programs available within of California"* | empty-variable artifact | **deterministic** |
| *"In , the involving substances such as"* / *"Among a population of in , overdose outcomes d compared to by %."* | empty-variable artifact | **deterministic** |
| *"In Los Angeles during [blank], there were 5 news reports"* | empty-variable artifact | **deterministic** |
| *"fix [acf field=ge,:"* | literal ACF token | **already shipped** (v1 `placeholder`) |
| phone link errors on the COC facility page | phone | **already shipped** (v1 `phone`) |

Jake's standing ask in that thread — *"how easy is it to add these issues to the automated QA tool
to catch similar items like these for us in the future?"* — is the mandate for this phase.

**The design consequence is the most important decision in Part D:** the largest, most embarrassing,
most *frequently reported* class (empty-variable artifacts) is **not** a spelling/grammar problem and
must not be sent to a model. It is a pattern problem, it is free, and it is deterministic.

## D2. Empty-variable artifacts — a v1-class check, built first (no AI)

v1's `placeholder` check catches the *literal* token (`[acf field=geo]`). It does **not** catch the
case where the token resolved to an **empty string** and left grammatically broken text behind —
which is what Jake actually keeps reporting. Signatures, all from his examples:

- **Dangling preposition before punctuation:** `within of California`, `in , the`, `during [blank],`
- **Orphaned comma/percent:** `In , there were`, `compared to by %.`
- **Truncated word from a cut merge field:** `overdose outcomes d compared to` (`d` = the stub of
  `decreased`/`increased`)
- **Missing numeral where the sentence demands one:** `There are at least outpatient drug rehab
  programs` (the count vanished)

Implement as `auditor/checks/empty_slot.py` (v1 package, not `ai/`): a small ordered pattern set over
`visible_text`, each with a fixture drawn from Jake's verbatim examples. Severity ERROR — these are
visible on the customer-facing page. **This is cheap, exact, has zero token cost, and closes the
single most-reported defect class.** It should ship before any AI work begins.

## D3. Proper-noun allowlist — the make-or-break, and it is buildable today

Without this, a spellchecker flags *Costa Mesa*, *buprenorphine*, and *Laguna Niguel* on every page
and the review queue is worthless. Sources, **all confirmed on disk this session**:

| Source | Yield | Confirmed |
|---|---|---|
| Fetcher `data/integrated_all_drugs_county.csv` (read-only) | **1,181 distinct counties across 51 state codes**, format `Baldwin County, AL` | 10,621 rows read |
| Fetcher `scripts/geo_location_mapper.py` `LOCATION_MAP` | ~30 CA proper nouns + county/region names (thin, CA-only — a seed, not the breadth) | read |
| **Our own crawl cache** (`cache/*/pages.json`, 9 brands) | **3,702 distinct capitalized tokens** from titles + H1s across **16,216 pages** — brands, cities, facilities, drugs, clinician names | computed |
| Brand guides (`.tmp_dd/brand_guide_*.txt`) | brands, execs, clinical terms, banned words | on disk |
| NAP sheet | brand + facility names | on disk |

**The third row is the discovery.** We do not need to source a drug/facility dictionary — we already
crawled the entire network, and every proper noun the sites actually use is sitting in our own cache.
Build the allowlist from *our own corpus*, not from an external word list.

Build rule: a term enters the allowlist if it appears **capitalized in ≥2 brands' titles/H1s**, or is
in the county CSV, or is in a brand guide. Cross-brand agreement is what separates a real proper noun
from one site's typo. Store as `auditor/ai/allowlist.json`, regenerated by a script, **committed** so
a run is reproducible and diffable — and hashed as a check-version component (§D6) so a rebuild
correctly rule-changes Phase-2 findings instead of silently resolving them.

## D4. Templated-content dedup — measured on 6 brands

Cost hinges on this. Measured this session (spread samples across each sitemap, sentence-level
blocks, "boilerplate" = a block present on ≥30% of sampled pages):

| Brand | pages sampled | boilerplate share | **dedup saves** | unique tokens/page |
|---|---|---|---|---|
| MHD | 11 | 64.5% | **56.7%** | ~2,071 |
| AR | 4 | 78.5% | **54.7%** | ~3,168 |
| RR | 25 | 50.1% | **49.7%** | ~4,595 |
| GL | 25 | 43.6% | **47.8%** | ~3,692 |
| CAD | 25 | 35.7% | **34.6%** | ~3,243 |

**Dedup roughly halves the bill on every brand measured** (35–57%). It is mandatory, exactly as the
sketch said — but now on 6 brands rather than one.

**Honest limitation, stated because it moves the cost number:** `unique tokens/page` is computed as
*distinct blocks in the sample ÷ pages sampled*, and small samples find less sharing than the full
corpus does. So these per-page figures are an **upper bound**; the real corpus-wide number is lower,
and dedup gets *better* with scale, not worse. The cost model below is therefore a ceiling.

*(Measurement note: the first run of this reported 0% boilerplate on every brand. That was my bug —
`ParsedPage.visible_text` is space-normalized (7 newlines in 27k chars), so paragraph-splitting
produced one block per page. Fixed to sentence-level splitting; numbers above are the corrected run.
`spike/dedup_measure.py`.)*

## D5. Cost model — current pricing, real page counts

**Pricing (via the `claude-api` skill, current — not from memory):** Opus 5 **$5 / $25** per 1M
in/out · Sonnet 5 **$3 / $15** (intro **$2 / $10** through 2026-08-31) · Haiku 4.5 **$1 / $5**.
**Batch API = 50%** of standard. **Prompt caching: writes 1.25×** (5-min TTL) or **2×** (1-h TTL),
**reads ~0.1×**. Minimum cacheable prefix is model-dependent: **Opus 5 512 tok, Sonnet 5 1,024,
Haiku 4.5 4,096** — our system+allowlist prefix clears all three, so caching works on any tier.

**Corpus (union-scope page counts from the actual runs):**

| GL | RR | MHD | CAD | COC | DBH | AR | AH | TDRC | **total** |
|---|---|---|---|---|---|---|---|---|---|
| 3,591 | 7,827 | 15,640 | 1,236 | 1,509 | 578 | 474 | 161 | 21 | **~31,037** |

*(AR = real pages only; its 10,740 duplicate `/city-data/` doorways are excluded by config, and CAD's
3,933 hidden doorways are not in its sitemap. Auditing 14,700 copies of two pages would be waste.)*

At the measured **ceiling** of ~3,700 unique tokens/page → **~115M input tokens** for a full
first pass; output is structured findings (most blocks yield nothing) — budget ~15% → ~17M.

| Tier (batch, cached prefix) | Input | Output | **Full first pass** |
|---|---|---|---|
| **Haiku 4.5** | ~$58 | ~$43 | **~$100** |
| **Sonnet 5** (intro rate) | ~$115 | ~$85 | **~$200** |
| **Sonnet 5** (standard) | ~$173 | ~$128 | **~$300** |
| **Opus 5** | ~$288 | ~$213 | **~$500** |

**Verdict: affordable, and cheaper than it looks** — these are ceilings (D4), a mixed-tier pipeline
(D6) puts most volume on the cheap tier, and incremental re-runs only re-check *changed* blocks via
the existing run-diff, which is a small fraction. **Do not commit on these numbers**: run
`count_tokens` against a real 200-block sample first, exactly as the earlier sketch said.

## D6. Pipeline, model routing, and output

`auditor/ai/`, consuming `ParsedPage`/projections and emitting `Finding`. v1 has zero imports from it;
`audit --brand gl --ai` is off by default, so with the flag off the tool stays byte-for-byte v1.

1. **Block extraction + dedup** (§D4) — check each distinct block once, fan the verdict to every page
   carrying it.
2. **Deterministic pre-pass** — `empty_slot` (§D2) and the banned-phrase matcher (§D7) run **before**
   any model call. Free, exact, and they remove the highest-volume defects from the AI's input.
3. **Model pass, routed per check type — never hardcoded** (already decided). `config/ai.toml`:
   ```toml
   [models]
   spelling      = "claude-haiku-4-5"   # high volume, allowlist does the heavy lifting
   grammar       = "claude-sonnet-5"
   context_scope = "claude-opus-5"      # the judgement call — see D7
   ```
   Read at runtime, hashed into the check-version. Rationale for the split: spelling on a strong
   allowlist is near-mechanical; context-scope is the one that needs real reasoning.
4. **Prompt shape:** system prompt + allowlist = the **stable cached prefix**; the block is the
   volatile suffix. Batch API for the bulk pass. Structured output (`output_config.format`) →
   `{issue, confidence, snippet, suggestion}`.
5. **Output = review queue, never an auto-verdict.** Emits `Finding` at INFO/WARNING with a
   `confidence` field; client preference is **over-flag** [n]. Nothing is ever auto-applied.

**Check-version discipline (the trap):** every Phase-2 input must be a hashed component or vanished
findings will read as `resolved` instead of `rule_changed` — that is the Check #2 pathology we have
already been bitten by twice. Components: `src:ai/*.py`, the **allowlist file**, the **banned-phrase
list**, and the **model IDs** from `config/ai.toml`. Scope them to the Phase-2 checks in
`diff.py::_CHECK_COMPONENT` so a model swap doesn't churn v1 findings.

## D7. Context-scope detection — how the page declares its own scope

The "national page says county" class (and Jake's *"meta description doesn't mention Tennessee"*).
The page tells us its scope in four places we already parse — **no gSheet needed for v1 of this**:

| Signal | Source | Example |
|---|---|---|
| **URL path** | crawl | `/drug/rehab/california/orange-county/costa-mesa/` → city scope |
| **H1** | `ParsedPage.headings` | *"Drug Rehab in Costa Mesa, CA"* → city |
| **Title / meta description** | `ParsedPage` | *"…CA and FL"* → state list |
| **Sitemap grouping** | RR now groups sitemaps by template (Naveed, ClickUp) | template class |

Derive a **declared scope** `{level: national|state|county|city, place: str}` from those, then ask the
model one narrow question per block: *does this text make a geographic claim inconsistent with the
declared scope?* That is a far cheaper and more precise prompt than "find context errors", and it is
checkable against the allowlist (§D3 knows which names are counties vs cities).

**The near-certain version is the sheet↔live diff** (compare rendered text to the source gSheet cell)
— deterministic, no model, catches county/country instantly. It is gated on the gSheet connector,
which is still **not wired** (we read a snapshot). That remains Decision D2 from Part B.

## D8. Decisions needed from Syed

1. **Ship `empty_slot` (§D2) first, before any AI?** It is the most-reported defect class, it is
   deterministic, free, and ~a day of work. I recommend yes — it also shrinks the AI input.
2. **Model routing defaults (§D6)** — I propose Haiku 4.5 for spelling, Sonnet 5 for grammar,
   Opus 5 for context-scope. Approve, or set different tiers? (All runtime-config, so this is a
   default, not a lock-in.)
3. **Cost ceiling to authorise for the first full pass** — ~$100 (Haiku-heavy) to ~$500 (all-Opus).
   I recommend authorising the **token-count validation run first** (~$0, `count_tokens` only), then
   a single-brand pilot on GL, then the network pass.
4. **Banned-phrase source.** Confirmed terms today are from the brand guide: *"District Behavioral
   Health Network"* / *network* next to DBH, and *addicts* (person-first is mandatory —
   person-first constructions must never be flagged). **I could not find an explicit do-not-say list
   in Jake's ClickUp comments** — his June-18 items are PDF attachments I can't read, and open
   question **[m]** ("do-not-flag terms") is still marked *unsure* by the client. So: do you have the
   list, should it come from a gSheet tab we read live, or do we ship with the brand-guide terms and
   add to it? **I will not invent the list.**
5. **Scope of the first AI pass** — all 9 brands, or GL only as a pilot? (GL has the best
   ground-truth fixture and a sent report to compare against.)

---

## D9. RESULT — Phase 2 was tested end to end and is NOT being shipped

**Status: measured, documented, shelved. Do not rebuild this without reading this section.**
Two models, four full-corpus runs, 473 + 282 + 216 findings hand-adjudicated by independent judges
with an adversarial refute pass. The deterministic layer beat it on this corpus. The numbers and the
reasons are below so nobody spends $111 rediscovering them.

### What was tested

The full §D6 pipeline on Gratitude Lodge: boilerplate excluded, blocks deduped, a 2,488-term
proper-noun allowlist (§D3) in a prompt-cached system prompt, structured JSON output, degenerate
findings filtered. Corpus 2,086–2,404 unique client-authored blocks from 25 pages. Both candidate
tiers: `claude-haiku-4-5` and `claude-sonnet-5`.

Adjudication was deliberately not self-graded: findings went to independent classifier agents against
a fixed rubric, then every finding called REAL went to a second adversarial pass instructed to refute
it and to default to "not real" when uncertain. The reported precision is that strict survivor rate.

### The numbers

| | Haiku 4.5 | Sonnet 5 |
|---|---|---|
| findings (2,086 blocks, restricted prompt) | 121 | 95 |
| survived adversarial refute | **5 (4.1%)** | **6 (6.3%)** |
| surviving defect types | misspelling 2, broken_word 2, space_before_punct 1 | misspelling 2, missing_space 2, broken_word 1, space_before_punct 1 |
| projected network cost (batch input) | **$33** | **$111** |

Cost is per-model because `count_tokens` is model-specific: the identical corpus is 66.8M tokens to
Haiku and 111.2M to Sonnet. Do not price one model with another's token count.

An earlier, looser prompt ("spelling, grammar, punctuation") scored higher on paper — Sonnet 55%
strict — but its output was dominated by word-choice rewrites the client would reject
("relapse probability" → "relapse risk", "has tons of fun" → "offer many fun"). Restricting the
prompt to six mechanical defect types removed the noise and revealed the real yield.

### Why the deterministic layer wins on THIS corpus

Every class that survived adversarial review is already caught for free:

| Surviving class | Already caught by |
|---|---|
| misspelling | `checks/misspelling.py` (exact list, ERROR, incl. slug/URL) |
| broken word ("program s") | `checks/empty_slot.py` `truncated_word` |
| missing space / space before punctuation | now a non-issue — it was a `parse.py` artifact (see below) |
| missing distance unit ("within 15 of Costa Mesa") | `checks/empty_slot.py` `missing_unit` — this was 4 of the 6 real AI findings in the first pilot and is a regex, not a judgement |

The AI tier's marginal contribution over the deterministic layer, on a corpus the deterministic
layer has already swept, is a handful of findings per thousand blocks at $33–111 per network run.

### The result that keeps the door open — recall is genuinely good

Precision is the wrong number to judge the tier by alone, so recall was measured against seeded
ground truth (`spike/seeded_control.py`): one word per real page block corrupted, split into
misspellings the client had already reported versus **novel typos on no list anywhere**.

**Both models: 13/13, including 9/9 novel.** Every seeded word was verified absent from the
allowlist first, so the prompt could not have suppressed them. Zero proper-noun false positives
across four runs.

So the model **does** find typos nobody has reported — the "next `paramFount`" case. It simply does
not beat regexes on a corpus we have already swept deterministically. If the corpus changes — a new
brand, freshly spun content that v1 has never seen, or a client request for genuine prose quality
rather than mechanical defects — this conclusion should be re-tested, not assumed to still hold.

### What the exercise actually bought us

The pilot's real value was as a **detector for defects in our own extraction**. Chasing its false
positives found four content-fabricating bugs in `parse.py`, all now fixed and gated:

1. `get_text(" ")` welded an `<h2>` into the `<p>` below it — a sentence not on the page.
2. Emitting a space after every inline node turned `<strong>phone rings</strong>.` into
   `"phone rings ."` — the ORIGINAL code had this too, so it was in every report shipped before.
3. Hidden content (`display:none`, `[hidden]`) was read as visible: **20.9% of GL's `visible_text`,
   39.8% of AR's**. This mattered far beyond the AI layer — `visible_text` is what the blank/thin
   check measures, so hidden text could make an empty ACF section look populated.
4. A whitespace-only text node was dropped, fusing `here:` + `Outpatient`.

All four are pinned by browser-verified fixtures in `tests/fixtures/parse/` (see `test_parse_fixtures.py`).
Re-running every `visible_text` check across 8 brands after the fixes changed **no** finding on any
brand — the shipped v1 numbers stand.

### Known, deliberately unfixed: static vs interaction-state hiding

`_strip_hidden` skips any CSS selector containing `:`, which was aimed at `:hover`. That also
discards structural pseudo-classes, so content inside a **closed Elementor accordion**
(`.e-n-accordion-item:not([open]) .e-con{display:none}`) is still extracted.

This is **not** a regex tweak and was not patched. Accordion/FAQ content is real client copy revealed
on click, and check #7 explicitly audits FAQ/accordion blocks — blanket-stripping every closed
accordion would blind the auditor to a section the client asked us to check. The correct fix
distinguishes **static hiding** (strip: the reader can never see it) from **interaction-state hiding**
(keep: revealed on click), and needs a decision about which side accordion content falls on.

---

## D10. RESULT — the dictionary spellchecker was measured and is NOT shipped as an ERROR check

**Status: measured, documented, parked behind `SPELLCHECK=1`. Do not rebuild without reading this.**
Second documented negative result after D9, and for a different reason: D9's AI layer lost to
regexes; this one loses to the corpus itself.

### What was built

`checks/spelling.py` — pyspellchecker 0.9.0 (160,572-word offline US-English dictionary, chosen on
measurement: `unknown()` on 8,000 words is 12ms because detection is a set lookup) layered under
three vocabularies: the 2,488-term proper-noun allowlist, a mined domain vocabulary, and the
brand's own words. Scope was body text, meta title, meta description and slug.

### The number, hand-classified in full

150 GL pages, **every one of the 78 distinct flagged words classified by hand** — not a sample:

| | |
|---|---|
| findings | 98 (0.7/page, 27% of pages) |
| precision by distinct word | **7/78 = 9%** |
| precision by finding count | **12/98 = 12%** |

The seven real ones: `txreatment`, `noteable`, `lorazapam` and `lorazepan` (both misspellings of the
drug *lorazepam*), `acetaminop`, `ency`, `alcoholusedisorderaud`.

An earlier run scored 1,873 findings before three fixes — curly apostrophes read as misspellings
(38% of findings), staff surnames spellchecked (27%), and plurals/compounds the dictionary lacks
(27%). Those were real bugs, all now tested. **The 95% cut to 98 findings did not make it
shippable**, which is the point of recording it.

### Why tuning cannot fix the rest

The residue is **rare pharmaceutical and clinical vocabulary appearing on exactly one brand**:
isotonitazene, solriamfetol, pitolisant, methemoglobinemia, armodafinil, xerostomia, mephedrone,
pentedrone, loperamide, estazolam. The cross-brand rule excludes single-brand words *by design*,
because single-brand rarity is precisely where a typo lives. **On this corpus those two populations
are the same population.** Rehab marketing copy is saturated with rare drug names.

The obvious filter was tested and rejected: reporting only words with a confident distance-1
correction lifts precision to 23% **but loses 4 of the 7 real findings, including both drug
misspellings** — `lorazapam` cannot be corrected because *lorazepam* is not in the English
dictionary either. That trade discards the highest-value findings a rehab site can have.

Adding a pharmaceutical vocabulary (RxNorm-style) would remove roughly 44 of the 71 false positives
→ about **26%**. Still three-in-four wrong, still cry-wolf by this project's standard, for real
effort. Not pursued.

### Recall, which is the part worth keeping

Seeded with Connor's seven confirmed misspellings (`spike/spelling_recall.py`):

* **Dictionary alone: 5/7.** Both misses are words the English dictionary legitimately CONTAINS —
  `programing` (frequency 145, an accepted variant of "programming") and `heath` (frequency 467,
  open uncultivated land). Not contamination, and no dictionary checker can flag either.
* **Combined with `misspelling.py`'s known list: 7/7.**

**That is the structural argument for keeping both checks.** The known list is not a weaker version
of the dictionary — it covers a class dictionaries **cannot reach**, because the misspelling is
itself a real word.

### What was salvaged instead

Three of the seven real findings were never misspellings at all: `acetaminop` (truncated mid-stem),
`ency` (a broken word fragment) and `alcoholusedisorderaud` (run-together). Those are **text
corruption** — template and data-pipeline defects — and are detectable structurally with no
dictionary. That half moved into `empty_slot`, which already owned `truncated_word`. See D11.

### The mining heuristic, which is reusable

Cross-brand frequency alone is NOT evidence: the brands share templates, so one author's typo
propagates and looks like vocabulary (it trusted `behavorial`). What works is **context diversity** —
real vocabulary appears in many different sentences (measured `angeles` 1,064 distinct contexts,
`meth` 879, `adhd` 518) while copied junk sits at exactly 1 (every lorem-ipsum word scored 1).
Correction confidence supports it but cannot lead: `behavorial->behavioral` and `comorbid->morbid`
are identical on that signal. Recorded in `auditor/ai/build_vocab.py`.


## D5. Enumeration when a site has no index (DBH, 2026-08-08)

**Decision: a per-brand static URL list is a first-class enumeration source, not a workaround.**

DBH was replatformed to **headless WordPress behind Next.js** during the 2026-08-08 run (verified:
`X-Powered-By: Next.js`, `x-nextjs-prerender: 1`, 188 `/_next/static` references, and 18,402
`elementor` markers still in the markup — WordPress went headless, it did not go away). Its DNS
also disappeared for ~2 days across the cutover; see `docs/incidents/2026-08-08-dbh-dns-outage.md`.

`sitemap_index.xml`, `sitemap.xml`, `wp-sitemap.xml`, four other sitemap variants, `robots.txt` and
`/wp-json/wp/v2/pages` **all return 404**, each serving the same 902 KB app shell. Both of our
enumeration sources are therefore gone. The content is unaffected — it is prerendered, still
Elementor-shaped, and 30 of 30 sampled pre-migration URLs still return 200 — so every check works;
only *finding the pages* is broken.

`BrandConfig.urls_file` supplies the list, consumed by `crawl.enumerate_pages` **after** sitemap and
WP-REST both yield nothing. Implementation note worth keeping: the WP-REST branch used to `return`
even on an empty result, which made the fallback unreachable for exactly the brand it exists for.
It now falls through only when a `urls_file` is configured, so the other eight brands' enumeration
metadata is unchanged.

**The limitation is permanent and must be stated in any report covering DBH: a static list cannot
discover pages added later.** There is no index on this platform to diff against, so new pages are
invisible and will silently never be audited. `enumerate_pages` returns
`cannot_discover_new_pages: True` in its meta so this travels with the data rather than living only
in someone's memory.

## D11. Rule-based grammar engine — pilot (LanguageTool, grammar-only)

**Written BEFORE the measurement, deliberately, so the result is read against the prior rather than
rationalised after it.** D9 (AI) and D10 (dictionary) both failed; this is the third and final
approach we will measure. Whatever the outcome, grammar stops here.

### The hypothesis, corrected by research (2026-08-11)

The premise was that a rule-based engine is a *third* thing: deterministic rules with stable IDs and
no judgement. Research showed that is only half true. **LanguageTool's spellchecker reproduces D10
exactly** — run locally, it flags `isotonitazene`, `solriamfetol`, `pitolisant`, `buprenorphine`.
So the pilot deliberately **disables `MORFOLOGIK_RULE_EN_US`** and measures only the non-spelling
rules (their/there, a/an, word repetition, agreement, unpaired quotes, spacing). That configuration
— rule-based grammar without spelling, on clean professional web copy — is the one setup for which
**no published precision figure exists**, which is the only reason the pilot is worth running.

### Priors, recorded up front

| Source | Figure | What it measures |
|---|---|---|
| Wikimedia, English Wikipedia | **precision 0.524**, their stated *lower bound* | LanguageTool on clean, professionally edited, entity-dense prose — the closest published analogue to our corpus |
| Wikimedia, error-free featured articles | **0.79–0.80 false positives per sentence** (en-US), cut >10× by switching to `en` | names the *misspelling* rule as the main FP driver — the reason we disable it |
| BEA-2019 detection | precision 0.53 / 0.41 / 0.27 (levels A/B/C), **recall 0.05–0.08** | learner corpus where nearly every sentence has an error, so its precision is an **upper** bound for our error-sparse pages |
| Human eval of sampled suggestions | 70–90% judged correct, **but only after post-processing filters** | the only published number touching our 80% bar |

**So the expected outcome is a narrow, high-precision, low-recall check** — something that fires
rarely and is right when it does. **That is a shippable shape, not a disappointment**: it is exactly
what `misspelling.py` already is (a curated known-list, ~zero false positives, deliberately narrow).
A rare-but-right check earns its place; a chatty one does not.

### Gate design — volume BEFORE precision

Fail fast on the cheap question. Hand-classification is the expensive part, so it is not spent
until the check has proven it says anything at all.

1. **Volume gate.** Grammar-only findings per page on real GL pages. **Under ~0.05/page the check
   is too quiet to ship and the pilot stops there** — that is roughly one finding per twenty pages,
   below which the sheet gains nothing worth a Java dependency.
2. **Only if it clears:** precision by hand-classification of every finding, no sampling.
   **Bar unchanged: 80%, proper nouns clean.**

Constraints carried from the research: run offline via a local server, call **`/v2/check` over
`httpx`** — **`language_tool_python` is GPL-3.0 and must not be imported into client work**. The
2,488-term allowlist loads via `spelling_custom.txt` (file + restart), which drove pharma false
positives to zero in testing; it matters less here since spelling is disabled, but it is the same
lever if spelling is ever revisited.

### The other lever, recorded whether or not it is used: UMLS SPECIALIST Lexicon

**This is the more interesting thread and it attacks the failure that actually killed D10.** A
clinical misspelling detector built on the **UMLS SPECIALIST Lexicon** — a real biomedical lexicon,
not a general English dictionary — achieved **PPV 0.9057 / 0.8979 on 76,786 real clinical notes**,
above our 80% bar, and critically **its residual false positives were *not* drug names**.

D10 died because rare pharmaceutical vocabulary and genuine typos were the same population on this
corpus: `isotonitazene` and `lorazapam` are both single-brand rare tokens. A biomedical lexicon
separates them by *knowing the drugs* rather than by inferring rarity — which is the only mechanism
proposed so far that could break that tie.

**It is not part of this pilot** (different problem: spelling, not grammar; and it needs a UMLS
licence and a corpus-loading spike). It is recorded here so that **if the grammar pilot dies, the
next person finds the one remaining lever on spelling written down instead of rediscovering it.**

### Result — MEASURED 2026-08-14. **REJECTED. Grammar stops here.**

150 GL pages sampled on the audit's own seed; 130 returned body text. LanguageTool 6.6 local
server, grammar-only exactly as specified above, `/v2/check` over `httpx`.

| | |
|---|---|
| pages checked | 130 |
| body text | 2,233,139 chars (mean 17,177/page) |
| findings | **1,153** |
| findings per page | **8.87** |
| volume gate (0.05/page) | **cleared — inverted** |
| **precision (generous)** | **99 / 1,153 = 8.6%** · excluding our own artifacts **10.6%** |
| bar | 80% — **FAILS, and is worse than D10's 9%** |

**The volume gate cleared in the wrong direction.** It was set to catch a check that says nothing;
this one says 8.87 things per page — ~275,000 findings across the network. The gate was the right
instrument pointed at the wrong failure mode, and the answer arrived anyway.

Classification is **deliberately generous to LanguageTool**: every rule that could plausibly fire on
a genuine defect is counted REAL, including borderline comma advice.

**Five rules are 83% of the output, and four of them are wrong:**

| n | % | verdict | rule | what it actually flagged |
|---|---|---|---|---|
| 528 | 45.8% | FALSE | `MISSING_COMMA_AFTER_YEAR` | `Updated May 11, 2026 Authored By:` — a correctly formatted byline. Its own message opens *"Some style guides suggest…"* |
| 195 | 16.9% | FALSE | `YOUR` | *"typically **your** prescribing physician"* → suggests "you're". Flatly wrong |
| 115 | 10.0% | artifact | `PHRASE_REPETITION` | `Learn About Gratitude Lodge` + heading `Gratitude Lodge Rehab…` joined by OUR block extraction |
| 68 | 5.9% | artifact | `ENGLISH_WORD_REPEAT_RULE` | topic-list labels concatenated the same way |
| 49 | 4.2% | FALSE | `EN_MULTITOKEN_SPELLING_TWO` | **`Los Alamitos`** — a California city |

**Two findings matter more than the headline number.**

1. **It fails the D9 way, not the D10 way.** 46% of output is a *style opinion about commas in
   dates* — the check editorialising about house style on text that is not wrong. That is precisely
   what made the AI layer unshippable, reproduced by a rule engine. Disabling `STYLE`,
   `REDUNDANCY`, `COLLOCATIONS`, `TYPOGRAPHY` and `CASING` did not stop it, because
   `MISSING_COMMA_AFTER_YEAR` lives under punctuation.
2. **Proper-noun spelling came back through a different door.** `MORFOLOGIK_RULE_EN_US` was
   disabled and verified silent on `isotonitazene`, `solriamfetol`, `pitolisant` and staff
   surnames. `EN_MULTITOKEN_SPELLING_TWO` then flagged **`Los Alamitos`** anyway. Killing the
   spellchecker by ID does not kill spelling — the D10 failure has more than one entrance.

**Runtime independently disqualifies it.** Measured on the running server: **517 ms per 1,000
characters** steady-state → **13.2 s for our median 25,517-char page** → **113 h single-threaded,
14.2 h at 8 workers** for 31,000 pages, on top of the existing ~7 h crawl. The pre-pilot research
claimed ~1.1 h at 8 workers and asserted it had measured our real page size; it was wrong by ~18×.
Recorded because the lesson generalises: *a claim to have measured something is not a measurement.*

**Read against the priors recorded above**, this is not a surprise — Wikimedia's 0.524 on cleaner
prose was already far below our bar, and we landed at 8.6%. The hoped-for outcome was *narrow and
right*; the actual outcome is *broad and wrong*.

**Honest caveat:** 220 findings (19%) are artifacts of our own `body_text` joining adjacent blocks,
not page defects. Excluding them entirely, precision is 10.6% — still below D10 and nowhere near
80%. Fixing the extraction would not change the conclusion.

### Where this leaves grammar and spelling — for good

Three approaches, three measurements, one answer: **on this corpus, automated grammar and
general-purpose spelling do not clear the cry-wolf bar.** D9 (judgement), D10 (rarity), D11 (rules).
What ships instead is what already ships: `misspelling.py`, a curated list of confirmed
misspellings, deliberately narrow, effectively zero false positives — a check that fires rarely and
is right when it does.

**CORRECTION (2026-08-14): the UMLS recommendation above was WRONG, and is withdrawn.** Measured
rather than assumed: the SPECIALIST Lexicon **misses `isotonitazene`, `solriamfetol` and
`pitolisant`** (three of the four terms that killed D10), and — decisively — it **whitelists 232 of
4,308 known English misspellings (5.39%)**, including `accidently`, `occured` and `developement`,
because it is a descriptive lexical resource that records non-preferred variants. Loading it as an
allowlist would **suppress the very errors the tool exists to find**. The published PPV 0.90 also
does not transfer: that method excluded rare tokens by construction, which is why drug names were
not a false-positive source — not because the lexicon covered them.

**The replacement, verified locally on 2026-08-14: the openFDA NDC Directory** — CC0 public domain,
no licence or account, 136,942 records refreshed daily, **23,687 distinct drug tokens**, covering
**7 of 12** D10 killers and containing **0 of 13** known misspellings. Its gaps are principled and
define the residual risk: it lists FDA-registered drugs, so **illicit/novel substances**
(`mephedrone`, `pentedrone`, `isotonitazene`) and **clinical conditions** (`xerostomia`,
`methemoglobinemia`) are absent — and both appear in this corpus. Since that residue is exactly what
sank D10, **the honest prior is that a fourth attempt is more likely to fail than succeed.** Full
reasoning and sources: `docs/plans/2026-08-14-product-design.md` §13.

**Reproducing this:** `spike/lt_volume_gate.py`, raw output in
`spike/lt_volume_gate_result.json`. LanguageTool is NOT a repo dependency and nothing in the
shipping path imports it.

---

## D12. Corruption detection — the fourth spelling/grammar attempt, and the one that SHIPPED

**Result: six families shipped, 21 findings on 876 GL pages, 59/60 hand-classified true positives
(98%). Seven families measured and rejected.** Ticket 86bapv5yn's headline — grammar and spelling —
is now partially closed by deterministic checks, with no model, no dictionary judgement and no
review queue.

### Why this attempt differed from D9, D10 and D11

All three predecessors asked **"is this text CORRECT?"** — a judgement. D9 editorialised about word
choice; D10 hit 9% precision because rare clinical vocabulary is indistinguishable from a typo when
the only signal is "not in my dictionary"; D11 was researched and abandoned for the same reason.

This attempt asks **"is this text BROKEN?"** — a structural fact. It is the question `empty_slot`
already answers profitably: that check knows nothing about grammar, it knows `"within  of
California"` has a hole in it. Corruption is what a broken CMS actually produces, and it is
detectable without a model.

### Method

876 live GL pages / 1.7M body words, fetched 2026-08-23 (seed 20260823, URLs from the existing
`cache/gl/pages.json` so the sitemap was never re-hit) and parsed with the REAL
`auditor.parse.parse_html`. Measuring against home-grown extraction would have measured a corpus
production never sees. Every surviving hit was then **re-fetched and confirmed present in the
RENDERED text**, so nothing shipped is an artifact of our own parser.

Note: **nothing in `cache/` retains page text** — `resume.done.jsonl` and `pages.json` store
projections only (`visible_chars` is a count). Any future text work must fetch its own corpus.

### SHIPPED (all in `checks/empty_slot.py`, one batched commit)

| family | hits | precision | what it catches |
|---|---|---|---|
| `missing_space` | 12 | 12/12 | `deaths.These` — a sentence boundary that lost its space |
| `doubled_word` | 5 | 5/5 | `from from`, `opioid opioid` |
| `lorem_ipsum` | 1 page | 1/1 | **30 blocks of Latin live on `/local-business-page-dev/`** |
| `repeated_letters` | 1 | 1/1 | `medically-asssited` |
| `stacked_punctuation` | 1 | 1/1 | `addictive?.` |
| `shattered_text` | 1 | 1/1 | `Al ways fo llow t he inst ructions pr ovided by y our do ctor` |

Plus **12 corpus-mined misspellings** in `checks/misspelling.py::MINED`, kept apart from `KNOWN` so
that list's "the client confirmed this" guarantee still means what it says. Mining rule: a token
appearing 1-3 times in the corpus that is ONE EDIT from a token appearing 100+ times. Measured
11/12 = 92%. Among them **`graditude` — the brand's own name, misspelled on a live page.**

**The rarity half is load-bearing and cannot be replaced by dictionary frequency.** Measured:
substituting "common in the dictionary" for "common in this corpus" drops precision to **42%**,
because `abilify`, `aleve`, `concerta`, `permanente` and `rogan` are each one edit from a common
word, and only "appears at most 3 times in the corpus" removes them. This is the precise reason D10
failed and this did not.

### REJECTED — measured, not guessed. Do not re-propose without new evidence.

| family | hits | precision | why it fails |
|---|---|---|---|
| `space_before_punct` | 2,315 | **0%** | **Parser artifact.** Raw HTML reads `insurance</strong>, easing` — no space at all. Our parser inserts a separator when closing an inline element. |
| `mid_sentence_capital` | 11,640 | ~0% | English capitalises proper nouns mid-sentence constantly (`near Seal Beach`). |
| `stranded_fragment` | 4,054 | ~0% | Staff names and labels (`Amy Leifeste`, `Licensed Professionals`). |
| `unterminated_block` | 1,449 | ~12% | Feature-list cards legitimately carry no full stop. |
| `verbless_sentence` | 130 | ~17% | Headings-as-paragraphs, and it fired on Latin for the wrong reason. Also the only family needing to know what words MEAN — which is the failure mode of D9. |
| `welded_pair` (2-part) | 22 | **0%** | `undertreated`, `paddleboarding`, `recreationally`. This validates the existing 3-part / 16-char floor on `run_together`. |
| `mixed_case_midword` | 4 | 0% | `eReaders` (a real word), `drugInfo` (a URL). |
| `stacked_separator`, `long_dots`, `digit_in_word` | 0 | n/a | Shipped anyway where free; the corpus contains no instances, so precision is unmeasured. |

### The two lessons worth carrying

1. **Detect on BLOCK text, never `visible_text`.** `visible_text` concatenates separate elements. Run
   against it, `missing_space` fired **3,837** times instead of 12 — a heading ending `costs.`
   beside a link reading `Verify` is indistinguishable from `costs.Verify`. There is deliberately
   **no whole-page fallback**: a page exposing no body landmarks is one these families stay silent
   on. Under-reporting on a few pages is the cheap failure; inventing defects is the expensive one.
2. **Verify every new text finding against the raw HTML before believing it.** Block-level detection
   guards against BLOCK seams and does nothing about INLINE ones. That single check is what caught
   the 2,315-finding `space_before_punct` family before it shipped.

**Reproducing this:** the corpus builder, prototypes and measurement harness are `build_corpus.py`,
`detectors.py`, `measure.py` and `measure2.py` — written to a scratchpad, deliberately never added
to the repo, because nothing in the shipping path may import them.

---

## D12b. Per-brand precision of the mined-typo check — measured on all nine sites

**Pooled reporting would have hidden two brands.** Precision varies with what a site is ABOUT, not
with the signal, so it is reported per brand and never averaged.

Corpora fetched 2026-08-23/24 from each brand's existing `cache/<brand>/pages.json` (no sitemap
re-enumerated), parsed with the real `auditor.parse.parse_html`, and scored with the SHIPPED
`misspelling.from_audit` — with `MINED` lifted out of `KNOWN` so every brand is scored as a site
nobody has mined yet.

| brand | pages | body words | findings | true | precision |
|---|---|---|---|---|---|
| AH | 179 | 204,853 | 3 | 3 | **100%** |
| GL | 876 | 1,712,396 | 12 | 11 | **92%** |
| COC | 1,520 | 2,800,847 | 9 | 8 | **89%** |
| DBH | 400 | 1,290,314 | 9 | 8 | **89%** |
| AR | 510 | 921,807 | 7 | 6 | **86%** |
| CAD | 1,239 | 2,107,243 | 35 | 30 | **86%** |
| RR | 1,192 | 3,596,141 | 11 | 9 | **82%** |
| MHD | 257 | 578,866 | 2 | 0 | **0%** — suppressed in production, see below |
| TDRC | 19 | 4,455 | 0 | — | n/a — below the minimum corpus, see below |

Highlights, all live: **`Distric Behavioral Health`** (DBH's own name, 1,535 occurrences),
**`Renaisance Recovery`** (a sister brand's name), **`Alcohol Rehab Calfornia`** (CAD, in a
heading), `Lexpro`/`Panax`-adjacent drug errors, `Palm Beach Coutny, FL` (a misspelled address),
and a large family of shattered-text fragments (`t reatment`, `j ourney`, `b ehavioral`).

### What happens when a brand lands below the bar

1. **MHD — already suppressed, by a gate that exists for another reason.** MHD always runs capped
   at 900 pages, so `partial_sample=True` and `from_audit` returns nothing. Verified: the same
   corpus yields 2 findings ungated and **0** gated. Its two candidates were `Ray Mears Boulevard`
   and `Urbana` — a street and a city — which is exactly what a directory of treatment centres is
   made of. No new policy was needed; the sampling gate already does the right thing.
2. **RR — fixed by a veto, not by suppression.** RR carries pop-culture copy (Jim Carrey, Krist
   Novoselic), and person names are one edit from common words as easily as typos are. It measured
   75%; adding the plural veto (`percocets` is not a misspelling of `percocet`) took it to 82%. The
   two residual false positives are both personal names. **A name-adjacency veto was tried and
   rejected** — vetoing a capitalised token beside another capitalised token would also have
   discarded `Graditude Lodge` and `Renaisance Recovery`, the two most valuable findings in the
   whole set.

### The dictionary gap was the real problem, not the signal

AR first measured 67%. Its false positives were `rehydration`, `destress` and `melia` — two of
them ordinary English absent from pyspellchecker. Two fixes, both measured:

* **Affix veto** — a known prefix or suffix on a known word IS a real word. The stem floor of **6**
  is measured, not chosen: at 5, `recovey` decomposes to `re`+`covey` and a real typo vanishes; at
  7, `destress` slips back through.
* **`/usr/share/dict/words` was tested and REJECTED.** It closes the same gap and wrongly vetoes
  nothing — but it is not installed by default on Ubuntu 24.04, the deploy target. A check whose
  vocabulary differs between laptop and server would emit findings in one place and not the other,
  and this tool reports a DIFF: that variance would surface as phantom new/resolved rows every run.
  Determinism beat the extra coverage.

### The place-name veto, and what it costs

A token immediately before a US state code is a place name. Silenced per brand: GL 43, AH 25,
AR 2,795 (AR carries city listings). Of those, the number that would otherwise have been REPORTED
is small — 2, 1 and 8 — and all but one were genuine places or real words.

**The exception was real and is now handled.** AH ships `Palm Beach Coutny, FL` — a misspelled
"County" inside an address. Two separate mechanisms were hiding it: the place veto, and the rarity
test (an address lives in a template, so it repeated 4 times, one past `RARE_MAX`). Address
STRUCTURE words (county, city, boulevard, suite …) are now exempt from both, because nowhere in the
United States is a town called County. The obvious alternative — "report it when the correction
also appears before a state code" — was measured and rejected: it rescues `coutny` but re-admits
`Centre, AL`, `Taylors, SC` and `Gardena, CA`.

### The minimum corpus is a property, not a gap

The signal needs words that RECUR. Measured, "words occurring 100+ times":

| TDRC | AH | MHD | AR | GL |
|---|---|---|---|---|
| 4,455 words -> **0** common words | 204,853 -> 209 | 578,866 -> 628 | 921,807 -> 957 | 1,712,396 -> 1,165 |

At TDRC's size **not one word occurs 100 times**, so no near-match can form and the check produces
nothing at all. That is the signal declining to guess on a site too small to support it — not a
missed defect. Anyone asking why the smallest site has no spelling findings should be given this
number.

### A cross-brand process finding: five sites are written in British English

`british_spelling` collapses several British forms on one site into ONE finding, because it is one
editorial decision, not N typos. It fired on **AR, DBH and RR**, and single British words also
appeared on **COC** (`characterised`) and **CAD** (`recognise`) — five of nine brands, with
`behaviour` shared across AR/DBH/RR and `personalised` across AR/DBH.

**That is a shared writer or content source, not five coincidences,** and it belongs in the rollup
as a process finding: fix the source, or new pages keep arriving the same way. The client's stated
standard is US English (jake_doc answer [k]).

Detection is by TRANSFORMATION rule (`our`->`or`, `ise`->`ize`, `lling`->`ling`, `ence`->`ense`,
`ae`/`oe`->`e`), never a word list, so it needs no maintenance. The rules are anchored to real
suffixes after a measured miss: a bare `ll`->`l` classified **`vallium` -> `valium`** — a misspelled
drug — as a British spelling.

---

## D13. A defect the database cannot show you — the dropped-field trap

**Found 2026-08-28 while mapping the cross-domain redirect bug. Recorded because the lesson is
general and the failure mode is silent.**

### The specific bug

`audit.py` parses each page as:

```python
parsed = parse_html(r.text, page_url=canonical_url(r.url), base_url=r.final_url or r.url, ...)
```

`parsed.url` is the **requested** URL. `final_url` is used only to resolve relative links and is
**never stored** — not on `ParsedPage`, not on the projection, not on the finding.

So when a sitemap URL 301s onto **another brand's domain**, the page that comes back belongs to that
other brand but is audited under *this* brand's configuration. It is not a per-check bug. Every
brand-scoped check is wrong for that page at once: the phone canon and `cross_brand_dial`, the
sister-brand check (the other brand's own name reads as an intruder), `schema`, `scope`,
`misspelling`, `meta`.

Confirmed real: TDRC's `/review-us/{code}` addresses are deliberate 301s to GL, RR and CAD review
pages — **7 of 19 sampled pages**. Verified live: `/review-us/gl-long-beach` → 301 →
`gratitudelodge.com/review-us/long-beach-ca/`.

### The lesson, which is the part worth keeping

**The blast radius is unmeasurable from stored data, and the query that looks like it measures it
returns a confidently wrong answer.**

"Which findings sit on a URL that is not the brand's own domain?" returns **zero** — for every
brand, across the whole database. That looks like proof the bug affects nothing. It is an artifact:
`parsed.url` is always the requested URL, which by construction is always on the brand's own domain.
The field that would reveal the defect was dropped before anything was written.

So: **when a projection drops a field, absence of evidence in the database is not evidence of
absence.** A query over derived data can only ever see what the derivation kept. This is the same
family as the partial-read traps already documented here — a partial sitemap read that silently
undercounts, and a partial CSS read that cannot know what a page hides. In all three the danger is
identical: the system returns a clean-looking answer to a question it is structurally incapable of
answering, and nothing about the output says so.

The practical rule: before trusting a query that reports "this does not happen", check that the data
could have recorded it happening.

### The fix (one change, not five)

Carry `final_url` onto the projection, and at the **audit level** skip brand-scoped checks for any
page whose final registrable domain differs from `config.base_url`. Not in each check —
`render/markup.py` and `checks/schema.py` already carry a working registrable-domain helper to copy.

**And the skip must not be silent.** A page listed in a brand's own sitemap that redirects to a
different brand's domain is itself a finding: the sitemap is advertising pages the brand does not
own. TDRC has 7 of 19. Skipping those pages quietly would replace a false finding with a hidden one,
which is the trade this project never makes.
