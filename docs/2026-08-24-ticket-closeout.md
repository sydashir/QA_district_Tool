# Automated QA tool — what was asked for, and what it does

**For the client. Written 2026-08-24. Closes ClickUp `86bapv5yn`.**

The ticket asked for an automated tool to catch grammar, spelling and broken-link problems across
the nine District sites. All six lines of it are now covered by checks that run on every page of
every site. This page says plainly what each one does, how it was proven, and where it stops.

One thing worth saying at the top, because it shaped everything else: **the approach that worked
was not AI.** Three attempts at "read the page and judge whether the writing is correct" were built
and rejected on measured evidence. What works is the opposite question — *is this text broken?* —
and it is ordinary deterministic code. The failures are documented further down, because they are
the reason to trust what shipped.

---

## The six lines of the ticket

| Asked for | Status | What it actually catches |
|---|---|---|
| **Grammar** | ✅ | Structurally broken text: doubled words, sentences welded together with no space, stacked punctuation, paragraphs with spaces driven into the middle of words, placeholder Latin left on a live page |
| **Spelling** | ✅ | Corrupted and misspelled words, found by how rare they are on the site rather than by a dictionary — including drug names and the brands' own names |
| **Punctuation** | ✅ | Stacked and orphaned marks (`addictive?.`), and the dangling commas an empty template field leaves behind |
| **Broken links** | ✅ | Every `<a href>` on every page, internal and external, including redirects that land on a 404 — deduplicated so a nav link is checked once, not 8,000 times |
| **Phone numbers** | ✅ | Visible numbers and `tel:` links compared against the canonical NAP sheet, including the display-vs-link mismatch that started this, and numbers dialling a *sister brand* |
| **Template variables** | ✅ | Both halves: the literal `[acf field=…]` token left unresolved, and the harder case where it resolved to nothing and left broken prose behind (`"within  of California"`) |

---

## Grammar and spelling: what actually works, and why it took four attempts

### The three that failed

These were built or piloted and then rejected. Each is written up in full in `ARCHITECTURE.md`.

1. **An AI pass over the page text.** It worked, in the sense that it returned opinions. It
   editorialised about word choice — preferring one phrasing over another — rather than finding
   errors. Judgement, not defects. Rejected.
2. **A dictionary spellchecker.** 9% precision. The reason is specific and instructive: this
   corpus is full of rare, correctly-spelled clinical vocabulary (`isotonitazene`, `solriamfetol`,
   `buprenorphine`). To a dictionary, a rare correct drug name and a typo look identical. Nine
   false alarms for every real one is worse than no check at all.
3. **A rule-based grammar engine.** Researched in depth. Its spellchecker reproduces failure 2
   exactly, and the only configuration worth piloting had no published precision figures from
   anyone. Not built.

The common thread: all three tried to decide whether text was **correct**. That is a judgement, and
judgement is where they each broke down.

### The fourth, which shipped

The question changed to: **is this text broken?** That is a fact about structure, not a matter of
opinion, and a damaged publishing pipeline produces exactly that kind of damage.

Measured on 876 live Gratitude Lodge pages (1.7 million words), then re-verified by re-fetching
every hit to confirm it was really on the page:

- **59 of 60 findings were real defects — 98%.**

What it found, all of it live on the public site:

- **An unfinished development page, publicly reachable, containing 30 blocks of "lorem ipsum"
  placeholder Latin.** See the note below — this one is not a typo.
- **`Graditude Lodge`** — the brand's own name, misspelled.
- **A whole paragraph broken apart**: *"Al ways fo llow t he inst ructions pr ovided by y our do
  ctor"*.
- **Drug names misspelled**: `trazadone` (trazodone), `Percoet` (Percocet) — the same class of
  defect that was originally reported by hand.
- Sentences welded together (`deaths.These numbers`), doubled words (`opioid opioid`,
  `from from`), and `medically-asssited`.

The spelling half works by a signal the site provides for free: **a word used once or twice on a
site, that is one letter away from a word used hundreds of times, is a typo — not vocabulary.**
`treatmnet` is one letter from `treatment` (used 29,341 times). `isotonitazene` is one letter from
nothing at all, so it is never accused. That single distinction is why this succeeded where the
dictionary failed, and it is measured: replacing it with an ordinary dictionary drops precision
from 92% to 42%.

This runs across the **whole site at once**, not page by page, because "used twice on this site" is
not something a single page can know.

---

## ⚠️ Separate item: an unfinished dev page is publicly live

**`gratitudelodge.com/local-business-page-dev/`**

This is not a typo and should not be triaged as one. It is an unfinished development page that is
publicly reachable, containing 30 blocks of `lorem ipsum` placeholder Latin where the copy should
be. Anyone — including a search engine — can reach it.

Two separate problems:

1. **The content is placeholder text**, which reads as an abandoned or broken page to anyone who
   lands on it.
2. **It should not be reachable or indexable at all.** A page with `-dev` in its URL is not
   intended for the public. Whatever process publishes pages allowed a working draft onto the live
   site, which means there may be others.

Recommended: unpublish it rather than fix the text, and check how it came to be published.

---

## ⚠️ Separate item: five of the nine sites are written in British English

Not a typo either, and not nine separate content tickets. The audit found British spellings on
**AR, DBH and RR** as a group, plus single instances on **COC** and **CAD**:

> `behaviour`, `behaviours`, `centre`, `centres`, `counselling`, `personalised`, `prioritise`,
> `recognise`, `recognises`, `stabilisation`, `characterised`

`behaviour` appears on three of those sites and `personalised` on two. **Five of nine sites sharing
the same non-US spellings is a shared writer or content source, not five coincidences.** The stated
standard for these sites is US English.

The practical consequence: fixing the pages does not fix the cause. Unless whatever produces this
copy is set to US English, new pages will keep arriving the same way. The audit reports it as one
finding per site rather than one per word, so the size of the editing job is visible without
burying the genuine typos underneath it.

---

## Why the smallest site has no spelling findings

This is worth stating plainly, because it looks like a gap and is not one.

The spelling check works by comparing rare words against common ones **on the same site**. That
needs a site with enough writing for words to recur. Measured across the nine:

| The District Recovery Community | Addiction Hotline | Renaissance Recovery |
|---|---|---|
| 4,455 words — **0** words used 100+ times | 204,853 words — 209 | 3,596,141 words — 1,612 |

On the smallest site **not one word is used 100 times**, so there is nothing to compare anything
against and the check reports nothing at all. That is the check declining to guess on a site too
small to support the method — not a clean bill of health, and not a defect it missed. The other
checks (broken links, phones, punctuation, template variables) run on that site normally.

The same applies to the one site that is deliberately sampled rather than crawled whole: a sample's
word counts are not the site's word counts, so the spelling check is switched off there rather than
run on numbers that would mislead it.

---

## Where the checks stop

Being told "the audit found nothing" is only useful alongside what it was never looking at.

- **It reads the HTML a page sends to a browser.** It does not run JavaScript, so anything drawn
  in after load is invisible to it.
- **It does not judge whether writing is good.** No check has an opinion about tone, clarity or
  word choice — deliberately, after the failures above.
- **It cannot spot a plausible wrong word.** "Their" for "there", or a county named instead of a
  country, are both perfectly ordinary English. Nothing about them is *broken*, so nothing here
  catches them.
- **Call-tracking number swapping is not audited.** The number hard-coded in each element is
  checked; what the tracking script swaps it to at runtime is not.
- **Two sites are audited with known limits.** One is crawled from a hand-maintained page list
  because its rebuild removed the sitemap, so pages published since that list was written are
  never seen. Another is deliberately sampled rather than crawled whole, because its server
  becomes unreliable under load — its results are labelled a partial sample everywhere they appear.
- **Sites too small to mine** — see the section above for the numbers.

A fuller version of this list is in `WHAT_THE_AUDIT_DOES_NOT_CHECK.md`.
