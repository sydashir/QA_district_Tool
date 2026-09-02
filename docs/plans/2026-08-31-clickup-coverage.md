# ClickUp coverage — seven tasks against what the auditor actually catches

**2026-08-31. Read-only pass over ClickUp; nothing was written there.**
Every "covered" claim below is checked against the code or the findings database, not assumed.
Counts are from the latest `ok` run per brand at the time of writing. **Corrected 2026-09-02:** the figure originally given here, 325,031, was the ALL-TIME row count across every run ever — the current set was 39,758. `findings` is per-run, so a total over the table counts the same defect once per run it appeared in. The per-class numbers below are current-set and stand.

| # | Task | Status | Covered? |
|---|---|---|---|
| 1 | `86ba87pzv` GL — Duplicates and Page Import Issues | queries/stuck | **partial** |
| 2 | `86ba87pqd` RR — Duplicates & Page Import Issues | ready to review | **partial** |
| 3 | `86bbbrpey` All Sites — NoIndex Issue | to do | **gap** |
| 4 | `86bar4y14` CTM & CRM Lead Creation Issues | revisions | **out of scope** |
| 5 | `86b7xbvb9` gSheet Import Issue Corrections | complete | **mostly covered** |
| 6 | `86ba1g4cb` GL & RR — Random Page Issues / hyperlink protocol | complete | **covered** |
| 7 | `86baawd2a` More issues found on the sites | complete | **mostly covered** |

---

## 1. `86ba87pzv` — GL Duplicates and Page Import Issues

Redirects pointing at the wrong URLs, duplicated pages, pages left on a blog template, no-indexed
drafts, URL-structure corrections.

**Caught:** duplicate titles/H1s (`meta`, `duplication` — 3,517 `duplicate_paragraph` findings
network-wide), dead sitemap entries (`enumeration:sitemap_dead`, 363), no-index pages missing from
the sitemap (`enumeration:noindex_unsitemapped`, 918).

**Not caught, and cannot be:** whether a redirect points at the *right* destination. That is a
mapping between old and new URLs which exists only in the client's sheet. We can say a URL redirects
and where it lands (`final_url` is captured); we cannot say it should have gone somewhere else.

---

## 2. `86ba87pqd` — RR Duplicates & Page Import Issues

~200 rows of 404s needing redirects, empty geo pages, blank pages, a broken sobriety-calculator
shortcode, 410-Gone decisions, ACF image/gallery QA, duplicated outpatient pages.

**Caught:** 404s (`broken_links:broken`, 112), unreachable targets (`unreachable`, 69), blank and
thin pages (`blank`), empty sections (`placeholder:empty_state`, 740 pages), leftover shortcodes and
tokens (`placeholder`), missing images (`broken_image`, render layer).

**Not caught:** the 410-vs-404 distinction — we report both as a dead link, and "deliberately gone"
versus "accidentally missing" is an editorial decision we have no input for. Also, a **broken
shortcode that renders as nothing** is only caught when it leaves an empty section behind; one that
renders as literal `[shortcode]` text is caught by `placeholder`.

---

## 3. `86bbbrpey` — All Sites NoIndex Issue → **THE REAL GAP**

Old no-indexed pages that should be redirected and made indexable, plus the linked task about
internal links that point at a URL which then redirects, instead of at the live page.

**Half covered.** `enumeration:noindex_unsitemapped` (918) finds no-index pages outside the sitemap.

**The other half is a genuine gap, verified in code.** `auditor/checks/links.py:299`:

```python
stats["redirects"] += 1  # benign redirect; tagged in stats, not a finding in M1
```

**A link that 301s is counted and then discarded.** It never becomes a finding, so it cannot appear
in a report, the sheet, or the diff — exactly the "links that go through a redirect instead of
straight to the active page" this task is about. The data is already in hand at that line; it is a
reporting decision from M1, not a missing capability.

**Recommended:** a `broken_links:redirected_internal` class — an internal link whose target 301s, and
the URL it lands on. INFO or WARNING, not ERROR: it works for the visitor, it costs a hop and some
link equity. `links.py` is hashed, so this belongs in the batch list.

---

## 4. `86bar4y14` — CTM & CRM Lead Creation → **out of scope, and should stay out**

Phone-number *format* between CTM, JotForm, TalkFurther and Zoho — `(###) ###-####` versus E.164 —
creating duplicate CRM leads, plus a 32k character limit on Zoho's transcript field.

**Nothing here is a website-content defect.** It is CRM integration. The one adjacency is that our
phone check normalises to E.164 (`checks/phone.py`), the same standard the thread settles on, so the
tool's own notion of "the same number" already matches what they moved to. Nothing to build.

---

## 5. `86b7xbvb9` — gSheet Import Issue Corrections → **mostly covered**

Blank pages, missing hero images, blank content in whole sections, duplicate page versions, `-2` URL
suffixes, page/parent IDs, unpopulated variables in national content.

**Caught:** blank pages and sections (`blank`, `placeholder:empty_state`), unpopulated variables
(`empty_slot` — the motivating class, 59,183 findings in the history), leftover ACF tokens
(`placeholder`), duplicates (`duplication`), and — new since this task closed — **missing images via
the render layer** (`broken_image`), which is precisely the "missing hero background" symptom.

**Not caught:** a `-2` URL suffix. Verified absent from `meta.py` and `duplication.py`. WordPress
appends `-2` when a slug collides, so it is a reliable fingerprint of an accidental duplicate page.
Cheap to add (a slug regex in `meta`), hashed, → batch list.

Worth noting this task asks for **"a full page screenshot sent to Jake during QA"** — the screenshot
work built this week is the automated form of that request.

---

## 6. `86ba1g4cb` — GL & RR Random Page Issues → **covered; this task is where `dead_cta` came from**

*"On this global widget on national pages, none of the buttons work. They are just # buttons."*

That is `actions:dead_cta` exactly — **380 findings across the nine brands today**, the single
largest actionable class after phone. Buttons landing on broken pages are `broken_links:broken`.
Elementor dynamic links still pointing at deleted page IDs surface the same way.

**One thing not covered:** *"Read reviews button links out to call instead of taking the user to the
reviews page."* The link works; it goes somewhere unrelated to its label. We catch this only for
social links (`actions:social_misrouted`, 4). A general "button label disagrees with its
destination" check is real but needs a label→destination expectation table, which does not exist.

---

## 7. `86baawd2a` — More issues found on the sites (re-read) → **mostly covered, and it is the tool's origin**

Re-read in full. Most of this task is *why the tool exists*, and the tool now catches it:

| Reported | Now caught by |
|---|---|
| `"In , the involving substances such as"` (CAD homepage) | `empty_slot` — the motivating case |
| `"Among a population of in , overdose outcomes d compared to by %."` | `empty_slot`, incl. the truncated word `d` |
| `"There are at least outpatient programs available within of California"` | `empty_slot` |
| `[acf field=ge,` | `placeholder` |
| COC facility page phone-number errors | `phone` — the flagship; `cross_brand_dial`, `display_dial_mismatch` |
| Broken mega-menu icons on mobile | `broken_image` (render layer) |
| Interlinking widget blank when no related posts | `placeholder:empty_state` |
| Zero `<nav>` elements, no landmarks for screen readers | `accessibility` (markup rules) — **but see below** |
| "spelled Heroin wrong" | partially — `misspelling` mined typos; general spelling is the documented negative result (D10) |

**Two that are not covered, honestly:**
* *"RR homepage meta description doesn't mention Tennessee."* We check meta length, presence and
  duplication — not whether the wording covers the right geography. That is editorial judgement, the
  class rejected four times (D9–D12).
* *Sitemap structure* (39 Rank Math files, grouping by template). We consume the sitemap; its
  organisation is an SEO decision.

### And one thing this re-read exposed

Jake's question on this very task — *"Let me know why these phone number link errors were not been
caught yet by the QA staff member or the automated QA tool"* — is the reason the phone check exists.
It is now the strongest check in the tool.

But: **`accessibility` findings have never been stored. Zero, in the entire database.**
`render/markup.py` is built, tested and measured, and `scripts/client_report.py` already has report
sections keyed to `accessibility:link-name` and `accessibility:label` — but the module is never
called from the audit pipeline. It exposes `audit_html(pages, brand_url)`, a batch API needing a
browser, rather than the per-page `run(parsed, config)` that `_PAGE_CHECKS` expects, so it was never
connected. The screen-reader/landmark findings this task raised are therefore **built but not
running**. Wiring it is product-layer work in `server/` — outside `checks_version`, so free.

---

## What to do, shortest path

**Free (not hashed, no re-crawl):**
1. Wire `render/markup.py` into a run so `accessibility` findings actually reach the database. The
   check, the collapse and the report sections all already exist.

**Batch list (hashed — land with the selector work, one invalidation):**
2. `broken_links:redirected_internal` — the M1 decision at `links.py:299` reversed. Task 3.
3. `-2` slug detection in `meta`. Task 5.

**Not worth building:** the redirect-destination mapping (tasks 1–2, needs the client's sheet), the
label-vs-destination check (task 6, needs an expectation table), and anything in task 4.
