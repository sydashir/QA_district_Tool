# Nine-brand re-run — what changed, what it found, what it still cannot see

**Run 2026-08-08 → 2026-08-10. 7 of 8 brands published; DBH could not be audited (see below).**
This is the first run carrying every check built since the acceptance run: dead buttons, misrouted
social icons, placeholder strings, doubled/fused URLs, wrong-brand copy, duplicate content, blank
slots, links to leftover pages, and the parse-level page-region work underneath them.

**Active crawl time was ~7h17m across the eight brands, which matches the planning estimate.** The
runner reported 69h36m, but that is wall-clock: the machine slept for ~62h in the middle of GL.
Per brand: TDRC 1m20s · AH 3m10s · AR 9m34s · CAD 21m08s · COC 33m38s · RR 6h08m · GL ~30m of
actual crawling.

---

## 1. What the new checks added, per brand — and is any tab now unreadable?

| Brand | Open | From new checks | Share | Previous full run |
|---|---|---|---|---|
| RR | 26,131 | 9,086 | 34.8% | 24,508 |
| GL | 10,922 | 4,930 | 45.1% | 10,003 |
| COC | 5,120 | 1,942 | 37.9% | 5,051 |
| CAD | 3,521 | 333 | 9.5% | 3,270 |
| MHD | 2,712 | — | — | 2,826 |
| AR | 1,222 | 42 | 3.4% | 1,180 |
| AH | 677 | 111 | 16.4% | 644 |
| TDRC | 96 | 8 | 8.3% | 96 |
| **All** | **50,438** | **16,477** | **32.7%** | |

**No tab became unreadable because of the new checks.** The honest reading of that 32.7% is that
most of it is **one pre-existing template defect**, `missing_unit`, which was added during the
Phase-2 pilot and is not new work: 7,123 rows on RR and 4,002 on GL. Excluding it, the checks built
in this batch are roughly **3–9% of each brand**, which is the COC/AR/CAD picture and the right
answer — signal, not flood.

### The real readability problem is repetition, and it is now fixed

Every long tab is dominated by a handful of **template** defects, not by many distinct problems:

| Brand | Biggest class | Rows | Distinct shapes | Pages |
|---|---|---|---|---|
| RR | heading_structure | 7,992 | **5** | 7,776 |
| GL | empty_slot / missing_unit | 4,002 | **1** | 1,149 |
| COC | empty_slot / missing_unit | 1,873 | **2** | 811 |
| CAD | heading_structure | 1,770 | **5** | 697 |
| MHD | misspelling | 1,056 | **1** | 352 |
| AH | scope / county_for_country | 171 | **1** | 169 |

GL's 4,002 rows are **one missing word**: *"programs available within 25 of Long Beach"* wants
"miles". MHD's 1,056 are **one misspelling** — `Inpateint` → `Inpatient`, the client's own reported
typo, on 352 pages.

The shape-collapse that already reduced GL's dead-button findings from 141 rows to 3 has been
extended to `empty_slot`, `misspelling` and `scope`, keyed on the defect's **shape** (digits
normalised) rather than the sentence around it. Measured against this run's real output:

**50,438 rows → 31,793. 18,645 fewer (37%), with nothing lost** — each collapsed row carries its
page count and source pages. It applies on the next run.

## 2. The footer-template corruption family — one field, every page

**37 findings across 4 brands, covering 12,858 pages.** These belong together because they are one
defect wearing three shapes, and each is a single template field:

| Brand | Findings | Pages | Kinds |
|---|---|---|---|
| RR | 23 | 8,020 | 22 doubled-slash, 1 misrouted icon |
| GL | 6 | 3,585 | 3 doubled-slash, 2 fused URL, 1 misrouted icon |
| CAD | 4 | 1,245 | 3 fused URL, 1 misrouted icon |
| TDRC | 4 | 8 | 3 misrouted icons, 1 fused URL |

* **Fused URL** — two web addresses welded into one dead link (CAD's LinkedIn field contains a
  LinkedIn *and* a YouTube URL).
* **Misrouted social icon** — GL's Instagram icon points at LinkedIn, so **GL has no working
  Instagram link at all**; RR's YouTube icon points at Instagram; TDRC has three.
* **Doubled slash** — `//facility/…`, a separate address to Google than the correct one.

Nobody clicks their own footer, so these survive indefinitely — and they are the cheapest fix in the
audit: one template field each, every page corrected at once.

## 3. What the audit still cannot see

Written for the client at **`docs/WHAT_THE_AUDIT_DOES_NOT_CHECK.md`**. Contrast and colour,
visually cut-off forms, mobile-only failures, broken image icons, page speed, and whether a
`<button>` works (its behaviour is JavaScript, which the tool does not execute — so it reads `<a>`
elements only). **All of these were really reported by Blue Media and none is covered.** Silence
from a run does not mean those are clean, and the doc says so plainly, with the
template-sampling alternative.

## The checks are validating against ground truth

The defects the client found **by hand** are now caught **automatically**:

* `empty_slot` catches Jake's verbatim sentences ("There are at least outpatient drug rehab
  programs available within of California").
* `misspelling` catches Connor's seven, including `Inpateint` — 352 pages of it on MHD.
* `brands` catches **"Gratitude Lodge" in Connections body copy** on three live pages — Connor's
  exact reported case, found automatically for the first time.
* `actions` caught the 17 dead `Verify Insurance` / `Learn More` buttons on DBH's homepage — and
  the DBH rebuild has since fixed 15 of them.

## Two brands the run could not audit, and why that matters

* **DBH — replatformed.** Its DNS disappeared mid-run on 08-08 and returned by 08-10; the site is
  now **headless WordPress behind Next.js**. Sitemap, `robots.txt` and WP-REST all 404, so the tool
  has no way to enumerate it. The content itself is still auditable (prerendered, still Elementor),
  and all 30 sampled pre-migration URLs still return 200 — so a static URL list restores it. Its
  sheet tab is **stale by a replatform**, not by a few days. Full write-up:
  `docs/incidents/2026-08-08-dbh-dns-outage.md`.
* **MHD — throttled, not down.** Concurrent requests get HTTP 503 while plain serial requests get
  200. Crawled gently it manages ~6 pages/min, so a full 929-page pass takes **~2.6 hours**, not
  the ~29h previously assumed.

**In both cases the tool refused to publish an empty result**, so neither tab was overwritten with
a misleading zero. That guard is the reason a DNS outage and a replatform did not silently report
the parent brand as clean.
