# The sheet pipeline — investigation

**Investigation only. No design, no code. 2026-08-26.**
**Read-only throughout: nothing in `~/Documents/workk/district` was written, edited or executed.**

The question behind this: **can we build a sheet↔live field-presence check?** The short answer is
**no, not as a URL-keyed diff — the row→URL mapping is not reliable enough to key one on.** The
longer and more useful answer is that the investigation surfaced a *better* check that needs no
sheet at all. Both are below.

---

## 1. The 14 tabs, and which reach a live page

The docs say 12 tabs; there are **14**, and tabs 13–14 are undocumented.

**Six write content that reaches a live page:**

| Tab | Writes | Where it lands |
|---|---|---|
| 1. Content Generator | body copy | Text Content columns (AZ–HB), plus AF/AK/BC |
| 2. SEO Headings | `rank_math_title`, `h1`, `h2___head` | AF / AK / BC — the title tag and heading outline |
| 4. Geo Data Fetcher | geo statistics | the geo block; WordPress substitutes these into surviving `[acf field=…]` tokens at render |
| 8. Pre-Import → *"Fix Content Formatting"* button | rewrites cell **values** | AY–HH, HTML cleanup, `USER_ENTERED` |
| 12. Bulk Find & Replace | rewrites ACF **token names** | defaults to FI/FL/OP |
| 14. Infographic Generator | PNG + image URL | the **only** genuine WordPress write in the repo — media library only, never page bodies |

The other eight write borders, notes and cell colours only.

**Every QA tab gates nothing** — no downstream code reads a QA result. Known-bad content flows
straight through, which is the whole reason a post-publish auditor exists.

One nuance worth carrying: the annotation tabs are not risk-free. Cell background colour is a
load-bearing processing gate (below), and one QA tab's "clear highlighting" repaints backgrounds
pure white, which can silently change which rows get content generated on the next run.

---

## 2. Columns

| Cols | Holds |
|---|---|
| A | section marker |
| C | `near_in` (national / in / near / drive / fly) |
| D | location name — **and its background colour gates processing** |
| T | WordPress Page ID |
| V | "Permalink URL (Live Site)" |
| W | slug |
| X | parent |
| Y | parent_slug |
| Z–AE | taxonomy |
| AF, AK, BC | `rank_math_title`, `h1`, `h2___head` |
| AZ→HB | ACF body content |
| HI→OD | media |
| geo block | geo statistics — located by **searching row 4**, not by a fixed letter |

**Columns are identified two incompatible ways, and the code says so about itself.**
`config/sheet_columns.py` asserts the positions "are ALWAYS in the same position". The newer
modules refute it directly — one comment reads *"never hardcode a column letter, because column
positions drift between client sheets"*, and the infographic service resolves every column by
header name because one brand renames `overdose___population` → `drug_users___population`.

Three services also disagree about where the content block even starts: AZ–HB, AY–HH, and AX–HH.

**There is no per-brand layout configuration anywhere.** Only RR and GL appear in code at all, and
their entries carry differing column *names*. So the layout varies per brand in practice, with
nothing describing the variation.

---

## 3. How a row becomes text on a page

Content generation substitutes **exactly five tokens** — `geo`, `near-in`, `state`, `full_geo`,
`topic` — and deliberately preserves every other `[acf field=…]` for WordPress to resolve at render.
The Claude prompt explicitly instructs the model not to resolve tokens. Nothing else substitutes
anywhere; Bulk Find & Replace *renames* tokens, it never resolves them.

Token → value works by matching the token name against a **geo column header in row 4**, exactly and
case-sensitively.

**What happens when a value is missing is the single most important finding in this investigation,
and it is not what anyone assumed.** The geo writer almost never leaves a cell blank. On missing or
zero data it writes:

- the literal string **`'Not found'`** — the general case
- a **hardcoded default** for radius fields (15/25/40/75/155/10 by location type)
- **`'2024'`** for any year field, hardcoded
- **`random.randint(1, 2)`** for facility counts
- addiction population = **overdose deaths × 25**, or population × 1.5%
- `'0'` for `number_of_treatment_centers` — deliberately

So the realistic live-page defect is **not** an empty gap. It is prose reading *"there are Not found
treatment centers in Fresno County"*, or a fabricated statistic that looks entirely plausible.

And `'Not found'` **appears in none of the Fetcher's three "is this empty" predicates**, so it passes
every one of its QA checks clean.

---

## 4. What its QA already covers (so we don't duplicate it)

Seven pre-import checks (missing media, geo-variable consistency, slug format, missing page/parent
IDs, blank variables T–AE, blank content, content formatting), plus markdown-leakage detection,
empty/zero geo cells with paired-column rules, a copy-vs-main sheet diff, compliance regexes, and
readability heuristics.

**All of it operates on the sheet before import. Nothing inspects a rendered page.** The one module
that touches WordPress fetches `id,title,slug,link,parent,status` and *explicitly excludes*
`content`, so no rendered body is ever retrieved. **Our auditor's surface is genuinely net-new**, and
the Fetcher's own documentation names "the planned out-of-repo site auditor" as the intended fix for
leftover tokens.

---

## 5. Empty / zero / missing — the conventions are genuinely inconsistent

This was asked because a blank cell might mean "no value", "not applicable" or "not yet filled".
The finding: **the code does not consistently distinguish them, and in one region cannot.**

- **Three different definitions of "empty"** across three modules. `'0'` is empty in geo QA but not
  in the other two. `'select'` (an untouched dropdown) is empty in only one — the single place
  "nobody filled this in" is distinguishable from "no value".
- **Zero is unrecoverable.** Four modules collapse `0` into "no value". A county with genuinely zero
  overdose deaths is indistinguishable from one nobody researched. Worse, the geo writer
  deliberately writes `'0'` for treatment centres, and the geo QA is then guaranteed to flag that
  same cell as a defect.
- **"Not applicable" has no encoding at all** in the Variables or Geo regions. Only Text Content
  expresses it, and it does so by the *absence of a template row* rather than by any cell value.
- **`'Not found'` is the real sentinel** and no QA module knows about it.
- **Cell colour is a processing gate with four incompatible definitions** — strict white (≥0.99),
  a near-white band (0.94–0.97, pure white *excluded*), a continuous range (0.94–1.0), and ≥0.95.
  There is a dead zone between 0.97 and 0.99 that two of them classify as neither.
- **Template rows are detected six different ways**, with fallback start rows of 13, 12, 13, 13 and
  **56** depending on the module, and two modules disagree about whether the National row is a data
  row.

---

## 6. Can a sheet row be matched to a rendered page? **No — unreliably.**

Five independent reasons, each verified in code:

1. **No live URL is ever constructed from a row.** There is not one `urljoin` in the repo. The slug
   generator emits a bare slug with no parent path.
2. **Column V, "Permalink URL (Live Site)", has no writer anywhere.** It is read once and discarded
   into a JSON export. Populated by hand, if at all — and a blank V is only a WARNING.
3. **The parent chain cannot be walked.** X is never validated as numeric, is fetched but never
   read, and Y's advertised consistency check does not exist in the code that claims it.
4. **Column T — the only stable join key — has exactly one writer, and that code path is dead.**
   It sits behind a nested-Streamlit-button pattern that can never fire: clicking the inner button
   causes a rerun in which the outer button is False, so the block never renders. Any page ID in a
   sheet was typed by a human or came from outside this repo.
5. **The slug itself is unreliable.** The code substitutes a *generated* slug whenever the column is
   empty, matches on fuzzy titles because there is no title column, and strips full URLs and paths
   down to a last segment before comparing — because in practice the column contains all three
   shapes. "Slug mismatch" is a first-class expected outcome.

---

## 7. So: is a sheet↔live field-presence check buildable?

**Not as specified, and I would not build it.**

A field-presence diff needs a trustworthy row→page join. There isn't one. Building it anyway would
mean inventing the mapping — matching on fuzzy titles or reconstructed slugs — and every mismatch
becomes either a false "field missing on the live page" or a silent skip. Given the empty/zero
ambiguity in §5, even a *correctly matched* row often cannot tell us what the page should show:
we could not distinguish "this field is legitimately blank" from "nobody filled it in" from
"the value is 0".

Two further limits, worth stating even if the mapping were fixed:
- Authenticated WP access exists for **RR and GL only**, and both point at **staging**, not
  production.
- Because the Fetcher writes `'Not found'` and fabricated defaults rather than blanks, a
  *presence* check would pass on exactly the rows that are most wrong.

**What it would have caught that we would lose:** genuinely empty content cells that render as an
empty section, and per-field drift between what the sheet says and what the page shows.

### The better check the investigation found — and it needs no sheet at all

Every one of these is a **string on the rendered page**, detectable by the auditor we already have,
with no mapping, no credentials and no sheet access:

| Defect | What to look for | Why it matters |
|---|---|---|
| `'Not found'` in prose | the literal string in visible text | the pipeline's real "no data" sentinel; passes every Fetcher QA check |
| `'unknown'` as a topic | literal `unknown` where a topic belongs | written when the topic row is empty |
| `[acf  field=…]` (two spaces) | a leftover token the strict single-space substitution misses | **nothing anywhere flags this** — the Fetcher's validator skip-lists it and its substituter can't match it |
| hardcoded `2024` in a year slot | year fields defaulted | presented as current data |
| implausible facility counts | "1" or "2" treatment centres | `random.randint(1, 2)` |
| addiction population ≈ deaths × 25 | ratio check across the page's own numbers | a fabricated statistic on a healthcare page |

The first three are trivial string checks. The last three are arithmetic on numbers already on the
page. **This is the same reframe that worked for spelling** — stop trying to verify correctness
against an external source, and instead detect the specific residue that a broken pipeline leaves
behind.

---

## 8. Recommendation

1. **Do not build the sheet↔live diff.** The join is unreliable, and the investigation says so with
   evidence rather than suspicion.
2. **Do build the sentinel checks in §7.** They catch the same class of defect — a geo variable that
   failed — from the live page alone, and they catch the *fabricated* cases a presence check would
   have passed.
3. **Tell the client about the fabricated values**, separately from any tool work. Random facility
   counts, a hardcoded year and a deaths×25 population estimate are being published as fact on
   healthcare pages. That is an editorial and possibly compliance issue, not a QA finding, and it is
   not ours to fix silently.
