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

## B. DETERMINISTIC AND **NOT** COVERED — build these

Ordered by how often the client reported them.

| # | Defect | Times reported | Detection | Verified live? |
|---|---|---|---|---|
| **B1** | **CTA/nav element with no destination** — `href` missing, `#`, or `javascript:` | **12** | `<a>`/`<button>` with CTA text and no usable href | **YES — 14 on districtbehavioralhealth.com today**, incl. `Verify Insurance` (`href="#"`) |
| **B2** | **Placeholder strings rendering as copy** — `No content found`, `No accordion items found`, `No Content Found in this Field`, literal `GEO`, `[sobriety_calculator]` | **6** (18+ URLs listed) | exact string / shortcode regex | placeholder.py **misses all five** (verified) |
| **B3** | **Duplicate content inside one page** — identical paragraph under different headings; step 2 = step 3; CBT text reused for Couples; 3 accordion entries sharing one description | **5** | normalised paragraph hash within a page | — |
| **B4** | **Empty structural slots** — blank table rows, blank accordion rows, "read more" with no read-more button, blank widget | **5** | empty `<td>`/`<li>`/accordion item beside populated siblings | — |
| **B5** | **Duplicate anchor text + href repeated** (nav rendered 3×; interlink widget listing the same link twice) | **3** | count identical (text, href) pairs per page | — |
| **B6** | **Wrong brand named in copy** — "Gratitude Lodge" on the Connections site | **2** | brand-name scan, exactly like `cross_brand_dial` for numbers | — |
| **B7** | **Double slash in URL** — `//facility/...` | **1** | path regex | **YES — live on gratitudelodge.com/locations/** |
| **B8** | **Social icons pointing at the wrong network** — LinkedIn and YouTube both → instagram.com | **1** | icon/aria label vs href host | — |
| **B9** | **Two distinct addresses sharing one map link** | **1** | duplicate href across different address blocks | — |
| **B10** | **Stale/`-old` URL leaking into nav** — `rehab-admissions-old/` | **1** | slug pattern + redirect check | — |
| **B11** | **`/feed/` URLs published** — 960 of 1,000 crawled-not-indexed | **1** | enumeration filter | — |

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
| "Are these sections supposed to be clickable?" | intent |

## D. NOT DETECTABLE WITHOUT A BROWSER — state the boundary to the client

The tool reads HTML. It does not render, execute JavaScript, or emulate a viewport. **These were
reported and we cannot see them** — the client should know that rather than assume they are covered.

* Button text unreadable against its background (contrast)
* Form visually cut off; widget formatting broken; content bold/oversized
* Mobile-only failures: Faculty Members widget, missing Therapists button, `/our-facilities/` layout
* Broken image icons in the mobile mega-menu
* Largest Contentful Paint regression on mobile

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
