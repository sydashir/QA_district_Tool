# Ranking findings by traffic — design

**Design only. No code written. 2026-08-31.**
Every number below was measured against this repo's own data, or traced to current Google
documentation. Three research streams, each adversarially reviewed; the review refuted enough of the
first pass that the corrections are the most useful part of this document.

**Headline: the feature is worth building, GSC is the right source, and it CANNOT be built on what
we store today.** Three storage defects make the ranking number unavailable or wrong for the
findings that matter most. They are cheap to fix and they are the real prerequisite.

---

## 1. Source: Google Search Console, not GA4

| | GSC | GA4 |
|---|---|---|
| API | Search Console API v3, `searchanalytics.query`, `rowLimit` 1–25,000, paginate by `startRow` | Data API v1beta `runReport`, max 250,000 rows/request |
| Python client | `google-api-python-client` | `google-analytics-data` |
| Per URL | clicks, impressions, CTR, position | sessions, screenPageViews, users |
| Retention | 16 months | property-dependent |
| Freshness | 2–3 days | ~24h |

**Three reasons GSC wins, in order of weight:**

1. **GA4 structurally cannot weight a quarter of our corpus.** `meta` findings — a bad title or
   description — do their damage *in the search results page itself*, which GA4 never observes. On
   the current corpus `meta` is one of the largest classes. GSC **impressions** is the only correct
   weight for them; GA4 has no number for a page nobody clicked *because* the title was wrong.
2. **GA4's `(other)` row silently eats the long tail — but GSC is NOT immune, and the first draft
   of this document was wrong to imply it was.** GA4 folds above ~500 unique dimension values/day
   (RR ~7,970 URLs, GL ~2,960 — 16× and 6× over) and also applies thresholding and sampling. What
   review established is that **GSC drops rows too**, from Google's own API documentation:

   > "When you group by page and/or query, our system may drop some data in order to be able to
   > calculate results in a reasonable time using a reasonable amount of computing resources."

   > "The Search Analytics method exposes a maximum of 50K rows of data per day per search type
   > … **sorted by clicks**."

   Grouping by page is exactly what this design does, and the tail that falls off is the low-click
   tail — the same population. **GSC's advantage here is that its loss is smaller and bounded, not
   that it is absent.** The honest statement is that neither source can promise a row for every
   page, which is why §6's "unmatched" state is mandatory rather than defensive.
3. **Credentials are half the ask.** GSC needs the service account added as a user on each property;
   GA4 needs a property ID *plus* a role grant per property, and someone has to find nine property
   IDs.

### What Syed would need to request — GSC

The existing Sheets service account can be reused; nothing new is created.

> For each of the nine properties in Search Console, add
> **`app-service-account@lexical-sol-454719-s2.iam.gserviceaccount.com`**
> as a user. Search Console → Settings → Users and permissions → Add user.
> **Only an existing owner can add a user.** Owner, Full and Restricted all reach the Performance
> data this needs; **Full is the safe minimum** to ask for, since Restricted has occasionally
> surprised people on API reads.

A service account needs no special treatment — the address is added like any other user, and
delegated ownership is *not* required for Search Analytics reads.

**Syed can verify every grant himself** by calling `sites.list`, which returns each `siteUrl` and its
`permissionLevel`. Nothing further needs to be asked of anyone, and nothing needs to be asked *up
front* — see the trap below.

**RULE: `sites.list` determines the property type. Never ask.** Asking invites a confident wrong
answer, and a wrong answer is indistinguishable from a failed grant. So the tooling must REPORT, per
brand, which property matched and of which kind — a brand returning zero rows is then diagnosed
rather than guessed at:

    GL   sc-domain:gratitudelodge.com       siteFullUser    4,102 pages   OK
    RR   https://renaissancerecovery.com/   siteFullUser        0 pages   URL-PREFIX ON THE BARE
         HOST, but we crawl www. — this property cannot contain our pages. Ask for the www prefix
         property, or a domain property.
    CAD  (no property matched)              —                   —         NOT GRANTED

Three different causes, three different messages. Without that they all read "no traffic data", and
someone spends an afternoon on the wrong one.

**Enabling it on our side:** switch on the Search Console API in the existing GCP project, and add
`https://www.googleapis.com/auth/webmasters.readonly` to the `SCOPES` list in `auditor/sheets.py`.
That file is outside `auditor/checks/`, so it costs no cache invalidation, and the same key file
already in use works unchanged.

**Nine separate grants, one per property.**

**The trap worth knowing before it wastes a day:** domain vs URL-prefix matters twice. It changes the
identifier (`sc-domain:renaissancerecovery.com` versus `https://www.renaissancerecovery.com/`) *and*
it changes coverage — a domain property spans all subdomains and protocols, a URL-prefix property
covers only the exact prefix. **A URL-prefix property registered on the bare host, while we crawl
`www.`, returns zero rows and looks exactly like a failed grant.** Given GL and RR are the two
`www` brands, this is likely rather than hypothetical. Do not ask anyone which kind they have —
`sites.list` says, and asking invites a wrong answer.

**What could stall it:** the properties are HWA-managed, so this is a request through Jake, and nine
grants is nine chances for one to be missed. The design must therefore work brand-by-brand — a
brand with no grant simply reads "not connected", never "no traffic".

### The v1 that needs no credentials at all

GSC's UI exports a per-page CSV. Nine manual exports, no API, no grants, no quota — enough to prove
the ranking is useful before asking anyone for access.

**But the CSV has a defect the API does not**, and it decides the ingestion path:
Google's own documentation states that values shown as `~` or `-` in the report (not available /
not a number) **are written as zeros in the downloaded file**. So in the CSV path, "no data" and
"genuinely zero" are indistinguishable — the exact confusion §4 exists to prevent. **If the CSV path
is used for v1, every CSV-derived zero must be labelled `unknown`, never `zero`.**

### Cost

Negligible. ~15,900 distinct URLs carry findings across all nine brands; at 25,000 rows per request
that is roughly **one request per brand per period**. The driver is brands × periods, not pages.
Refresh monthly: traffic that moves week to week is noise at the resolution of "which finding first".

---

## 2. Matching: much easier than expected, because we already canonicalise

Measured across all nine brands, 15,959 audited pages:

| | our stored URLs |
|---|---|
| https | **100%** |
| trailing slash | **0%** (`canonical_url()` strips it at store time) |
| query strings / fragments / mixed case | **0% / 0% / 0%** |
| `www` | **100% on GL and RR, 0% on the other seven** |

So the messy cases the brief anticipated — protocol, case, query strings — **do not exist in our
data**. Two things do:

* **The trailing slash, in reverse.** The *live* sites serve the slashed form (96.7–100% of
  `final_url` per brand), so GSC will report `…/mescaline/` while we store `…/mescaline`. Raw string
  equality matches ~0%. One `rstrip("/")` on Google's side fixes essentially all of it.
* **`www` is per-brand, not global.** Hardcoding either form breaks half the estate. The match key
  must keep the host as-is and normalise only the path.

**Proposed match key:** `scheme-less host + path, lowercased, trailing slash stripped`. No query, no
fragment (we have none, and a GSC row with a query string should be summed into its bare path).

**Redirects: 328 of 15,959 pages (2.1%)** have `final_url != url`. Google indexes the destination,
so the key should be built from **both** the requested URL and the final URL, both pointing at the
same finding. **16 pages redirect off-domain entirely** (TDRC 7, AH 5, MHD 4) — those must never
inherit traffic, since the traffic belongs to another brand.

**The match rate must be reported.** An unmatched page silently weighted zero is indistinguishable
from a page with no traffic. Every refresh records matched / unmatched / unknown per brand, and the
report states it.

---

## 3. What refutation found — the three storage defects that block this

These are the reason the feature cannot be built today, and all three were found by measurement.

### (a) `broken_links` page counts are a truncation artefact — **1,247 findings affected**

`auditor/checks/links.py:216` and `:317` both do `sources = targets[url][:5]`, and `_finding` never
sets `details["page_count"]`, so `server/importer.py` falls back to `len(sources)`.

**Verified: all 1,247 current `broken_links` findings have `max(page_count) = 5`, and not one
carries an explicit page_count.** GL's footer Facebook link is stored as affecting **5 pages on a
3,353-page site**.

The trap is that `page_count` *equals* the stored source count by construction, so any completeness
test of the form `len(sources) >= page_count` reports **complete** and the reach would be stamped
"measured". This is check #1 of the client mandate.

### (b) Findings anchored on a homepage while affecting the whole site

`findings.url` is a representative anchor, not the affected set. **61 current findings are anchored
on a brand homepage while affecting 62,813 page-instances between them** — RR's flagship phone
finding is anchored on `https://www.renaissancerecovery.com` and covers **7,967 pages**.

Weighting that by `findings.url` traffic gives the defect **the highest traffic number on the site,
from the wrong page**. That is worse than not weighting at all, and it would happen to the flagship
check.

### (c) Multi-page findings recorded as single-page

`auditor/audit.py:180` emits `details={"count": …, "pages": urls[:8]}` — the key is `pages`, not
`sources`, so the importer records `page_count=1, sources=NULL`. **591 findings**, spanning
thousands of page-instances (`meta` 347 of them, `heading_structure` 244), look single-page. A
duplicate meta description across 102 pages would be ranked on one page's traffic.

### (d) And the client report would double-count

`scripts/client_report.py` groups findings and then computes `span = max(sum(page_counts), len(g))`.
Real data: GL has a `dead_cta` group of **13 findings on exactly one URL**. Summing member reach
multiplies that page's traffic by 13. **Reach must be a union over distinct page keys**, computed
per group after merging — never a sum of member reaches.

---

## 4. Sum or max: both, and sum is the ranking number

**Rank on sum (reach). Print max beside it.** They are not rival rankings — max is the qualifier
that says whether the sum is trustworthy.

The argument is what the reader does. For a collapsed finding the fix is *one edit* — one footer
element, one template field, one theme colour — so page count is not a cost estimate. It is a proxy
for what the defect costs while it stands, and that is the number of visits that met it: the sum.
Max cannot rank, because it makes a footer defect on 8,000 pages tie with a single-page defect on
the homepage — both include the homepage.

But sum alone produces a long-tail illusion, so both are shown:

> **Reach 8,058/mo** · busiest page 120/mo · across 1,343 pages
> **Reach 7,000/mo** · busiest page 4,200/mo · across 8 pages

Those two invert under the two metrics, and the inversion is decision-relevant: the second is a dead
button on the homepage *and a paid landing page*, where every impression was bought.

**Reach is only computable where the affected set is known.** Given (a)–(c), every finding must
carry a `reach_basis`: `measured` (complete source list), `partial` (truncated — state how many of
how many), or `unknown`. **Partial must never render as measured.**

---

## 5. Ranking: within harm, never across it

Harm ordering stays. A cross-brand phone leak on a quiet page still outranks a contrast issue on a
busy one, because the harm is someone calling a competitor. **Traffic orders findings *within* each
section**, replacing the current page-count sort. The report's opening line can then honestly say
*"these findings sit on your highest-traffic pages"* about the top of each section.

---

## 6. The no-data cases — four distinct states, four different sentences

Never collapse these. Zero is not a synonym for unimportant, and a brand-new page and a dead page
both show zero.

| State | What the report says |
|---|---|
| **Not connected** — no GSC grant for this brand | "Traffic data is not connected for this site, so these findings are listed in the usual order and none of them are weighted. It does not mean these pages are quiet." |
| **Unmatched** — connected, but this URL was not in the data | "We could not match this page to the traffic data, so it is unweighted. That is a gap in our matching, not a measurement of the page." |
| **Matched, zero** — present in GSC with zero impressions | "This page had no search impressions in the period. It may be new, no-indexed, or reached another way." |
| **Cannot have data** — the finding is *about* the page being invisible to search | not weighted at all, and not listed as unmatched |
| **Partial reach** — collapsed finding, truncated source list | "Reach is at least N/mo, measured across 8 of 1,343 affected pages." |

And a rule the CSV path forces: **a zero that came from a CSV is `unknown`, not `zero`** — Google
writes missing values as zeros in the download.

**One whole class is unweightable by construction, and must not be reported as "unmatched".**
`enumeration` findings are largely about pages that *cannot* appear in Search Console: on the
current corpus, **915 are "no-index page missing from the sitemap" and 492 are "sitemap page returns
4xx"**. A no-indexed page has no impressions because it is no-indexed; a dead URL has none because it
is dead. Counting those as matching failures would make the match rate look broken and would bury
the genuine gaps. They are excluded from the match-rate denominator and shown unweighted.

**The exclusion must be VISIBLE on the finding, not merely applied.** A silent exclusion is a rule
nobody can see, and the next person to look wonders why `enumeration` findings are never weighted
and re-adds them. So the finding carries its reason and the report prints it:

> *"This page is no-indexed, so it has no search impressions by design — not weighted."*
> *"This page returns an error, so nobody reaches it from search — not weighted."*

The rule then explains itself at exactly the point someone would otherwise undo it.

---

## 7. Storage

A new table, `page_traffic(brand_id, url_key, period, impressions, clicks, position, source,
fetched_at)`, keyed on the normalised URL — **not** a column on `findings`. Findings are immutable
per run; traffic changes weekly. Joining at read time means an old run's report re-renders with
today's traffic, which is correct: the question "which of these should I fix first" is asked now.

The match report (`matched / unmatched / unknown` per brand per refresh) lives beside it, and the
client report reads it to state coverage.

---

## 8. Build order

**Prerequisites — hashed, must be batched with other check work (they cost one re-crawl):**
1. Stop truncating: `links.py` `[:5]`, `audit.py` `[:8]`, and set an explicit `page_count` on
   `broken_links`. Or, better, store a URL *pattern* for template-wide findings rather than a list.
2. Fix `audit.py:180` to emit `sources`, not `pages`, so 591 multi-page findings stop reading as
   single-page.

**Then, free (not hashed):**
3. `page_traffic` table + a GSC client (or CSV importer) + the match report.
4. Reach computation with `reach_basis`, union-per-group in the client report.
5. Report and sheet columns, with the four no-data sentences.

**Do not start at step 3.** Without 1–2 the ranking is wrong precisely where it matters most: the
flagship phone check and every broken link.
