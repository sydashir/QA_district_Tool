# Client-reported defects vs. our coverage — all 7 PDFs on ClickUp 86baawd2a

> **"Thanks Connor! Assigned to devs and adding these scenarios to the automated audit tool."**
> — Jake Heinrichs, 2026-06-18, replying to Connor Bringas on this thread.
>
> These seven attachments are the specification for this tool, in the client's own words. Where
> this table and anyone's reading of the ticket disagree, the attachments win.

Every defect Connor Bringas (Blue Media) reported to Jake between 2026-06-05 and 2026-07-23, read
from the seven PDF attachments, classified for **deterministic detectability** and **current
coverage**. Jake's own reply on 2026-06-18: *"Assigned to devs and adding these scenarios to the
automated audit tool."* This table is that scenario list.

Sources: DBH Group Issues 6/05 (17pp) · RR Content Issues 6/10 · RR Spelling Issues 6/10 ·
Holistic RR Template Issues 6/18 · Holistic Template Issues 6/25 · Holistic Template Issues 6/30 ·
More Site Issues 7/23.

---

## A. ALREADY COVERED — the tool catches these today

| Reported defect | Our check | Note |
|---|---|---|
| `"Among 9861 people in 2023, overdose outcomes d compared to 2022 by 5.96%"` | `empty_slot` truncated_word | Connor's verbatim sentence is our test fixture |
| `"There are at least outpatient drug rehab programs available within of California"` | `empty_slot` double_preposition | verbatim fixture |
| `"In , the involving substances such as"` | `empty_slot` orphan_comma | verbatim fixture |
| `"overdose outcomes d compared to by %."` | `empty_slot` empty_percent | verbatim fixture |
| `"within 20 of Orange County"` — missing unit | `empty_slot` missing_unit | added after the Phase-2 pilot |
| `[acf field=geo]` / `[acf field=ge` visible in copy | `placeholder` | |
| Link to broken `?page_id=951` | `broken_links` | |
| Footer links that 301-redirect (Rehab Admissions, Check My Insurance) | `broken_links` | client asked for redirects to be flagged |
| Duplicate `<h1>` / heading already on page | `heading_structure` | |
| Misspellings incl. in URLs (`inpateint`, `residental`, `tennesse`) | `misspelling` | Connor's 7, body + slug |
| Empty / thin sections | `blank` | partial — see B4 |
| Wrong-brand phone number dialled | `phone` cross_brand_dial | the flagship check |
| CTA with no destination (`Verify Insurance`, `Learn More`) — **B1** | `actions` dead_cta | 22 live findings, hand-verified; 17 on the DBH homepage alone |
| Social icon wired to the wrong network — **B8** | `actions` social_misrouted | GL's Instagram icon → LinkedIn; RR's YouTube icon → Instagram |
| Double slash in a URL — **B7** | `broken_links` double_slash | |
| Two URLs fused into one link — **B7** | `broken_links` fused_url | CAD's footer |
| `No content found` / `[sobriety_calculator]` / bare `GEO` — **B2** | `placeholder` | general pattern set, not the five reported strings |

## B. DETERMINISTIC AND **NOT** COVERED — build these

Ordered by how often the client reported them.

| # | Defect | Times reported | Detection | Verified live? |
|---|---|---|---|---|
| ~~**B1**~~ | ~~CTA/nav element with no destination~~ | 12 | **SHIPPED** — `actions` dead_cta | 22 findings across 8 brands, all hand-verified |
| ~~**B2**~~ | ~~Placeholder strings rendering as copy~~ | 6 | **SHIPPED** — `placeholder` | |
| ~~**B3**~~ | ~~Duplicate content inside one page~~ | 5 | **SHIPPED** — `duplication` duplicate_paragraph | live: the Hydromorphone description renders 3x on one AR page |
| ~~**B4**~~ | ~~Empty structural slots~~ | 5 | **SHIPPED** — `empty_row` | only INTERIOR gaps; trailing blanks are grid padding (492 of 492 first-pass findings) |
| ~~**B5**~~ | ~~Duplicate anchor text + href repeated~~ | 3 | **SHIPPED** — `duplication` duplicate_link | scoped to ONE container; a repeated CTA down a page is normal design |
| ~~**B6**~~ | ~~Wrong brand named in copy~~ | 2 | **SHIPPED** — `brands` sister_brand (WARNING, worded as a question) | live: COC serves an archived California Detox page design |
| ~~**B7**~~ | ~~Double slash in URL~~ | 1 | **SHIPPED** — `broken_links` | |
| ~~**B8**~~ | ~~Social icons pointing at the wrong network~~ | 1 | **SHIPPED** — `actions` social_misrouted | 7 findings; GL has no working Instagram link at all |
| **B9** | ~~Two distinct addresses sharing one map link~~ | 1 | **MOVED TO SECTION C** — needs judgement, see below | zero instances found on the 3 likeliest pages |
| ~~**B10**~~ | ~~Stale/`-old` URL leaking into nav~~ | 1 | **SHIPPED** — `broken_links` cruft_link (ERROR) | **YES — the client's own URL, `rehab-admissions-old/`, still linked from 56 RR pages** |
| ~~**B11**~~ | ~~`/feed/` URLs published~~ | 1 | **SHIPPED** — `broken_links` feed_link (WARNING) — but measures **ZERO**, see below | 0 across all 16,564 cached pages |

## C. NEEDS JUDGEMENT — deliberately NOT building

We have measured twice what judgement-based checks cost (ARCHITECTURE.md D9, D10).

| Defect | Why not |
|---|---|
| Link text vs destination mismatch ("Read our Success Stories" → verify-insurance; "Laguna Beach" → Gallery) | requires knowing intent |
| Section content doesn't match its heading; FAQ answers swapped | semantic |
| Wrong topic on page (porn-addiction intro on a drinking-problems page) | semantic |
| California landmarks listed on Florida pages | needs a landmark→geo authority |
| Meta description doesn't mention Tennessee | editorial |
| Clinical stats with no citation | editorial |
| **B9 — two distinct addresses sharing one map link** | **Deliberately not built.** Detecting that one map href is used twice is trivial; deciding it is WRONG is not. Measured on the three likeliest pages: GL `/locations/` has 4 map links and 4 distinct targets (no duplicates at all); RR `/about-us/sober-living-gallery` lists 16 addresses and **zero** map links; DBH `/our_locations/` has neither. GL's links are `maps.app.goo.gl` shortlinks, so **the href carries no address** — confirming a mismatch would mean resolving each shortlink against Google and pairing it to an address by DOM proximity. That is a judgement call plus an external dependency, for a single report with zero observed instances. |
| "Are these sections supposed to be clickable?" | intent — and now **measured**: TDRC's homepage styles 14 amenity labels ("Paintball", "Hiking") as `elementor-button` with no href. They were never links. `dead_cta` therefore requires an ACTION PHRASE when the href is absent, and button styling alone is not enough. |

## B11 — shipped, but the reported phenomenon is a setting, not a page defect

The check is live and costs nothing: an `<a href>` in page content pointing at `/feed/`, `/rss/`
or `/atom/` is flagged. **It finds nothing.** Scanning every link target in the crawl cache —
16,564 pages across all nine brands — returned **zero** feed links.

That is not a gap in the check; it is the wrong surface. WordPress does not *link* to feeds from
page content, it **declares** them in `<head>` (`<link rel="alternate" type="application/rss+xml">`)
on every page, which is how Google found the 960 crawled-not-indexed URLs the client reported.
Flagging that declaration would produce a finding on every page of every brand for stock WordPress
behaviour — the definition of crying wolf.

**So the client's `/feed/` observation is real but is a one-time site-wide setting** (disable feeds,
or `noindex` them, in Rank Math / robots), not something a per-page content audit should report.
Worth passing to whoever owns SEO config; not worth 16,564 rows.

## D. NOT DETECTABLE WITHOUT A BROWSER — state the boundary to the client

The tool reads HTML. It does not render, execute JavaScript, or emulate a viewport. **These were
reported and we cannot see them** — the client should know that rather than assume they are covered.

* Button text unreadable against its background (contrast)
* Form visually cut off; widget formatting broken; content bold/oversized
* Mobile-only failures: Faculty Members widget, missing Therapists button, `/our-facilities/` layout
* Broken image icons in the mobile mega-menu
* Largest Contentful Paint regression on mobile
* **Whether a `<button>` works.** A `<button>` has no `href` by design — what it does lives in
  JavaScript, which this tool does not execute. Every `<button>` the dead-CTA check reached on live
  pages was working (GL's form Submit, COC's `relatedloadMoreBtn`, a modal's close "x"), so the
  check reads `<a>` elements only. A genuinely dead `<button>` is invisible to us.

## The footer-template corruption family

Three of the shipped classes are the same defect wearing different clothes, and they should be
reported to the client as ONE thing: **a footer/header template whose link fields were filled in
wrongly, so the error is on every page of the site at once.**

* `fused_url` — CAD's LinkedIn field contains a LinkedIn URL and a YouTube URL welded together.
* `social_misrouted` — GL's Instagram icon points at LinkedIn; RR's YouTube icon points at Instagram.
* `double_slash` — GL's `//facility/...`.

Nobody clicks their own footer, so these survive indefinitely. They are also the cheapest fix in
the whole audit: one template field, every page corrected at once.

## Recommendation

Build **B1, B2, B7** first: B1 is the largest single class (12 reports) and is confirmed still live
on the client's parent-brand homepage eight weeks after they reported it; B2 and B7 are string and
regex matches against defects we can already see. Then B3–B5, which share the "structure is present
but empty/duplicated" shape that `empty_slot` already owns.

**The root cause of B1 is a blind spot, not a missing feature.** `parse.py` collects links with
`find_all("a", href=True)` — an anchor with no `href` is discarded before any check runs, so a dead
button has never been findable by this tool.

**The discriminator matters more than the detection.** GL's `/locations/` page alone has 33
anchors with no `href` that are legitimate mega-menu toggles. Keying on raw missing-href would be
the cry-wolf failure this project has spent its whole life avoiding. Key on CTA-ness: button
classes/roles, action-phrase text, or button styling.
