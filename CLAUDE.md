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
- **The `gh` active account DRIFTS — check it, never assume it.** Three accounts are logged in
  (`sydashir`, `dev778d`, `hybridmediaworks`) and the active one has silently flipped to `dev778d`
  twice, which fails as a bogus `Repository not found` on push. So **before every remote op**
  (push, PR, any `gh api`): run `gh api user --jq .login`; if it is not `sydashir`, run
  `gh auth switch --user sydashir`. Do this without asking — it is a known, recurring condition,
  not a surprise worth stopping for.
- **PUBLISH BEFORE YOU CHANGE CHECK CODE.** The resume cache is keyed on the check-version, and
  `checks_version` hashes every `checks/*.py` plus `parse.py`, `report.py`, `nap.py`, `crawl.py`
  **and `audit.py`** (it mints collapsed fingerprints). Editing any of them invalidates every
  brand's banked pages instantly. This cost MHD's 1,546 banked pages once — a check fix landed
  before the partial was published, and MHD re-crawls at ~2 pages/minute. Land the publish first,
  then the check change. (`server/` and `web/` are NOT hashed — product-layer work is free.)

- **NEW CODE THAT IS NOT A TEXT CHECK GOES OUTSIDE `auditor/checks/`.** `checks_version` hashes
  *every* file in that directory, so a module dropped there invalidates all nine brands' resume
  caches on every edit — even when it has nothing to do with the text audit. The **rendering layer
  belongs in its own top-level `render/` package** for exactly this reason: it will be iterated on
  constantly, and each iteration would otherwise cost a full re-crawl. It emits the same `Finding`
  shape and flows into the same report/diff/publish pipeline; only its *source* stays out of the
  text checks' version. Same rule for anything else product-shaped — `server/` and `web/` are
  already outside, and that is why product work is free.

- **`parse.py` is the highest-blast-radius file in the repo — BATCH every change to it.** It is in
  `_GLOBAL_SRC`, so *any* edit rule-changes **every check on every brand at once** and costs a full
  re-crawl of all nine — **~12 hours without MHD, of which RR alone is 9.4h**, plus MHD (see §13;
  a full census is ~85h, which is why it is never run). A one-line addition costs exactly the
  same as ten. So before touching it, work out everything the next few checks will need from it and
  make those changes in **one** commit — never one field at a time as each check comes up. The same
  is true of `report.py`. Compare a check module: editing `checks/phone.py` rule-changes only phone
  findings, because `diff.py::_CHECK_COMPONENT` scopes it.

- **A SAMPLED run (`-n N`) overwrites the diff baseline — never leave one as a brand's last run.**
  `write_run` persists history on every audit, publish or not, and the diff compares the next run
  against whatever ran last. A `-n 40` tuning run on CAD left a ~158-finding baseline, so the next
  FULL run reported **3,362 "new"** findings that were not new at all. The open count stays correct;
  only the new/resolved delta is junk, and it self-corrects on the following full run. The tell is
  unmistakable: brands never sampled showed 1/36/42 new, the sampled ones thousands. So: tune with
  `-n` freely, but finish with a full run before anyone reads the sheet.

- **A test double must mirror the real thing, including its empty and degenerate states.** A fake
  that is kinder than reality hides the bugs it exists to catch. The fake Sheets client invented a
  header row on first append; the real API does not, so the first row landed in A1 and was read back
  AS the header — every re-run would have appended a duplicate instead of updating, and the test
  suite said green. When a live run finds a bug the fake missed, fix the FAKE first, watch the tests
  go red, then fix the code.

- **RUN EVERY NEW TEST AGAINST THE UNFIXED CODE FIRST. A test that cannot fail on the bug it was
  written for is not a test.** Same family as the rule above and as the D-series: verify the
  instrument before trusting what it tells you. Write the fix, then `git show HEAD:<file> > <file>`,
  run the new tests, and **watch them fail for the right reason** before restoring. Two of these
  were caught exactly this way: `test_an_off_canvas_twin_is_never_the_one_photographed` passed
  against the buggy code because it only asserted "a shot came back" — the bug returns a perfectly
  good PNG of the wrong place — and had to be rewritten to assert WHICH element was flagged; and
  `test_demo_run_timestamps_are_utc_and_never_in_the_future` passed at 17:00 and failed at 03:00,
  so it only caught the defect inside the 2a-5a crawl window, which is precisely when someone would
  hit it. A test that passes both ways is worse than no test: it is a green light nobody re-checks.
  The same applies to a MEASUREMENT: `locator_measure.py` reported n=22 when the report held 70,
  because a silent `continue` dropped every occurrence-suffixed class, and the number was quoted as
  a result before anyone checked the denominator. Print what you excluded, always.

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
| RR (Renaissance Recovery) | https://www.renaissancerecovery.com | SEO 866-330-9449 · PPC 866-923-1867 | **~8k pages** (run 107, 2026-08-27: audit set **8,029 = 7,854 sitemap + 175 REST-only**, **7,995 audited**). Largest site by far and the slowest per page, so it dominates any full re-crawl. **Do not quote a fixed hour figure** — throughput is set by host load, not by RR: measured between 5.8 and 14.2 pg/min across runs. See the runtime section of README.md. |
| GL (Gratitude Lodge) | https://www.gratitudelodge.com | SEO 844-576-0144 · PPC 844-972-2859 | best ground-truth fixture (problem_sheet.csv) |
| AH (Addiction Hotline) | https://addictionhotline.com/ | (844) 575-6602 · (855) 701-0479 | |
| COC (Connections) | https://connectionsoc.com/ | 844-759-0999 | phone-bug motivating example |
| CAD (California Detox) | https://californiadetox.com/ | 888-995-4208 | empty-variable motivating example |
| AR (Alliance Recovery) | https://alliancerecovery.com/ | 844-287-8506 (+844-263-5113, (877) 511-4904) | multiple numbers — confirm canonical. **WE AUDIT 474 PAGES; ITS SITEMAP ADVERTISES ~11,200** — see §6a |
| MHD (Mental Health Directory / Inpatient finder) | https://inpatientmentalhealthfinder.com/ | (888) 376-8385 | |
| TDRC (The District Recovery Community) | https://thedistrictrecoverycommunity.com/ | (888) 871-2088 | |
| DBH (District Behavioral Health) | **not in sheet** | 888-707-6073 | parent brand; base URL unknown |
| SLN (Sober Living Nation) | **not live yet** | none | soberlivingnation.com when taken live |

### §6a. AR is audited at 4% of what its sitemap advertises (found 2026-09-02)

Every AR run since at least 2026-08-11 has enumerated **4 sitemap URLs** and reached 474 pages only
because WP-REST supplied 470 of them. Verified in five consecutive runs (51, 90, 102, 111, 123):
`audit_scope = {sitemap: 4, rest_only_added: 470, union: 474}`.

**What the sitemap actually advertises, checked directly 2026-09-02:**

| child sitemap | URLs |
|---|---|
| `city-data-sitemap1..5.xml` | 2,000 each |
| `city-data-sitemap6.xml` | 746 |
| `page-sitemap.xml` | **HTTP 500 — broken on their side** |
| `location-sitemap.xml` | 2 |
| | **~10,746 under `/city-data/` alone** |

**None of those 10,746 URLs are in our audit set.** AR's audited pages are `/vs/`, `/adderall/`,
`/ketamine/` and similar — not one `/city-data/` page.

Two facts worth separating:

* **Why we miss them:** the run records `sitemap_partial: True` with failed children, so the
  coverage guard correctly withholds `indexable_unsitemapped` — a partial read must never produce a
  coverage finding. The guard is right. **What is missing is that nobody was told the SCOPE was 4%.**
  The children respond 200 today, so the failure may be transient or a size/timeout issue on
  2,000-URL children; **cause unconfirmed, and it should not be guessed at.**
* **What is on those URLs:** every one ends in `-2` (`east-lansing-mi-2`, `east-hartford-ct-2`) —
  the WordPress slug-collision suffix, i.e. exactly the `meta:collision_slug` check. A spot-checked
  URL returns **301**, and a sitemap should list canonical 200s. So AR's sitemap advertises ~10,746
  redirecting duplicate-slug URLs, and we have never audited any of them.

**This is an escalation, not a to-do:** it needs Syed to decide whether AR's real scope is 474 pages
or ~11,000 before any AR result is quoted as coverage.

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

---

## 13. HANDOVER — state as of 2026-09-04 (read this first if you are new)

**The deterministic tool is COMPLETE. Do not build more checks.** Every row of the client-defect
coverage table (`docs/plans/2026-08-06-client-reported-defect-coverage.md`) is closed: sections A
and B shipped, section C declined with written evidence, section D written for the client. The
remaining work is *operating* it, not extending it.

### What exists
* 15 checks in `auditor/checks/`, all deterministic. Flagship is `phone.cross_brand_dial`.
* One command publishes all nine brands to the sheet: see `README.md`. ~7h of actual crawl.
* Reports land in `reports/<brand>/<stamp>/` (gitignored) and are published to the Google Sheet
  `1QnKHZBnEoxW2gIcOdDz6Ac2WjUbAa94Te_KR-7r2m-E` — **that sheet only, never anything else in Drive**.
* `scripts/post_run_report.py` answers "what did this run change" after a full run.
* Two-minute overview for a human: `docs/WHAT_THIS_TOOL_IS.md`, with screenshots of all five product
  screens in `docs/screenshots/`.
* **Per-brand client reports** — `python3 scripts/client_report.py` writes nine HTML files to
  `reports/_client/`. Each shows the top findings per harm section, with **a photograph of the
  element**, or a sentence saying why there is none.
* **The passes that run OUTSIDE the crawl**, all host-only (they need a browser, and `render/` is
  deliberately not in the image so it stays out of `checks_version`):
  - `scripts/accessibility_pass.py --all --status` — says which brands' accessibility findings are
    stale; `--all` refreshes them. Markup-only: the HTML is fetched with httpx and analysed in a
    browser with EVERY request blocked and the block proven by a canary.
  - `scripts/shot_pass.py --all` — photographs the elements. **It asks `client_report` which
    findings the client will see and shoots exactly those.** Never invert that: the first version
    photographed anything with a selector, and on GL wrote 61 pictures of which the report showed 0.
  - `scripts/locator_measure.py <brand>` — the acceptance test for the above, per finding class,
    against a 70% floor. Refuses a verdict below n=10.
* **THE BROWSER NO LONGER LIVES IN THE CACHE macOS PURGES, AND THE PASSES INSTALL IT THEMSELVES.**
  Playwright's default is `~/Library/Caches/ms-playwright/`, which macOS reclaims under disk
  pressure — it did so on 2026-09-08 (accessibility pass crashed mid-run) and again by 2026-09-23
  (28 tests erroring at `chromium.launch()`). Fixed 2026-09-23 in `render/browser.py`, two parts,
  because neither alone is enough:
  - binaries live in **`~/.local/share/ms-playwright`**, outside the cache tree. `PLAYWRIGHT_BROWSERS_PATH`
    is read at **RUN time as well as install time** (measured: unset, `launch()` looks in the cache
    and fails however the browsers were installed), so the module exports it in code — NOT from a
    shell profile, which no launchd agent or cron job would inherit.
  - `ensure_chromium()` runs **before any client page loads** in all six launch sites and installs
    if the binary is missing. A version bump, a fresh machine and a deleted folder all present as
    the same crash, and none is fixed by a better directory. A wiring test fails if a new pass
    calls `chromium.launch(` without it.
  **Do not run a bare `python3 -m playwright install chromium`** — it installs into the cache we no
  longer read, so it looks like it worked and changes nothing. Use `render.browser.INSTALL_COMMAND`
  (it carries the path), or just run the pass and let the preflight do it.
* `scripts/seed_demo.py` fills an EMPTY database so the product opens without a 12-hour crawl. Every
  URL is on `*.demo.invalid`; it refuses a database holding real findings, runs, pages or traffic.

### Known-broken / permanent limitations — state these, never paper over them
1. **Nothing schedules it.** The VM was dropped by decision. If nobody runs the command, nothing is
   audited. This is the single biggest operational risk.
2. **MHD is degraded and only ever sampled.** Its origin 503s under concurrent requests; conc 4 once
   pushed it into 500s that outlasted the run, so **max_concurrency = 2 is a locked ceiling** — the
   binding constraint is CONCURRENCY, and ~2 pg/min is its consequence (**measured 2.10 pg/min over
   4,600 pages, 2026-08-19/20** — not an estimate). A full census is impractical, and the
   scale figure has MOVED: the sitemap was **15,635 URLs up to 2026-08-05 and has been 10,722 since
   2026-08-21** (runs 83/84/85 vs 99/108 — it shrank by a third; cause unknown, not investigated).
   Any hour estimate quoted against 15,635 is now ~31% high. At ~2.1 pg/min a 10,722-URL census is
   still ~85h, so it is published as a labelled `PARTIAL SAMPLE`. **Since 2026-08-20 this is enforced by the product, not by remembering:** MHD carries
   `default_sample_size = 900`, so `POST /runs` caps it automatically with no flag. 900 is the
   largest sample MHD has ever actually published (901 on 2026-07-30; 353 twice on 08-05).
   **If enumeration returns blocked + 0 URLs, that is evidence the origin is degraded: leave it
   alone, do not retry into it.**
3. **DBH is enumerated from a static list** (`config/urls/dbh.txt`, **576 URLs**) because its
   2026-08-08 replatform to headless Next.js removed the sitemap, robots.txt and WP-REST. **A static
   list cannot discover pages added later** — they are silently never audited. Regenerate by hand
   when DBH publishes.
4. **Transient link timeouts cause a small recurring new/fixed wobble** in the Summary row. Cosmetic
   and self-correcting; the real fix is hysteresis on transient classes only.
5. **A sampled `-n` run no longer writes the diff baseline** (fixed in code) — but always finish with
   a full run before anyone reads the sheet.
6. **A running audit cannot be stopped mid-crawl.** `POST /api/runs/{id}/cancel` cancels a *queued*
   run for real, but a *running* one keeps fetching until it finishes or the worker stops; the API
   says so plainly rather than pretending. A true abort would mean polling a flag inside
   `run_audit` — an `auditor/` edit, so a full cache invalidation. Deliberately not built.
7. **A REFUSED OR SHORT RUN POISONS THE DIFF BASELINE — batch item 0, NOT yet fixed.**
   `run_audit` writes history BEFORE `server/jobs.py` applies the crawl verdict, so a run that is
   later refused has already replaced the baseline. RR run 119 was throttled to 77 of 8,029 pages,
   was correctly refused for reporting, and still left a 77-page baseline: run 129 then reported
   **11,259 new** against a real **128**. COC took the same damage from a run that did NOT fail —
   run 114 was `ok` at 886 pages against a normal 1,505, inflating the next run to 1,334 against a
   real 52. So the rule is about COVERAGE, not status. It recurs every time the crawl-floor guard
   fires. **Mitigated, not fixed:** `client_report.poisoned_baseline` detects it from run history
   and prints "Ignore the new since last time figure on this report" on exactly the affected
   brands. The open counts are always correct; only the new/old split is wrong, and it self-heals
   on the next full run. Fix is in `auditor/audit.py` (HASHED) — see the batch list.
8. **MHD's last attempt (run 130, 2026-09-03) was REFUSED** — it reached only 334 of 900 pages
   (37.1%), below the 50% crawl floor, so it produced nothing rather than 566 false "unreachable"
   findings about the client's pages. That is the guard working. MHD's published results are from
   **run 108 (2026-08-28)**. Do not retry into a throttling origin.
9. **AR is audited at 4% of its advertised scope** — 474 pages against a sitemap advertising
   ~11,200. Fully written up in section 6a; the report now states this to the client. **This needs
   Syed to decide** whether AR's real scope is 474 or ~11,000 before any AR coverage number is
   quoted.
10. **GL's resume cache was lost on 2026-09-03 and the cause was never established.**
   `cache/gl/pages.json` (55 MB) and `resume.done.jsonl` (75 MB) were present at 03:00, run 128
   completed at 04:09, and the directory was modified at 04:43 with both files gone. Ruled out by
   checking: disk space (127 GB free), the other eight brands (all intact), the auditor's own logic
   (only tmp+`os.replace`; the sole `unlink` is the non-resume start path, and there has been no
   later GL run), and the test suite (`CACHE_DIR` is monkeypatched to `tmp_path`). **Do not invent a
   cause.** Consequence: the next GL run re-crawls all 3,594 pages. The accessibility pass now falls
   back to the newest report's URLs, so a lost cache can no longer make a brand silently skip its
   checks — that fallback exists because of this incident.
11. **EVERY BRAND'S FIRST NATIVE RUN IS A FULL RE-CRAWL, NOT AN INCREMENTAL — expect ~12h, not an
   evening.** Measured on TDRC run 141 (2026-09-23), the first crawl since Postgres moved off
   Docker: `resumed_from_cache: 0`, and the summary named why —
   `changed_components = ["dict:soupsieve", "geodata_gfv_path", "src:geo_field_validator.py"]`.
   Runs up to 140 executed INSIDE the container, where `geodata_gfv_path` was `/opt/geodata-services`;
   a native run resolves it to the host path, so the path and that file's hash both move and every
   brand's banked pages are invalid. This is a ONE-TIME cost per brand, not a recurring one — the
   second native run resumes normally. TDRC paid 58 seconds; **RR is ~8,000 pages and the eight
   brands together are about 12 hours** (§6 has the per-brand rates). Nothing is wrong when this
   happens, and the diff handles it correctly: findings that vanish under the moved ruler are
   labelled `rule_changed`, never `resolved`. Do not "fix" it by reverting to Docker.

### What needs SYED, not an engineer
Nothing below is a coding task. Each is a decision only he can make.
1. **AR's real scope** — 474 pages or ~11,000? Until answered, no AR coverage figure means anything.
2. **Whether to pay a full re-crawl for batch item 0** (the poisoned baseline). It is mitigated in
   the report today; fixing it properly invalidates every brand's cache.
3. **Whether anything should schedule the tool.** The VM was dropped by decision, so today a human
   runs it or nothing is audited. `brands.schedule_cron` is NULL for all nine ON PURPOSE, so the
   dashboard cannot claim audits run overnight when nothing runs them; installing a timer means
   setting those crons — the step is written into `deploy/README.md` section 5.
4. **Traffic is connected for all nine through the Search Console API — DONE 2026-09-15, nothing
   left for Syed.** He enabled the API in Cloud project `lexical-sol-454719-s2` ("Google Map Reviews
   Project", in the districtbehavioralhealth.com org) and granted
   `app-service-account@lexical-sol-454719-s2.iam.gserviceaccount.com` on all nine properties
   (`sites.list` shows `siteFullUser` on each). Refresh with
   `python3 scripts/traffic_import.py --api` (last 92 days of final data), then regenerate reports.

### Traffic ranking — how it works (2026-09-14)
* **`server/traffic.py` is the one definition**, used by the client report and the API. Harm first:
  traffic orders findings only WITHIN a severity. Reach is a UNION of the pages a finding names,
  never a sum (GL had 13 findings on one page). A capped page list says "at least … N of M". The
  search-results section ranks on impressions, everything else on visits.
* **Five states, five sentences:** weighted, zero (a CSV zero is "not proof" and does not rank),
  unmatched ("a gap in our matching"), by design (no-indexed / 4xx / off-brand pages, reason
  printed), not connected (said once per report: "It does not mean these pages are quiet.").
* **The dashboard ranks by traffic only when one brand is selected** — visits to different sites are
  not comparable — and says so above the list.
* **The API is the source now** (`traffic_import.py --api`): property picked from `sites.list`
  (exact URL-prefix on the audited host, else the covering `sc-domain:`; never a URL-prefix on another
  host), paged by `startRow` until Google returns 0 rows, 429/5xx retried. For the same period the
  loader uses API rows and ignores the CSV. **Verified against the CSV on 2026-09-15:** on every page
  in both, impressions identical on all seven brands and clicks identical except 5 RR pages (+6
  clicks total). Unweighted findings fell RR 81%→12%, GL 74%→49%, CAD 47%→11%, COC 37%→13%;
  TDRC 100%→47%, MHD 100%→57%; AR (62%) and AH (17%) unchanged because their exports were complete.
  **www / bare twins** are folded onto the audited host ONLY when the twin permanently redirects
  (301/308) there, checked at import: TDRC's homepage traffic (392 clicks) was on `www.`, which 301s to
  the bare host; DBH's `www.` does not redirect permanently, so its 23 rows (1 click) stay unmatched.
  `url_key` itself still never strips www.
* **Redirected pages (2026-09-15).** A finding on an audited URL that redirects to another page of the SAME
  site is weighted by the page it lands on: table `page_redirects` (migration 0005), rebuilt from
  `cache/<brand>/resume.done.jsonl` by `traffic_import.py --api`, but ONLY when that cache holds at least as
  many distinct URLs as the brand's latest ok run audited (MHD's cache is a refused run's, so MHD keeps its
  last map). Rules proven on live data by two review workflows: each page counts once however many of its
  addresses a finding names; ONLY the destination's own rows count, never the old address's (those rows can
  be a different page it used to be — COC's homepage would have gained 50,773 impressions from an Orange
  County page); enumeration findings never follow redirects; in the dashboard the real page sorts above a
  redirect copy of the same finding. Weighting gained: RR +207, GL +86, COC +22, AR +19, CAD +15.
  **Known limitation:** the report's "on N pages" still counts addresses, so a group whose addresses all land
  on one page can say "on 5 pages". Pre-existing; no traffic number depends on it.
* `traffic_import.py` sums visits across duplicate rows and keeps the largest impression row (jump-link
  anchors overlap in one result). `--match-report` now says LIMITED BY THE EXPORT when most of the
  export's pages matched, instead of blaming the property type.

### The database is NATIVE, not Docker (2026-09-14)
Docker Desktop disrupted Syed's Mac, so Postgres moved to Homebrew `postgresql@16`
(`/usr/local/var/postgresql@16`, `brew services`, starts at login) on **port 55432** — the port every
default already uses, so no config changed. Moved by pg_dump/pg_restore; row counts identical on all
12 tables. `psql`/`pg_dump` live in `/usr/local/opt/postgresql@16/bin` (keg-only). **The API and
the web app start at login through two launchd agents** installed by
`deploy/macos/login_agents.sh install` (`local.district-auditor.api` on 8099,
`local.district-auditor.web` on 5173, KeepAlive, logs in `~/Library/Logs/district-auditor/`). The API
does NOT reload code: after any `server/` change run
`launchctl kickstart -k gui/$(id -u)/local.district-auditor.api`. Only to run audits from the web app,
also start `python3 -m server.worker`. **Do not start Docker on this machine.** The compose file is kept for a
server deployment only; the old Docker volume was left untouched as a fallback.

### Three DOCUMENTED NEGATIVE RESULTS — do not re-litigate without new evidence
* **D9 (ARCHITECTURE.md) — AI grammar/spelling.** An LLM pass editorialised about word choice.
  Judgement, not error. Rejected.
* **D10 (ARCHITECTURE.md) — dictionary spellchecker.** 9% precision by distinct word (7 real of 78,
  hand-classified). The residue is rare single-brand pharmaceutical vocabulary, and single-brand
  rarity is exactly where a typo lives — on this corpus those two populations are the same
  population. Parked behind `SPELLCHECK=1`, not deleted.
* **D11 — rule-based engines, researched 2026-08-11, NOT BUILT.** Harper rejected outright (no
  Python binding exists; 56% precision measured; missed 5 of 8 classic confusables). LanguageTool is
  viable on cost and speed (~1.1h for 31k pages at 8 workers, measured on our real median page;
  offline; free) **but its spellchecker reproduces D10 exactly** — it flags isotonitazene,
  solriamfetol, buprenorphine. So the only configuration worth piloting is **grammar-only with
  `MORFOLOGIK_RULE_EN_US` disabled**, which is the one setup nobody has published numbers for.
  Published prior is discouraging: Wikimedia measured 0.524 precision on Wikipedia (their stated
  lower bound); BEA-2019 recall is 5–8%. **Note: `language_tool_python` is GPL-3.0 — call
  `/v2/check` over httpx instead of importing it into client work.**

### If you pilot the grammar engine
Gate on VOLUME before precision: measure grammar-only findings-per-page on GL first. Under
~0.05/page it is too quiet to ship and you stop cheaply. Ground truth is recoverable but not stored
as a dataset — D10 names the 7 real words of 78; note that 2 of those 7 (`alcoholusedisorderaud`,
`ency`) are already caught by `empty_slot`. A separate, better-evidenced lever exists: the **UMLS
SPECIALIST Lexicon** hit PPV 0.90 on 76,786 clinical notes with residual FPs that were *not* drug
names — that attacks the failure that actually killed D9 and D10.

---

## 14. Process Management & Memory Constraints

- ALWAYS gracefully shut down llama-server, watchman, and node (Metro/Expo) dev servers before
  exiting a task, running a new server instance, or restarting the environment.
- Do NOT leave orphaned processes running in the background. Use killall or pkill to verify your
  spawned servers are dead before moving to the next step.

**Exception — this repo's three deliberate long-running services.** They are not orphans and must
NOT be swept up by a `killall`/`pkill` pass: the two launchd agents `local.district-auditor.api`
(uvicorn :8099) and `local.district-auditor.web` (vite :5173), which are installed to start at
login, and `python3 -m server.worker`, which the product needs in order to run an audit at all
(Syed, 2026-09-23: "Leave the worker running"). Stop those only when asked, or when restarting them
deliberately — and say so. Everything else you spawn is yours to clean up.

**Why this matters here specifically.** This machine is shared with other projects and has gone
into swap thrash repeatedly — load 947 on 2026-08-21, load 167 with `com.docker.backend` at 1000%
CPU on 2026-09-01/02, and three unclean reboots in four hours on 2026-09-02/03 that killed a crawl
and corrupted a Postgres checkpoint file. Measured 2026-09-23: with another project's jest suite at
84% CPU on two cores and 6.5 GB of 8 GB swap used, `/api/health` took **31.8 seconds** to answer and
a trivial `count(*)` took 8.3 s. A leaked dev server is not a tidiness problem on this box — it is
the difference between a 58-second audit and a dead run. Check `uptime` before starting long work.
