# CLAUDE.md — District Site Auditor

> Authoritative context for this repo. Authored 2026-07-13 (session 1) by consolidating the
> ClickUp ticket + thread, the contractor implementation plan (`jake_doc.txt`), the two client
> brand guides, the client NAP/sites sheet, and a direct code investigation of the existing
> GeoData Fetcher. Every fact below was confirmed on disk or via a tool this session — where a
> fact is still unknown it is listed under **Open questions**, not guessed.

---

## 1. Role & hard rules (non-negotiable)

You are the **sole engineer** on the District Site Auditor. You do real engineering, not demo
code. One way to do each thing — no parallel implementations "just in case."

**Hard rules — obey these verbatim:**

- **Sole agent, real engineering, never assume or guess a fact** not confirmed on disk or via a
  tool you actually have — if something's missing, surface it, don't invent it (you already did
  this correctly once; keep doing it).
- **Read before you write.** Don't break existing working code when adding new code — confirm
  existing functionality still works after every change.
- **Session continuity:** at the start of every session, read `SESSION_STATE.md` first.
  **Write it after every meaningful step — every commit, every finding, every decision.** Not
  "continuously" as a vibe, not at some threshold. After each step, before starting the next one.
  **The test: if this session died right now, could a fresh one continue with zero loss?** If no,
  you are already late. Writing state costs seconds; losing a session costs hours. There is never
  a reason to defer it.
- **Git identity for this repo:** `sydashir` / `meetashirr@gmail.com` (set repo-local, not global).

Corollaries: never invent a URL, config shape, or library API surface. If it isn't in this file,
`SESSION_STATE.md`, or the existing code, stop and ask. When crawl access or any external
dependency blocks you, stop and report immediately — do not silently work around it.

---

## 2. What we're building

A **standalone Python batch tool** that crawls the **live WordPress sites** of the District
Behavioral Health brands and emits a **per-brand CSV/JSON report** of content-QA issues — each row
carrying the page URL, issue type, location, a snippet, and a suggested fix. It runs as a batch
job (not an interactive web app) so a multi-thousand-page crawl is never trapped in a web process.

It **closes the loop after WordPress import**: the existing GeoData Fetcher validates the *source
gSheet before import*; this tool audits the *rendered live page after import* (see §8).

**Source of the ask:** ClickUp `86bapv5yn` — *"Build automated AI QA tool for grammar, spelling,
and broken link detection"* (assignee: Syed Ashir; High priority; due ~2026-08-04; Sites field =
GL, RR, DBH, MHD, AH, COC, CAD, AR, TDRC). Scope was discussed in `86baawd2a` (Jake Heinrichs,
June 17–30, 2026); Ashir confirmed feasibility and proposed a standalone tool (chosen) vs.
extending District AI.

---

## 3. Sources of truth (where facts live)

| Source | Location | What it authorises |
|---|---|---|
| Primary ClickUp ticket | `86bapv5yn` (app.clickup.com/t/86bapv5yn) | The mandate + Sites custom field |
| Discussion thread | `86baawd2a` ("More issues found on the sites (6-5-26)") | Real issue catalog + COC phone bug + scope |
| Implementation plan | `~/Documents/workk/district/.tmp_dd/jake_doc.txt` (Google Doc: docs.google.com/document/d/1qkjFpz17v_Zhu02B0J20hpCkNcsjLOOoWpFD8en3IY4) | Architecture, stack, checks, milestones, client answers [a]–[n] |
| Brand guide (general) | `~/Documents/workk/district/.tmp_dd/brand_guide_general.txt` (docs.google.com/document/d/12zpqSAseohE1H5GHcf74pRdfvi1nvVVV5ogHGo0mEyM) | Phase-2 spelling/terminology allowlist |
| Brand guide (geo) | `~/Documents/workk/district/.tmp_dd/brand_guide_geo.txt` (docs.google.com/document/d/1E6BLBWzJik7a9kNcz1hY9dLK5_r8S-ZneLCQHpfyH14) | Per-section heading/content rules |
| NAP / sites sheet | `~/Documents/workk/district/.tmp_dd/jake_sites_tab.csv` (+ `jake_sites.xlsx`); canonical gSheet: docs.google.com/spreadsheets/d/1AU_wNukifVPc6yH7pvXW051llwOnf7RTDx9hZDVgF-c (**VERIFIED live 2026-08-03** — reads as "NAP Phone numbers / UTM Codes / DBAs", tab "NAP (Current)") | Per-brand base URL + canonical phone numbers |
| GeoData Fetcher (existing tool) | `~/Documents/workk/district/` (git repo, Streamlit app) | Pre-import QA logic; Check #2; ACF token helper to reuse |
| GL content fixture | `~/Documents/workk/district/.tmp_dd/problem_sheet.csv` | 161 GL CA drug-rehab pages w/ expected h1/meta/phone/slug — a validation fixture, NOT an issue list |

---

## 4. Architecture (from jake_doc.txt, locked)

Standalone new repo. Target layout:

```
site-auditor/                # (this repo: QA_district_Tool)
  config/                    # one file per brand: base_url, sitemap, canonical_phones, crawl rules
  auditor/
    crawl.py                 # sitemap enumeration + async fetch + content-hash cache
    parse.py                 # HTML -> DOM, text, links, headings, phones
    checks/                  # one module per check (pluggable)
      links.py  blank.py  phone.py  structure.py  meta.py
    report.py                # CSV + JSON writers, severity rollup, run-diff
    cli.py                   # audit --brand AH | --all | --all --since last-run
  cache/                     # content hashes for incremental runs
  reports/                   # dated, per-brand output
```

**Stack:** Python **3.12** (spec) — note this machine has 3.13.7; pin 3.12 in the build.
`httpx + asyncio` (concurrent crawl), `selectolax`/`BeautifulSoup` (parse), `Typer` (CLI),
`pydantic` (config + report models), `pandas` (CSV). Phase 2 adds `anthropic` SDK + `rapidfuzz`.

**Crawl approach:** (1) enumerate every live URL from `sitemap.xml` (fallback: WP REST
`/wp/v2/pages`); (2) async fetch with concurrency cap + polite rate-limit + retries — **be polite
to Cloudways/Cloudflare**; (3) **dedup links** — nav/footer/CTA repeat on every page, so collapse
to unique targets and check each once; (4) **incremental** — content-hash per URL; re-runs only
re-audit changed pages.

---

## 5. The checks

### v1 — deterministic (no AI, tuned for near-zero false positives)

1. **Broken links** — extract every `<a href>`, dedup, HEAD/GET each (internal + external); flag
   4xx/5xx, timeouts, and redirects that land on a 404. Per client: **flag all** (internal,
   external, and 301/302 redirects) and tag them.
2. **Blank / thin pages & sections** — flag empty/below-threshold visible text, missing `<h1>`,
   missing main content. **Key client need:** detect when a section's **ACF field is blank** —
   the page can *look* populated at a glance while an ACF section is actually empty.
3. **Phone numbers** — pull visible numbers + `tel:` links; flag missing, malformed,
   **display ≠ `tel:` mismatch**, or numbers not matching the brand's canonical number(s).
   **This is the flagship check** — the COC facility page shipped with wrong phone links that
   neither manual QA nor the old tool caught. DNI/CTM call-tracking swap is *not* audited yet;
   audit that the **hardcoded target number in each element** is correct.
4. **HTML / heading structure** — build the heading outline; flag: a heading tag wrapping
   body-length text (the "`blog_section` became an H2" bug), **multiple `<h1>`s** (rule: exactly
   one H1 per page), skipped levels (H3 before H2), empty/duplicate headings, two headings with no
   body between, unclosed/broken tags, and **any leftover `[acf field=...]` placeholders or stray
   markdown**. Also catch **empty-slot artifacts** — unpopulated variables that render as dangling
   text ("In ,", "…by %.") rather than literal tokens (the CAD homepage case). H4/H5 don't matter
   for SEO. H2/H3 hierarchy rules are known to HWA (see Open questions).
5. **Meta / title / URL basics** — missing/duplicate meta title or description, title length out
   of bounds, duplicate H1 across pages, malformed slug. (Spelling of these = Phase 2.) Sites use
   **Rank Math** (`rank_math_title` / `rank_math_description`).

### Phase 2 — AI / context layer (after v1 ships)

- **Claude pass** for spelling, grammar, punctuation, and context-aware scope errors
  (national→"county"/"country"). Reads page text + the scope the page declares; output is a
  **review queue with confidence + snippet, never an auto-verdict**.
- **Proper-noun allowlist** built from District's city/county/drug/facility CSVs + the brand-guide
  allowlist (brands, execs, clinical terms) to kill false positives.
- **Batched + prompt-cached** (Anthropic Batch API) with near-identical-template dedup for cost.
- **Optional gSheet connector** → cheap deterministic sheet↔live diff (the most reliable
  county/country catch) and pre-publish auditing.
- Spelling standard: **US English**, AP style. Client preference: **over-flag** (catch more, more
  human review). Hard banned phrase: **"District Behavioral Health Network"** / the word "network"
  next to DBH. Banned word: **"addicts"** (person-first language is mandatory — do not flag
  person-first constructions as errors). Full rules in the brand-guide summary in `SESSION_STATE.md`.

---

## 6. Brands & per-brand config (from jake_sites_tab.csv — verbatim)

Sitemap URLs are **not** in the sheet; derive as WordPress/Rank Math default and confirm per site
(Rank Math → `/sitemap_index.xml`). Base URLs and canonical phones are confirmed:

| Brand | Base URL | Canonical phone(s) | Notes |
|---|---|---|---|
| RR (Renaissance Recovery) | https://www.renaissancerecovery.com | SEO 866-330-9449 · PPC 866-923-1867 | ~6k pages; largest site |
| GL (Gratitude Lodge) | https://www.gratitudelodge.com | SEO 844-576-0144 · PPC 844-972-2859 | best ground-truth fixture (problem_sheet.csv) |
| AH (Addiction Hotline) | https://addictionhotline.com/ | (844) 575-6602 · (855) 701-0479 | |
| COC (Connections) | https://connectionsoc.com/ | 844-759-0999 | phone-bug motivating example |
| CAD (California Detox) | https://californiadetox.com/ | 888-995-4208 | empty-variable motivating example |
| AR (Alliance Recovery) | https://alliancerecovery.com/ | 844-287-8506 (+844-263-5113, (877) 511-4904) | multiple numbers — confirm canonical |
| MHD (Mental Health Directory / Inpatient finder) | https://inpatientmentalhealthfinder.com/ | (888) 376-8385 | |
| TDRC (The District Recovery Community) | https://thedistrictrecoverycommunity.com/ | (888) 871-2088 | |
| DBH (District Behavioral Health) | **not in sheet** | 888-707-6073 | parent brand; base URL unknown |
| SLN (Sober Living Nation) | **not live yet** | none | soberlivingnation.com when taken live |

PPC subdomains `help.rr` / `help.gl` / `gethelp.rr` / `gethelp.gl` are named in jake_doc but
**absent from the sheet** (URLs unknown). All sites are behind **Cloudflare** (set up by HWA).

---

## 7. Client decisions (jake_doc answers [a]–[n], resolved)

- **[a] Brand list:** DBH, RR, GL, AR, CAD, COC, AH, MHD, TDRC (+ SLN when live) + RR/GL PPC subdomains.
- **[b] Targets:** audit **live public sites** first; re-audit after staging→live push (cache/other surprises).
- **[c] Phones:** per the NAP sheet (§6).
- **[d] Priority:** audit all pages in 3 tiers — **nav pages (highest) → other geo pages → all other pages**.
- **[e] Access:** Cloudflare in front of all sites (HWA-managed) → allowlisting is possible.
- **[f] Crawl window:** **2a–5a PST** ideally.
- **[g] Broken links:** flag **all** (internal, external, redirects) and tag them.
- **[h] Blank/thin:** no intentionally-short pages; the real need is catching **blank ACF sections**.
- **[i] Phones:** all pages use CTM DNI swapping — **not** audited yet; audit the hardcoded target number per element.
- **[j] Headings:** exactly **1 H1** per page; H2/H3 rules known to HWA; H4/H5 irrelevant to SEO.
- **[k] Spelling:** **US English**.
- **[l] Style:** two brand guides per site (general + geo) — see §3.
- **[m] Do-not-flag terms:** *unsure* — still open (see Open questions).
- **[n] Flagging bias:** **over-flag**.

---

## 8. Relationship to the GeoData Fetcher

> Evidence-based connect-the-dots pass, verified adversarially against the real code at
> `~/Documents/workk/district` (all file:line refs are in that repo). Two earlier assumptions were
> **refuted** and are corrected here: the Fetcher's real output is the **Google Sheet, not
> WordPress**, and the `.tmp_dd/` files are **independent client docs, not tool exports**.

### Q1 — What the GeoData Fetcher does, end to end

It is a **Streamlit app that reads/writes a Google Sheet** — the sheet is its output artifact, **not
WordPress**. Per-sheet lifecycle (14 tabs; `streamlit_app.py:399-400`):

1. **Geo Data Fetch** (Tab 4 only, no CLI) — `GeoDataService.populate_geo_data`
   (`services/geo_data_service.py:1882`) reads the brand sheet, pulls facts from local CSVs + Google
   Places Text Search v1 + Anthropic Claude gap-fill, writes geo columns (~OE–OW) back to the sheet.
   Fabricates data on empty cells (random facility counts, hardcoded year 2024, addiction-pop =
   deaths×25).
2. **Spin / generate ACF-templated content** (Tab 1 or CLI `python main.py generate-acf-content`,
   `main.py:176`) — `ContentGenerationEngine.generate_content_for_sheet` spins template rows into geo
   rows via the Anthropic Batch API. **It substitutes only 5 `[acf field=...]` tokens** (`geo,
   near-in, state, full_geo, topic`) and **deliberately preserves every other `[acf field=...]` token
   for WordPress to resolve at render** (`docs/_deepdive_sections/90-architecture-dataflow.md:98`).
   Never reads the geo columns → spin is independent of geo-fetch.
3. **QA** (Tabs 3/5/6/7/8) — **purely advisory; gates nothing.** Every check writes only borders,
   notes, and JSON downloads; no downstream code reads a QA result
   (`docs/_deepdive_sections/90-architecture-dataflow.md:116`).
4. **Remove formatting** — HTML-sanitizes cell values.
5. **WP Sync** (Tab 9) — **read-only against WP**: `GET /wp-json/wp/v2/pages` to reconcile IDs, then
   writes the WP page IDs **back into the sheet** (col T). Does not publish pages.
6. **Infographic Generator** (Tab 14, newer than the deep-dive) — the one genuine WP **write**: builds
   a stats PNG per geo row and uploads it to the WP **media library** via `POST /wp-json/wp/v2/media`
   (`wordpress_service.py:683`). Images only — never page bodies/ACF.

**The actual sheet → live-page import is OUT OF THIS REPO** — done by an external (HWA) team,
mechanism **unverified** (presumed WP All Import). `create_page`/`update_page` in
`wordpress_service.py` exist but have **zero callers** repo-wide.

### Q2 — Is it the producer of the pages we audit? (confirmed, with a correction)

**Yes, indirectly.** The Fetcher produces the **sheet content** that becomes the live pages; the
sheet→WP publish is a separate external step. So auditing the **live pages** catches defects from
**any** stage: bad spin, unresolved `[acf field=...]` tokens, the external import itself, or manual WP
edits. Because the Fetcher's own QA **gates nothing**, known-bad content can flow straight through —
which is exactly why a post-publish live-page auditor is needed. Chain: **Fetcher → sheet →
(external import) → live page → OUR AUDITOR.**

### Q3 — Provenance of the `.tmp_dd/` files (assumption REFUTED)

**All five are INDEPENDENT Jake/HWA documents — NOT exports of any gSheet the Fetcher connects to.**
The tool **hardcodes no sheet/doc IDs**; every sheet ID is a runtime user input passed to
`gspread.open_by_key(sheet_id)` (`services/sheets_service.py:213`, `streamlit_app.py:196`). None of
the three plan IDs (NAP `1AU_wNukif…`, brand-general `12zpqSAseoh…`, brand-geo `1E6BLBWzJik…`) appear
in any `.py`/config/`.env` (grep = 0 hits). The tool has **no Google Docs API client**, so the two
brand-guide `.txt` files physically cannot be tool exports.

- `jake_sites.xlsx` / `jake_sites_tab.csv` = the client NAP/phone workbook, "Sites"/"NAP (Current)" tab.
- `brand_guide_general.txt` = exported "About the Business"/language Google Doc.
- `brand_guide_geo.txt` = exported "GEOPAGE BEST PRACTICES" Google Doc.
- `problem_sheet.csv` = a "Parts to Completion for writers" content export (medium confidence).

**Consequence:** they are point-in-time **2026-07-02 snapshots** of live Google sources. The auditor
should read those **live** sources itself (its own Sheets connection, using the plan IDs) — there is
**no connection to inherit** from the Fetcher; it's a shared source-of-truth spreadsheet we connect
to directly.

### Q4 — What to reuse from `services/` (don't rebuild)

| Need | Reuse | Where | Verdict |
|---|---|---|---|
| WP REST + auth | `WordPressService`: App-Password Basic auth `_get_headers` (`:199`), paginated `fetch_all_pages` (`:248`), `get_page_by_id` (`:787`), `test_connection` (`:868`), `SUPPORTED_SITES` (`:82`) | `services/wordpress_service.py` | **reuse-with-adaptation** — blocking `requests`; port auth+pagination to `httpx.AsyncClient` |
| Google Sheets I/O | `SheetsService`: service-account auth, `open_by_key`, `get_worksheet_data`→DataFrame, rate-limit backoff | `services/sheets_service.py:23,128,210` | **reuse-as-is** for one-shot sheet reads (sync; gspread not thread-safe) |
| ACF-token detection | `extract_acf_tokens(text)→list`, `ACF_TOKEN_RE` (`:56`), `LENIENT_TOKEN_RE` (`:64`), `ACF_SKIP_TOKENS` (`:71`), `validate_grid` (`:254`) | `services/geo_field_validator.py:74` | **import directly** for check #4 |
| HTML tag-balance | `HTMLValidator(HTMLParser).validate_html` (stdlib, no deps) | `services/acf_content_checker.py:15,60` | **copy as-is** |
| Text QA (words/readability/SEO) | `ContentChecker` (pure text) | `services/content_checker.py:10` | copy pure methods |
| Phase-2 compliance regexes | `ComplianceRule` list + `SOBER_LIVING_RULES` (medical-claim/payment/ownership) | `services/acf_content_checker.py:84`, `config/compliance_rules.py:80` | reuse for the AI/compliance layer |
| Sheet column map | `sheet_columns.py` (T–AE: page-id/slug/parent/taxonomies) | `config/sheet_columns.py` | reference when reading the sheet |
| Geo-name allowlist seed | `LOCATION_MAP` (FIPS/lat-lng/radius; city/county/region names) | `scripts/geo_location_mapper.py:33` | seed Phase-2 proper-noun allowlist |

**Build ourselves (nothing reusable):** phone extraction/validation (only a loose detector regex at
`acf_content_checker.py:146`; use the `phonenumbers` lib); a live **link checker** (none — only a
format-only `_is_valid_url`); **pydantic config models** (config is plain dicts); a generic
**JSON/CSV findings writer**.

**High-value discovery — authenticated staging access.** `config/settings.py:33-39` holds Cloudways
**staging** WP endpoints + App-Password env vars for two brands: **RR** =
`wordpress-1325662-4849104.cloudwaysapps.com`, **GL** = `wordpress-1325235-4846502.cloudwaysapps.com`
(`WordPressService.SUPPORTED_SITES` = `renaissance`, `gratitude` only). So we likely already have
**authenticated WP-REST access** to RR/GL staging — a real mitigation for the crawl-access +
sitemap-fallback risk (WP REST `/wp/v2/pages` enumerates pages even if a public sitemap is blocked),
and it fits the client's "audit staging, then re-audit live" flow. The other 7 brands aren't in the
registry (creds unknown).

### Q5 — All 7 Pre-Import Checker checks (`services/pre_import_checker.py`, `run_all_checks:114-330`, `total_checks=7`)

| # | Name | Method (file:line) | Catches |
|---|---|---|---|
| 1 | Missing Media | `_check_missing_media` `:353-490` | white content rows with text but no image/video/gallery (cols HI–OD) — WARNING |
| 2 | Geo Variable Consistency | `_check_geo_variable_consistency` `:492-609` | `{{var}}`/`[acf field=…]` tokens in geo templates with no matching GeoData column — ERROR (**this is "Check #2"**) |
| 3 | Slug Validation | `_check_slug_validation` `:611-728` | empty-with-content or non-`^[a-z0-9-]+$` slug (col W) — ERROR |
| 4 | Missing Page/Parent IDs | `_check_missing_ids` `:730-847` | missing WP Page ID (col T, ERROR) / Parent ID (col X, WARNING) |
| 5 | Blank Variables (T–AE) | `_check_blank_variables` `:849-966` | blank cells in cols T–AE (id/slug/parent ERROR, else WARNING) |
| 6 | Blank Content Cells | delegates → `TextContentQA.run_qa_checks` (`text_content_qa.py:97-402`) | empty templated content (ERROR) + markdown-syntax leakage: bold/italic/heading/list/code (ERROR) |
| 7 | Content Formatting | `_check_content_formatting` `:968-1210` | inline `style=` (WARNING), FAQ/accordion missing `<h3>+<p>` (ERROR), non-list Sources (WARNING) |

**Direct answers (verified):**
- **Phone numbers — NO check validates them** anywhere. Our phone check is **net-new**.
- **Heading structure / H1 count — NO** H1-count or hierarchy validation. Only incidental: markdown
  `#` flagged as leakage (`text_content_qa.py:30`); check #7 requires an `<h3>` inside FAQ blocks. Our
  heading-structure check is **net-new**.
- **Brand terms / banned words — NO** word-list/terminology check exists. Net-new (Phase 2).
- **Overlap:** our "blank/thin" overlaps checks #5/#6 and "meta/url" partially overlaps check #3's
  slug — **but those run on sheet cells pre-import; ours run on rendered live pages**, a different
  surface/stage, not duplicated code. Broken-links, phone, and heading-structure have **zero** overlap.

**The Check #2 bug** the plan referenced ("looks for `{{var}}` not `[acf field=var]`") was real —
originally matched only `r'\{\{([^}]+)\}\}'` (`pre_import_checker.py:566`) — and is **fixed** on branch
`add/alcoholism-wiring` via `geo_field_validator.extract_acf_tokens` (skip-list `geo, state, near-in,
full_geo, topic, city, drug, title`). Stale artifacts remain (docstring `:506`, message `:580`).

**Net:** the Pre-Import Checker and our auditor are complementary halves of one QA story — it guards
the **sheet before the external import**; we guard the **rendered page after it**. Reuse
`geo_field_validator` for the `[acf field=…]` check; the plan's other v1 checks (links, phone,
heading structure) are net-new.

---

## 9. Open questions still blocking / to confirm with Asif/Jake/HWA

1. **Sitemap URLs** per brand — not in the sheet. Derive `/sitemap_index.xml` (Rank Math) and
   confirm live (spike will test GL). If wrong, need the real path or WP REST fallback.
2. **Cloudflare crawl access** — biggest risk. Will plain `httpx` be challenged/blocked? If so,
   need a Cloudflare **allowlisted IP/User-Agent** or a WP application password. (Spike tests this.)
3. **DBH base URL** — parent brand has no URL in the sheet. Is DBH a crawl target, and what's its URL?
4. **PPC subdomain URLs** — `help.rr`/`help.gl`/`gethelp.rr`/`gethelp.gl` real hostnames.
5. **AR canonical phone** — sheet lists 3 numbers; which is the single canonical one?
6. **H2/H3 hierarchy rules** — "HWA knows" them; get them written down for the structure check.
7. **[m] Do-not-flag terms** — client "not sure"; needs a definitive allowlist for Phase 2.
8. **Blank-ACF detection mechanics** — is an empty ACF section reliably detectable from rendered
   HTML alone (empty container / dangling text), or do we need WP REST / gSheet diff to be certain?

---

## 10. Milestones (4-week plan; we STOP after the spike this session)

- **Spike (this session):** point a minimal crawler at one brand's `sitemap.xml`, pull ~50 real
  pages, run broken-link + heading-structure checks. De-risk crawl access. **Do not** start the build.
- **M0/Week 1:** repo scaffold, brand config, sitemap crawler + async fetch + cache; begin checks.
- **M1/Week 2:** finish the 5 deterministic checks + report writer + incremental cache + link
  dedup; run across RR/GL/AH; tune thresholds → **v1 shipped**.
- **M2/Week 3:** Claude spelling/grammar/context layer + proper-noun allowlist; batching + caching.
- **M3/Week 4:** false-positive tuning, cost/dedup controls, optional sheet connector; full run + handoff.

---

## 11. Working agreements

- Git identity is **repo-local**: `sydashir` / `meetashirr@gmail.com`. Verify with
  `git config --local user.name/user.email` before the first commit.
- `SESSION_STATE.md` is **gitignored** (working memory). `CLAUDE.md` is **tracked**.
- Tools available this session (verified): Playwright MCP (`mcp__playwright__*`), Context7
  (`mcp__context7__*`), ClickUp MCP (`mcp__claude_ai_ClickUp__*`), Google Drive MCP, Superpowers
  skills, GSD skills, WebFetch/WebSearch, Workflow orchestration.
- Crawl politely: respect the 2a–5a PST window for any real full run; cap concurrency; never
  hammer Cloudways.

---

## 12. Commit & author conventions (standing — do not deviate)

**1. Commit messages: short, lowercase, human.** Under ~50 chars. No body unless something
genuinely needs explaining. Write like a dev typing fast, not a changelog generator. No emoji, no
em-dashes, no `M0:`/`M1:` milestone prefixes, no bulleted bodies, no marketing voice. If it reads
like an AI wrote it, rewrite it.

- Bad: `M1: five deterministic checks + shared normalizer + enumeration reconciliation (GL)`
- Bad: `Add ARCHITECTURE.md — as-built (M0/M1) + design (M2, multi-brand, Phase 2) + R&D`
- Good: `add the five checks for GL`
- Good: `ignore .claude/`
- Good: `fix cfemail hash instability`
- Good: `wire NAP sheet for phone canon`

**2. Author is `sydashir <meetashirr@gmail.com>` and nothing else.** No `Co-Authored-By: Claude`
trailer, no "Generated with Claude Code" footer, no 🤖 line — strip all of it from every commit,
ever. This is client work under Syed's name.
