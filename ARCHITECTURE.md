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
- **Freshness caveat:** the classification uses the **2026-07-02 snapshot** (`NAP_SHEET_ID` UNVERIFIED),
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
