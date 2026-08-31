# What the audit checks — every finding it can produce

**Written 2026-08-27. Every example below is a real finding from a live client page.**

Sixteen check modules produce about fifty-five distinct kinds of finding. This page lists all of
them: what each one catches, a real example, and — just as importantly — **how it avoids crying
wolf**, because a check that reports things that are not wrong is worse than no check at all.

Findings carry one of three severities:

* **ERROR** — certainly wrong. A visitor or a search engine is being given false or broken information.
* **WARNING** — probably wrong, or wrong in a way that needs a human to confirm.
* **INFO** — worth knowing, not a defect.

---

## 1. Phone numbers — the flagship

The check that started the project. Numbers are compared against the client's own NAP sheet,
including per-location facility lines.

| Finding | Catches | Real example |
|---|---|---|
| `cross_brand_dial` | a button that dials a **different brand** | DBH: *shows "Call Now! 844-759-0999" but dials +1 888 871 2088 (TDRC)* |
| `dials_retired` | dials a number that has been retired | GL: *shows "(844) 576-0144" but dials RETIRED +1 800 994 2184* |
| `display_dial_mismatch` | the number shown differs from the number dialled | DBH: *shows "844-759-0999" but dials +1 888 707 6073* |
| `stale_retired` | a retired number still printed on the page | GL: `+1 800 692 9850` |
| `unknown` | a number in nobody's set | AH: `+1 800 950 6264` |
| `display_dial_unknown` | shown and dialled differ, and the dialled one is in no brand's set | DBH |
| `third_party` (INFO) | a known crisis hotline — expected, not a defect | CAD: SAMHSA's `+1 800 662 4357` |

**How it avoids crying wolf.** Numbers are parsed with a real telephony library, not a regex, so
dates and prices are never mistaken for phone numbers. Known third-party hotlines are recognised
and reported as INFO rather than errors. A number claimed by two brands is reported as *ambiguous*
rather than blamed on one.

---

## 2. Links and buttons

| Finding | Catches | Real example |
|---|---|---|
| `broken` | a link returning 404 or 5xx | DBH: a `404` |
| `dead_cta` | a call-to-action that goes nowhere | DBH: *"Lisdexamfetamine Uses & Side effects"* button with no destination |
| `staging_link` | a link to the **staging site** on a live page | AH: `wordpress-1325662-4849104.cloudwaysapps.com/...` |
| `fused_url` | two web addresses run together | GL: `.../vyvanse-addictionhttps://www.gratitudelodge...` |
| `social_misrouted` | a social icon pointing at the wrong network | TDRC: `linkedin.co` + `youtube.com` fused into one link |
| `cruft_link` | a link to a leftover `-old` / `-copy` / `-delete` page | GL: `/adderall-detox-delete` |
| `malformed_link` | an address that is not an address | CAD: `http://Medication-assisted treatment` |
| `unreachable` | a link that timed out or failed to connect | CAD: an NIH archive page |
| `double_slash` | an address containing a doubled slash | GL: `…/california//orange-county/` |
| `unverified_external` (INFO) | an outside site we could not check (e.g. 403) | GL |

**How it avoids crying wolf.** Every link is deduplicated before being probed — a nav link appears
on 8,000 pages and is checked once. Sites that block automated requests are reported as
*unverified*, never as broken.

---

## 3. Unfilled template fields — the defect the client reports most

Two halves. One is visible; the other is much harder to see and much more common.

| Finding | Catches | Real example |
|---|---|---|
| `placeholder` | the raw template code left on the page | GL: `[acf field=near-in]` |
| `variable_name` | a variable **name** printed as text | DBH: *"See more about our 'TOPIC' program below"* |
| `empty_state` | a template's own "nothing here" message | CAD: *"No Content Found in this FIeld"* |
| `shortcode` | an unrendered shortcode visible on the page | GL: a `[…]` block printed as text |
| `orphan_comma` | a value vanished, leaving a dangling comma | GL: *"In Long Beach during , there were 5 news reports"* |
| `double_preposition` | a value vanished between two prepositions | *"…private insurance within of Aliso Viejo"* |
| `empty_percent` | a percentage with no number | CAD: *"representing a % change compared to ."* |
| `missing_unit` | a distance kept its number but lost its unit | CAD: *"seldom fit within 7, 10, or 14-day…"* |
| `not_found_sentinel` | the pipeline's literal "no data" text | GL: *"Among **Not found** people in Westminster…"* |
| `count_disagreement` | a count of 1 against a plural noun | CAD: *"**1 facilities** within 20 miles"* |

**How it avoids crying wolf.** These patterns were tightened against live pages, not invented. A
naive "preposition followed by preposition" rule fired on ordinary English like *"ratings of at
least 4"*, so it was narrowed to the two shapes that are never valid. The `Not found` match is
case-sensitive, because a 404 page says "Not Found" and ordinary prose says "not found".

---

## 4. Broken and corrupted text

Not a spellchecker. These find text that is structurally **damaged**, which is what a broken
publishing pipeline produces.

| Finding | Catches | Real example |
|---|---|---|
| `shattered_text` | spaces driven into the middle of words | *"Al ways fo llow t he inst ructions pr ovided by y our do ctor"* |
| `lorem_ipsum` | placeholder Latin on a live page | GL's `/local-business-page-dev/` — an unfinished dev page, publicly reachable |
| `doubled_word` | the same word twice | *"benefits will vary from from provider to provider"* |
| `missing_space` | two sentences joined | CAD: *"…like benzodiazepines.With roughly…"* |
| `run_together` | several words fused | TDRC: `@thedistrictrecoverycommunity` in body copy |
| `repeated_letters` | a letter three times over | GL: *"medically-asssited detox"* |
| `truncated_word` | a stranded fragment | CAD: *"Cardiovascular complication s include…"* |
| `stacked_punctuation` | two marks together | CAD: *"synthetic opioids, , California Detox…"* |

**How it avoids crying wolf.** Every one of these reads a single block of text, never the whole
page — because joining two separate elements creates defects no reader sees. That distinction was
not theoretical: reading whole pages produced 3,837 false "missing space" findings instead of 12.
Web addresses are excluded before matching, and legitimate English doubles ("had had") are named.

---

## 5. Spelling

Three previous attempts at spellchecking were built and rejected — a dictionary approach reached
only 9% precision, because rare drug names look exactly like typos. What works is different: **a
word used once or twice on a site that is one letter away from a word used hundreds of times.**

| Finding | Catches | Real example |
|---|---|---|
| `misspelling.body` | a confirmed misspelling | DBH: *"**Rennaisance** provided me every…"* — a sister brand's name, in a testimonial |
| `misspelling.slug` | a misspelling in the **web address** | CAD: `/trazadone` (should be trazodone) — needs a redirect to fix |
| `mined` | a typo found by rarity | COC: *"a mental health disorder **characterised** by…"* |
| `british_spelling` | British spellings where US is the standard | AR: `behavioural`, `centres`, `counselling` — reported as **one** finding, not five |

**How it avoids crying wolf.** The dictionary is used only to *rule things out*, never to accuse.
Rare clinical vocabulary is far from every common word, so it never fires. Place names before a
state code are excluded, plurals are excluded, and a known prefix on a known word is treated as
real English.

---

## 6. Structured data (what search engines read)

Invisible in a browser. On healthcare sites it is what local search results are built from.

| Finding | Catches | Real example |
|---|---|---|
| `schema.missing` | no business declared at all | DBH — on every page sampled |
| `schema.invalid_json` | markup a crawler silently discards | GL and COC — one contains JavaScript emitted *as* the schema |
| `schema.phone_cross_brand` | a sister brand's phone in the markup | see the known issue below |

**How it avoids crying wolf.** A bare `Organization` is not held to the same bar as a local
business, because contact details are only *recommended* there. Phone numbers are checked against
the full NAP sheet including per-location lines — using only the national numbers reported sixteen
of GL's own facility lines as wrong.

---

## 7. Page structure, content and coverage

| Finding | Catches |
|---|---|
| `heading_structure` | skipped heading levels, empty headings, body text inside a heading tag |
| `multi_h1` | more than one main heading |
| `meta` | missing or duplicated search titles and descriptions |
| `blank` | a page or section with no content |
| `empty_row` | a blank cell in an otherwise filled table |
| `duplicate_paragraph` | the same paragraph repeated on one page |
| `duplicate_link` | the same link repeated within one page |
| `sister_brand` | another brand named in this brand's copy |
| `county_for_country` | "county" written where "country" was meant |
| `sitemap_dead` | a page in the sitemap that no longer exists |
| `indexable_unsitemapped` | a live, indexable page missing from the sitemap |
| `noindex_unsitemapped` (INFO) | a no-index page missing from the sitemap — usually correct, reported for completeness |
| `cruft_noindex` | a leftover `-old`/`-copy` page missing from the sitemap, no-index limiting the harm |
| `rest_404` | WordPress lists the page but it returns 404 to the public |
| `redirected_internal` (INFO) | a link on your own site points at an address that redirects, instead of at the page itself — it works, but costs a round trip and stops the old address ever being retired |
| `collision_slug` | a page address ending in `-2`, which is what WordPress does when the address was already taken — usually a duplicate of another page |
| `redirects_off_brand` | a URL in this brand's sitemap that sends visitors to a different brand's website — nothing else on that page is audited, because the page that comes back is not this brand's |
| `business_name_internal` | the machine-readable business card names the business with an internal content-management label instead of the brand — invisible to visitors, wrong to search engines |
| `address_not_in_nap` | a street address published to search engines that is not on the brand's NAP sheet |

---

## Accessibility (analysed from the same HTML, no rendering)

| Finding | Catches | Real example |
|---|---|---|
| `link-name` | a link with no readable name | RR: `<a href="tel:629-299-2329">` — a **click-to-call a screen-reader user cannot identify** |
| `label` | a form field with no label | MHD, on every page sampled |
| `meta-viewport` | the page prevents zooming | AH |
| `aria-*` | list roles, hidden-but-focusable content, unnamed buttons | |

**How it avoids crying wolf.** Findings are collapsed by what the element points at, so a broken
logo link on 8,000 pages is one row, not 8,000 — measured, 2,274 raw violations become 185 rows.

---

## Known issues, stated rather than hidden

* **`schema.phone_cross_brand` currently reports 6 false positives on TDRC.** TDRC's
  `/review-us/…` addresses are deliberate redirects to sister-brand review pages. The finding
  records the *requested* address rather than the one it landed on, so the guard that should
  exclude them does not fire. Real, understood, and queued to fix.
* **Contrast and tap-target size are not checked.** They need the page actually drawn in a browser,
  which is a separate piece of work awaiting the client's agreement.
* **Spelling needs a reasonably large site.** On the smallest site no word occurs often enough for
  the comparison to mean anything, so it correctly reports nothing rather than guessing.
