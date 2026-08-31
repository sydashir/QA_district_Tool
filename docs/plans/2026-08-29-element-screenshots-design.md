# Element screenshots for render-layer findings — design

**Design only. Nothing built. 2026-08-29.**
Every number below was measured this session, not estimated.

The problem: *"this text is #7a7a7a on #ffffff, 4.29:1"* is a true statement that a reader cannot
picture. A cropped screenshot of the element, outlined, is the same statement made legible.

**Scope limit, stated first:** screenshots make our existing deterministic findings *legible*. They
add no detection. Nothing here judges whether a design "looks off" — that is the judgement class
measured and rejected four times (D9, D10, D11, and the AI pass in D12's preamble).

---

## 1. Crop: element + 48px, outlined

Measured on a real flagged element (CAD, `<h3>` at 310x38 CSS px, `device_scale_factor=2`):

| padding | what it shows | PNG |
|---|---|---|
| 0px | **the outline is not even visible** — an outline draws *outside* the border box, so a tight clip cuts it off. No context. | 14.0 KB |
| 16px | outline visible, but the element floats with no surroundings — you cannot tell it is a card in a list | 15.7 KB |
| **48px** | **outline visible, element sits in its card, one neighbour visible for scale** | **19.6 KB** |
| 96px | more context, no new information for the reader | 33.7 KB (+72%) |

**Decision: 48 CSS px of padding**, element outlined `3px solid #ff2d55` with `outline-offset: 2px`,
`scrollIntoView({block:'center'})` first, clip intersected with the viewport, `device_scale_factor=2`
so small text is legible. Mobile viewport (390x844), which is where tap targets matter and where the
client's traffic is.

**The outline is injected AFTER every measurement on that page has completed, and removed after the
shot.** This is a D14 rule, not a style preference: the render layer has twice reported defects its
own preparation created. Injecting a style before axe runs would put our own outline into the
contrast and tap-target results.

## 2. Format: PNG, not JPEG — and the reason is not size

JPEG q60 at 48px padding is 14.5 KB against PNG's 19.6 KB (26% smaller) and looked identical to me
side by side. **Take the PNG anyway.** These findings are *about colour*: a lossy codec rewrites the
exact hex values the finding asserts, so a reader who samples the image can get a different colour
than the text claims. Do not distort the evidence you are presenting. 5 KB is not worth it.

## 3. Storage: one shot per COLLAPSED finding — and it is small

Contrast is already collapsed to a colour decision (2-8 rows per brand, measured; 37 across the 8
brands sampled). Tap targets are 18 raw and should collapse the same way. Broken images are 0.

- **~55-70 screenshots for all nine brands**, not one per page. The `#7a7a7a` pair covering 531
  instances gets ONE image.
- **~20 KB each -> ~1.1-1.4 MB total**, roughly 3-8 images and 60-160 KB per brand report.

**Marginal cost is near zero.** The page is already open for the render checks; a screenshot measured
**40-250 ms**. No extra page loads for render findings.

## 4. Embedding: base64, with a cap

| | report size | survives forwarding |
|---|---|---|
| base64 inline | ~33 KB -> ~210 KB worst case | yes — one file |
| separate files | stays ~33 KB + a folder | **no** — the images vanish the moment someone emails the HTML on |

These reports are delivered as a file someone sends to someone else. A report that loses its images
in transit is worse than one that is 180 KB larger. **Decision: base64 inline.** Cap the embedded
total at 4 MB per report; past that, drop the lowest-severity images and say in the report that some
were omitted — a silent truncation would imply the rest were fine.

## 5. Text findings: cheaper than expected, but blocked on a locator

**Cost is not the obstacle.** Measured page render (load + scroll + settle + one shot): **10.1s
median**. 1,208 distinct pages carry visually-demonstrable text findings (`display_dial_mismatch` 574,
`empty_state` 740, `dead_cta` 198, overlapping) = **3.4 hours** if every page were rendered — and
only **~10 minutes** if the same collapse discipline is applied and one exemplar page is rendered per
collapsed group.

**The real obstacle is that text findings have no element locator.** Render findings carry an exact
CSS selector from axe. Text findings carry only text and attributes:

| class | what is stored | locatable? |
|---|---|---|
| `dead_cta` | `tag`, `href="#"`, `label` | yes — `a[href="#"]` matching the label |
| `display_dial_mismatch` | `tel`, `displayed` | yes — `a[href^="tel:"]` matching the number |
| `empty_state` | `matched` ("No content found.") | yes — exact text search |
| `misspelling` / `mined` | one word in the body | **no** — a word can occur many times |

So a text screenshot means re-deriving the element with a second, fuzzier locator, and **a screenshot
of the wrong element is worse than no screenshot** — it is a confident-looking picture that proves
something we did not find. Same family as D14.

**Recommendation: ship text screenshots only for attribute-anchored classes** (`dead_cta`,
`display_dial_mismatch`, `empty_state`), and **only when the locator resolves to exactly one visible
element**; otherwise omit the image and keep the quoted snippet. Never for word-level classes.

`display_dial_mismatch` is the one worth doing first: 574 pages, it is the flagship defect, and
"shows *Call Now! 844-759-0999* but dials 888-707-6073" is far more damning as a picture of the
actual button.

### The gate must count its own misses (Syed, 2026-08-29)

The exactly-one-match gate silently omitting an image looks identical to a class that has no images
to show. **So the gate keeps a tally per class: attempted, matched-exactly-one, matched-none,
matched-many** — reported at the end of a screenshot pass and stored on the run.

This is the difference between a feature and a feature that fires. If `dead_cta` resolves cleanly 90%
of the time and `empty_state` 30%, then `empty_state` screenshots are not a capability, they are an
occasional accident, and we either fix the locator or drop the class. Without the tally the two are
indistinguishable from the outside — the same trap as a silent sampling cap, which this project
already rules must always be reported.

The tally is also the acceptance test: **a class whose locator resolves below ~70% does not ship**,
on the same principle as the 80% precision bar. Measure first, then decide — do not assume the
attribute-anchored classes will behave alike just because they all have attributes.

## 6. What could go wrong

- **A screenshot of the wrong element.** Mitigated by the exactly-one-match gate above.
- **Our own outline polluting a measurement.** Mitigated by injecting only after measurement.
- **A page that renders differently on the screenshot pass than on the audit pass** (A/B tests are
  blocked by the guard, but content changes between runs). The image is evidence of what the page
  looked like when shot, and the report should date it.
- **Report bloat** past the 4 MB cap — handled explicitly, never silently.
- The guard applies unchanged: canary-proven attachment before any client page is opened.

## 7. Build order, if approved

1. `render/shots.py` — outline, clip, capture, base64. Pure, unit-testable against fixtures.
2. Wire into the existing render pass (no new page loads) for contrast and tap targets.
3. `scripts/client_report.py` — render `<img src="data:image/png;base64,…">` under the finding,
   with the cap and the omission notice.
4. The locator tally (§5) — built WITH the first text class, never bolted on afterwards, because
   its whole purpose is to decide whether that class ships.
5. Only then consider `display_dial_mismatch`, behind the exactly-one-match gate.

**Sequencing: none of this starts until the merge queue is clear** — merge `buildlist`, re-run the
NAP backfill, regenerate all nine client reports, then screenshots. Building mid-MHD is what the
worktree exists to avoid.


---

## MEASURED RESULT — 2026-08-31

Built in §7 order. Render-finding screenshots ship. **Text-finding screenshots do not.**

### Shipped: render findings

`render/shots.py` — 48px padding, PNG, outline applied only after measurement and removed in a
`finally`. `attach_shots` de-duplicates by *picture*, not by finding: contrast collapses to a colour
pair, broken images to a src. Measured on TDRC, 8 pages: **44 contrast nodes → 11 shots**, locator
**11/11 (100%)**.

Verified on CAD's live homepage that the outline does not contaminate anything: axe reported **34
violations before the shots and 34 after**. That was D14's fifth-instance risk, and there is now a
test that fails if the outline survives a capture.

`scripts/client_report.py` embeds them as base64 PNGs under the finding, with a 4 MB budget per
report and a notice naming how many were dropped if it ever binds. Rendered findings also needed a
report section — `contrast`, `tap_target` and `broken_image` matched no section key and would have
been **silently dropped from every report**.

### NOT shipped: `display_dial_mismatch` — 50%, against a 70% floor

Measured on **14 real findings from the live sites**, and the tally is the only reason this is known:

| attempt | located | why the misses |
|---|---|---|
| exactly-one-match gate | **0 / 14 (0%)** | 12 refused as "ambiguous", 2 not found |
| after allowing repeats of the SAME defect | **7 / 14 (50%)** | 7 not found |

The first relaxation was legitimate and the tally is what exposed the need for it: candidates pinned
to BOTH the dial target and the displayed text are the same defect repeated — a CTA in the header,
the sticky bar and the footer — so photographing the first is correct, not a weakened gate.

**The remaining 50% cannot be fixed by relaxing anything, because the premise is wrong.** The
locator assumed the displayed number appears in the link's own text. On the real pages it often does
not: `connectionsoc.com` carries `<a href="tel:844-759-0999">Call For Treatment</a>` six times, and
the finding's `displayed` number (`657-298-1982`) is elsewhere on the page entirely. The phone check
pairs a `tel:` target with a number rendered somewhere else; the link's label is frequently prose.

Crossing the bar from here would mean matching on the dial target alone, which brings back genuine
ambiguity — several different links, different labels, one arbitrary choice. **That is precisely the
"confident-looking picture of something we did not find" this gate exists to prevent**, so it is not
worth 20 percentage points.

`mark_tel_mismatch` / `capture_tel_mismatch` are kept and tested — parked, in the same spirit as
`SPELLCHECK=1` — so the measurement can be repeated if the locator premise is ever revisited. They
are not called from any shipping path.

**What would change the verdict:** storing a selector, or an offset, on the finding at audit time.
The text layer knows exactly which anchor it flagged; it simply does not record it. That is a change
in `checks/phone.py` (hashed) and would want batching with other check work rather than doing alone.
