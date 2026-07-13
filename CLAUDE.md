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
- **Session continuity:** at the start of every session, read `SESSION_STATE.md` first. Update it
  continuously through the session, not just at the end — if you're ever unsure you have enough
  context budget left to finish a thought, write state down first.
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
| NAP / sites sheet | `~/Documents/workk/district/.tmp_dd/jake_sites_tab.csv` (+ `jake_sites.xlsx`); canonical gSheet: docs.google.com/spreadsheets/d/1AU_wNukifVPc6yH7pvXW051llwOnf7RTDx9hZDVgF-c | Per-brand base URL + canonical phone numbers |
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

## 8. Relationship to the GeoData Fetcher & "Check #2"

The existing GeoData Fetcher (`~/Documents/workk/district/`, Streamlit) validates the **source
gSheet before WP import**. Its **"Check #2 = Geo Variable Consistency"**
(`services/pre_import_checker.py:492-609`, method `_check_geo_variable_consistency`; one of 7
numbered checks in `run_all_checks`) is the relevant analog. It extracts variable references from
template cells and flags any that don't map to a real GeoData column.

**The bug the plan refers to** ("currently looks for `{{var}}` instead of `[acf field=var]`") was
real: it originally matched only `r'\{\{([^}]+)\}\}'` (`pre_import_checker.py:566`), but real
sheets use the WordPress ACF shortcode `[acf field=var]`, so the check silently passed. It has
since been **fixed on branch `add/alcoholism-wiring`** — it now also extracts `[acf field=...]`
via the shared NBSP-tolerant `extract_acf_tokens` helper in
`services/geo_field_validator.py:56-71` (skip-list: `geo, state, near-in, full_geo, topic, city,
drug, title`). Stale artifacts remain (docstring `:506`, message `:580` still say `{{}}`).

**Implication for us:** reuse `geo_field_validator.py`'s `ACF_TOKEN_RE` / `extract_acf_tokens`
logic as the reference for our live-page placeholder check (check #4). The pre-import check
guards the sheet; ours guards the rendered page — leftover `[acf field=...]` tokens **or**
empty-slot artifacts on a live page mean the import didn't resolve them.

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
