# Rendering layer — design

**Design only. No code written. 2026-08-25.**
**Decisions returned 2026-08-25 and folded in — see §10 for what was decided and what changed.**

Everything in `docs/WHAT_THE_AUDIT_DOES_NOT_CHECK.md` is invisible because the auditor reads the
HTML a server sends and stops. A real browser renders it. This document designs that layer, answers
the five decisions that were asked for rather than assuming them, and proposes a build order.

**The headline of the research: most of this should not be built.** axe-core already implements two
of the requested checks *and* already implements the false-positive handling that would otherwise
have to be designed from scratch. What is genuinely net-new is a much shorter list.

---

## 1. Build vs buy — what the existing tools actually cover

Checked against the libraries themselves, not from memory.

### axe-core — use it, and it is better than expected

`color-contrast` and `target-size` are real axe-core rules. The important discovery is **how they
handle the ambiguous cases**. axe-core returns three outcomes, not two: `violations`, `passes`, and
**`incomplete`** — "I could not determine this." The incomplete reasons are exactly the false
positives this design was asked to solve, verbatim from
`lib/checks/color/color-contrast.json`:

| axe messageKey | meaning |
|---|---|
| `bgImage` | background colour could not be determined due to a background image |
| `bgGradient` | …due to a background gradient |
| `imgNode` | …because the element contains an image node |
| `bgOverlap` / `elmPartiallyObscured` / `elmPartiallyObscuring` | …because another element overlaps it |
| `outsideViewport` | …because it is outside the viewport |
| `fgAlpha` | foreground colour has alpha transparency |
| `complexTextShadows`, `pseudoContent`, `colorParse`, `shortTextContent` | other undecidable shapes |

So the "contrast on a background image" false positive **does not need a veto we invent** — axe
already refuses to rule on it. Our job is to **report `violations` only, and drop `incomplete`**
(or hold it in a separate, clearly-labelled low-confidence bucket). That matches the project's
existing cry-wolf standard exactly, and it is the same discipline already used for a partial CSS
read and a partial sitemap read: when the tool cannot know, it says nothing rather than guessing.

**Two things about `target-size` that change the spec as written:**

1. It tests **24×24 CSS px**, not 44×44. 24×24 is WCAG 2.5.8 *Target Size (Minimum)*, level AA.
   **44×44 is WCAG 2.5.5 *Target Size (Enhanced)*, level AAA** (and separately the Apple HIG
   guidance). These are different standards, and the request specified 44.
2. It is **off by default** — it ships behind the WCAG 2.2 tag and must be explicitly enabled.

It also does more than measure a box: a small control **passes** if it has enough clear space
around it, which is the correct reading of the criterion and kills a whole class of false positive
we would otherwise have generated on tight icon rows.

> **Decision needed from you:** 24×24 (AA, defensible against a published standard, fewer findings)
> or 44×44 (AAA/Apple, stricter, many more findings on any normal site)? My recommendation is
> **ship 24×24 as ERROR** and, if wanted, 44×44 as a separate INFO class — because a 44 failure is
> a design opinion the client has not signed up to, while a 24 failure cites a standard.

### Lighthouse — no

Lighthouse bundles axe-core for its accessibility score, so using both duplicates the same engine
while adding a heavyweight performance run we did not ask for. Speed (item 5 in the boundary doc) is
real but is a different product with different thresholds and far more noise. **Out of scope for v1.**

### BackstopJS / Percy — no

Playwright has visual comparison built in (`toHaveScreenshot`): it pins the browser build to the
library version, disables CSS animations, and waits for fonts before capturing — the three largest
sources of screenshot flake. BackstopJS adds a separate diffing/reporting layer we do not need;
Percy and Chromatic add **hosted baseline review**, which is a paid dependency and puts client page
images on a third-party service. Given the standing rule about what may leave this machine, that is
a hard no. **Use Playwright's own comparison, store baselines ourselves.**

### Net: what is actually net-new

| Check | Source |
|---|---|
| contrast ratio | **axe-core** `color-contrast` |
| tap targets | **axe-core** `target-size` (plus alt threshold if 44 is wanted) |
| broken images (`naturalWidth === 0`) | **build** — trivial DOM probe, no tool needed |
| element overflow / clipped text | **build** — `scrollWidth > clientWidth` + overflow style |
| overlapping elements | **build** — bounding-box intersection, heavily vetoed (see §6) |
| off-screen at mobile viewport | **build** — bounding box vs viewport |
| dead JS buttons | **build** — nothing does this; it is the most valuable and most dangerous |
| forms actually post | **build** — see §3, and the answer is *partly* |
| visual regression | **Playwright** `toHaveScreenshot`, our own baseline store |

---

## 2. Scale — measured, not estimated

Page counts from each brand's cached URL list; template counts computed from URL path shape, which
is the same collapse idea `audit.py` already uses for findings.

| brand | pages | templates (coarse) |
|---|---|---|
| RR | 8,081 | 192 |
| GL | 3,578 | 226 |
| CAD | 1,239 | 495 |
| COC | 1,520 | 56 |
| MHD | 1,275 cached (**15,635 real sitemap**) | 100 |
| DBH | 576 | 25 |
| AR | 510 | 176 |
| AH | 181 | 167 |
| TDRC | 19 | 11 |
| **total** | **~31,000 real** | **~1,450** |

At a measured-elsewhere 2.5s/page for a bare render, and ~4s/page once axe-core, a mobile viewport
pass and a screenshot are included:

| option | pages rendered | cost @4s | verdict |
|---|---|---|---|
| **every page** | ~31,000 | **~34 hours** | No. Longer than the entire text audit of all nine brands, for a defect class that is template-wide by nature. |
| **one page per template** | ~1,450 | **~97 minutes** | **Recommended.** |
| one per template, ×2 viewports | ~2,900 | ~3.2 hours | Recommended for the mobile-specific checks only. |
| key pages only (home, contact, top 20/brand) | ~200 | ~13 minutes | Too narrow — misses the geo templates where the defects actually live. |

**Recommendation: per-template, two viewports, and it is the right unit for a reason beyond cost.**
Every visual defect in the boundary doc was a *template* problem showing on many pages at once — the
Faculty Members widget, the mobile mega-menu, `/our-facilities/`. Rendering 20 pages of the same
template 20 times to find the same defect is exactly the duplication the finding-collapse layer
already exists to undo. Rendering one and reporting `page_count` from the template's real size gives
the same information at 3% of the cost.

**The sampling rule must be recorded per finding**, the same way `partial_sample` already is: a
template-level finding says "this template is broken, and it is used on N pages", not "N pages are
broken", because only one was looked at.

---

## 3. Production safety — the biggest risk in the design, and it starts before any click

**The problem is larger than the request framed it.** Clicking and submitting are the obvious
hazards, but merely *rendering* a page with JavaScript enabled is already a data-integrity event:

- **Google Analytics / GTM fires a pageview on load.** Rendering ~2,900 template pages injects
  ~2,900 fabricated sessions into the client's analytics, with a bot-shaped navigation pattern.
  Repeated nightly, that is a permanent distortion of their reporting.
- **CallTrackingMetrics DNI runs on load.** The tool's own notes record that every brand uses CTM
  dynamic number insertion. Each render is a tracked session that may consume a number-pool slot.
- Facebook/Meta pixels, Hotjar-style session recorders, and any consent-banner logic all fire too.

The current HTML-only auditor has never had this problem, because it never ran a line of JavaScript.
**A rendering layer must therefore block trackers on every page load, not merely around clicks.**

### The mechanism (verified in the Playwright API, not assumed)

`page.route(pattern, handler)` intercepts every request before it leaves the browser, and
`route.abort('blockedbyclient')` stops it. So:

1. **Deny-list every analytics and tag host** — GA/GTM, Meta, CTM, Hotjar, and anything else the
   sites load — aborted at the network layer on every render. Nothing reaches the client's
   reporting. This is a hard precondition, not a setting: **the render layer must refuse to start
   if the deny-list fails to load.**
2. **Allow-list-by-default for first-party assets**, so the page still renders truthfully — CSS,
   fonts and images must load or contrast and layout results are worthless.

### Dead buttons: safe, with care

Click, then assert something observable changed (URL, DOM mutation, a dialog appearing). With
trackers already blocked and third-party navigation intercepted, a click's blast radius is a page
we then discard. **Two rules:** never click anything whose accessible name matches a destructive
verb (delete, remove, cancel, unsubscribe, opt out), and never click while authenticated — the
render layer runs logged-out, as an anonymous visitor.

### Forms: the honest answer is **we cannot verify a real submission on production, and should not try**

The request asked "does submit actually post and return success". Those are two halves and they
differ completely in risk:

- **Does it post?** — Safe and worth doing. Intercept the submit request, capture its method, URL
  and body (`request.postDataJSON()`), then **abort it**. We learn that Submit is wired, and to
  where, and the request never leaves the machine. This catches the real defect class: a Submit
  button that posts nowhere.
- **Does it return success?** — **Not safely testable on production.** Letting it through creates a
  genuine lead in the client's CRM, an email to an intake team, and potentially a call-back to a
  fake person. A treatment-centre intake form is close to the worst possible thing to submit test
  data into. No amount of `test@example.com` makes that acceptable: the row still lands in their
  funnel, and somebody may act on it.

**Recommendation: scope server-side form validation OUT.** Report "Submit posts to `<endpoint>`" or
"Submit posts nowhere", and state plainly in the client doc that end-to-end form delivery is not
covered. If the client ever wants the full path tested, the correct venue is a **staging site** —
and the tool's own notes record that authenticated staging endpoints already exist for RR and GL.
That is a separate, later piece of work with the client's explicit agreement, not something to
switch on quietly against production.

---

## 4. Visual regression — and what a baseline can honestly claim

A baseline is only meaningful if it was correct, and nobody is going to hand-verify ~1,450 template
screenshots. Pretending otherwise would bake today's defects in as "correct" forever.

**So do not claim correctness. Claim change.** This is precisely what the existing diff layer
already does for findings: it never asserts a finding is right, it asserts the set changed. Visual
regression should be framed and worded the same way:

- The first run stores baselines and reports **nothing**. It is explicitly a capture run, recorded
  as such, in the same spirit as a labelled partial sample.
- Subsequent runs report **"this template's rendering changed since <date>"** — never "this template
  is wrong". The finding's whole value is drawing a human's eye to a change they did not expect,
  usually right after a deploy.
- Because it cannot assert correctness, it ships as **INFO severity**, never ERROR.
- A deliberate redesign will light up dozens of templates at once. That is correct behaviour and the
  wording must say so, with a one-click "accept these as the new baseline" path. Without that,
  the first redesign makes the whole check noise and it gets ignored.

**Flake control is the make-or-break.** Rendering must be pinned — same Playwright version, same
browser build, same container image, same viewport, same fonts — or OS-level font differences will
produce diffs on every run. Carousels, animations and any "N reviews today" counter must be masked
or the check is worthless; Playwright can mask specific selectors. A tolerance threshold is required
rather than pixel-perfect matching.

---

## 5. Where it runs

Playwright is not a pip install: it needs real browser binaries (~1–1.5 GB for Chromium plus its
dependencies) and roughly 300–500 MB of RAM per concurrent browser context.

The current deploy target is a small VM already running Postgres, the API and the audit worker.
**Recommendation: same box, but strictly bounded** —

- Run it from the **official Playwright container image**, which is also what makes screenshots
  reproducible (§4). Do not install browsers onto the host.
- **Concurrency 2–3**, never more; the render pass must yield to the text audit, which is the
  product's core function.
- Schedule it **off the nightly audit window** so the two never contend for CPU.
- It is a **separate pass over the same URL list**, not a replacement for the crawler. The
  HTML-only auditor stays exactly as it is: it is 15× faster per page and covers every page rather
  than one per template.

If the box cannot take it, the fallback is a second small worker rather than degrading the text
audit — but the numbers above (97 minutes, twice a week) do not suggest that will be necessary.

### One architectural constraint that must not be missed

`auditor/checks_version.py` hashes every file in `auditor/checks/` plus `parse.py`, `report.py`,
`nap.py`, `crawl.py` and `audit.py`. **The render layer must therefore live in its own package
(`render/`), outside `auditor/checks/`.** If it is added as a check module, every edit to it
invalidates every brand's text-audit resume cache — a multi-hour re-crawl for a change that has
nothing to do with the text audit. It should emit the same `Finding` shape and flow into the same
report/diff/publish pipeline, but its source must not be part of the text checks' version.

---

## 6. False positives — the vetoes, designed now

The pattern from the typo work applies: assume every check will fire wrongly, and design the veto
before shipping, not after.

| check | how it fires wrongly | veto |
|---|---|---|
| **contrast** | text over a background image, gradient, or a transparent foreground | **None needed — axe returns `incomplete`, and we report `violations` only.** This is the single biggest win from using axe rather than building it. |
| **tap targets** | icons in a deliberately tight row | axe passes a small target that has enough *spacing*; that logic is already in the rule. Additionally exclude anything not visible in the viewport. |
| **overlap** | intentional layered design — a card over a hero, a badge on a thumbnail, sticky headers | Flag **only** where overlap plausibly harms: (a) both elements contain text, (b) the overlap hides a meaningful fraction of one, (c) neither is `position: sticky/fixed`, (d) neither is a known decorative/`aria-hidden` node. Start ERROR-free: ship as INFO for one run and measure before promoting. |
| **off-screen at mobile** | carousel slides, off-canvas menus, tab panels, modals — all legitimately positioned outside the viewport | Ignore anything inside a carousel/slider/tab/modal container, anything `aria-hidden`, anything with zero size, and anything whose ancestor has `overflow: hidden` with transform (the carousel signature). Only flag content that is off-screen **and** reachable in the tab order **and** not in a recognised widget. |
| **overflow / clipped text** | deliberate line-clamp and "…" truncation, which is a design pattern not a defect | Skip elements with `-webkit-line-clamp`, `text-overflow: ellipsis`, or an explicit `overflow: hidden` paired with a set height — those are intentional. Flag only *unintended* clipping: content exceeding a container that has no clipping style. |
| **broken images** | lazy-loaded images that have not entered the viewport yet | Scroll the page to trigger lazy loading and await network idle **before** probing `naturalWidth`. Otherwise this check reports the whole site as broken. |
| **dead buttons** | a button that legitimately does nothing visible (analytics-only, or opens a native dialog) | Require a *positive* signal of deadness: no URL change, no DOM mutation, no dialog, no network request — over a generous wait. Anything ambiguous is not reported. |
| **visual regression** | any dynamic content — dates, counters, rotating testimonials, ads | Mask known dynamic selectors; require a diff ratio above a tuned threshold; INFO only. |

The measured lesson from the text work applies directly and should be written into the build: **the
first run of each new check is a measurement, hand-classified, not a shipment.** A check that cannot
clear the project's 80% precision bar on a real brand does not ship as ERROR.

---

## 7. Cost summary

| item | cost |
|---|---|
| Render pass, per template, 2 viewports | **~3.2 hours** per full network run |
| Disk — screenshots, ~1,450 templates × 2 viewports × ~200 KB | **~600 MB** per baseline generation |
| RAM | ~1 GB at concurrency 2–3 |
| Browser install | ~1–1.5 GB, in a container |
| New dependencies | `playwright`, `axe-core` (both permissively licensed; unlike the GPL grammar library previously rejected) |
| Cache impact on the existing text audit | **zero**, provided the layer lives outside `auditor/checks/` |

---

## 8. Build order

Deliberately ordered by *value per unit of risk*, with the dangerous items last and gated.

1. **Render harness only.** Container, URL-list reuse, template sampler, tracker deny-list, and a
   hard refusal to run if the deny-list is not active. No checks. Prove the safety layer first, by
   asserting in a test that zero requests reach any analytics host.
2. **axe-core pass** — contrast + target-size, `violations` only. This is the largest coverage gain
   for the least new code, and Connor's "unreadable button" report lands here.
3. **Broken images**, after lazy-load scrolling. Simple, high precision, immediately useful.
4. **Overflow / clipped text**, then **off-screen at mobile** — measure precision on one brand,
   hand-classify, ship only what clears 80%.
5. **Overlap** — the most false-positive-prone; INFO for a full run before it is allowed to be ERROR.
6. **Dead buttons** — only after the safety layer has been proven in production for a while.
7. **Forms, client half only** — "does Submit post, and where". Never the server half.
8. **Visual regression** — last. It needs everything above to be stable, and its baseline is only
   worth capturing once the render environment is pinned and reproducible.

Stop after any step and the layer is still coherent and useful.

---

## 9. Open questions for you

1. **24×24 (WCAG AA) or 44×44 (AAA/Apple) for tap targets?** They are different standards and the
   request specified 44. My recommendation is 24 as ERROR, 44 as optional INFO.
2. **Is the analytics deny-list acceptable as the safety mechanism**, or should the render layer
   only ever run against staging? Blocking is verifiable and testable, but it is our code deciding
   what not to send from the client's own site.
3. **Visual regression at all in v1?** It is the most expensive item, the least certain, and the
   only one that cannot assert correctness. It could reasonably be deferred entirely.
4. **Does the client know** we would be rendering their pages? Even with trackers blocked, this is a
   change in how their infrastructure is being used, and it seems worth their agreement rather than
   ours.

---

## 10. Decisions (returned 2026-08-25) — these supersede the open questions in §9

### 10.1 Tap targets — 24 is the error, 44 is a recommendation

**24×24 (WCAG 2.5.8 AA) ships as ERROR. 44×44 ships as optional INFO, off by default, and
labelled a design recommendation rather than a failure.** 44 is never an error: *we do not get to
assert a standard the client has not adopted.* This is the same line the tool already holds
elsewhere — it reports what is broken, not what it would have preferred.

### 10.2 The deny-list — necessary, not sufficient, and the highest-risk thing here

Four requirements, all of which are build-blocking:

1. **Refuse to start** if the deny-list is not active.
2. **Assert it worked, per run** — count blocked requests and fail loudly on zero. Configured is not
   the same as working; a silently-broken deny-list looks exactly like a page with no trackers, and
   the failure mode is invisible pollution of the client's data. This is a *runtime assertion*, not
   a setting.
3. **Default-deny on known trackers, and log every third-party domain that was NOT blocked**, so new
   trackers are discovered rather than silently leaked to. The allow-list will drift as the client's
   marketing stack changes; the log is how we find out.
4. **CTM specifically** — researched, and the answer is worse than assumed.

#### CTM: the pool number is consumed on page LOAD

Vendor documentation is explicit: the tracking snippet runs on every page load, reads UTM
parameters, referrer and `gclid`, then pulls a number from the managed pool and swaps it into the
page. It does **not** wait for interaction, and the assignment persists for that visitor's session
so the callback can be attributed.

So an unblocked render pass does not merely register a visit — **it consumes pool numbers and
creates attribution sessions bound to a visitor who does not exist.** At ~2,900 renders a night
that is a standing distortion of the client's call attribution, and potentially of their CTM
billing. Blocking CTM is mandatory, not preferable.

#### And blocking it has a side effect that must not become a false finding

With CTM blocked, the DNI swap never happens and the page renders its **hardcoded default number** —
which is exactly the number the HTML-only auditor already checks, so the two layers stay consistent.
That is the good case. The bad case: if any brand's markup leaves the number element **empty** until
CTM fills it, blocking leaves a visibly empty slot. Two consequences for the design:

* The render layer **must not run phone checks at all.** The HTML layer owns phones; duplicating
  them against a deliberately-crippled page would produce findings that are artifacts of our own
  blocking. (Same lesson as `space_before_punct`: never report a defect your own tooling created.)
* Empty-looking number slots must be **excluded from the overflow / blank / clipped-text checks**,
  for the same reason.

This must be verified per brand on the first run, not assumed.

### 10.3 Visual regression — designed, parked, not in v1

Cut. The reasoning that killed it is the reasoning in §4: nobody will hand-verify ~1,450 baselines,
so the baseline would bake today's defects in as canonical, and even then it can only ever claim
"changed", never "wrong". The axe checks produce real findings on day one; visual regression
produces nothing until run two and then only ambiguity. The design stays in §4 for whenever it is
asked for.

### 10.4 The client is told before this runs — not after

This is not a technical decision and is not ours to make. We would be loading their production
pages in a real browser, nightly, ~2,900 times. Even with every tracker blocked, it is their
infrastructure and their analytics.

**The render layer does not run against production until Syed has told them.** Draft notice below —
it is Syed's to send, and this repo does not send anything.

> **Draft — for Syed to send. Not sent by the tool, not sent by me.**
>
> Before we switch anything on, I want to flag a change in how the audit works, because it affects
> your sites rather than just our tooling.
>
> The audit currently reads the raw HTML of each page. That covers text, links, phone numbers and
> template fields, but it is blind to anything that only exists once a page is drawn on a screen —
> colour contrast, mobile layout, images that fail to load, buttons wired up in JavaScript. Several
> of the issues reported over the summer were exactly that kind, so we would like to add a second
> pass that opens each page in a real browser.
>
> What that means in practice:
>
> - It renders roughly 2,900 pages — one per page template across the nine sites, not every page —
>   at desktop and mobile sizes. A full pass takes about three hours and would run outside your
>   busy hours.
> - **It blocks all analytics and tracking before the page loads.** Google Analytics, Google Tag
>   Manager, Meta pixels and CallTrackingMetrics are all prevented from firing, so these visits do
>   not appear in your reporting and do not consume call-tracking numbers. We verify that blocking
>   actually worked on every run, and the pass refuses to start if it is not active.
> - **It does not submit any forms.** It checks that a Submit button is wired up and where it would
>   post, then cancels the request before it leaves our machine. No test enquiries reach your intake
>   team or your CRM. If you ever want form submission tested end to end, that should be done on a
>   staging site, and only with your agreement.
> - It does not log in, and it never clicks anything that deletes or cancels.
>
> The only thing we need from you is a yes, and a note of any hours you would rather we avoided.

### 10.5 Build order (confirmed)

1. Render harness + safety layer, with the runtime deny-list assertion. **Proven before any check.**
2. axe-core: contrast + `target-size` (24 ERROR, 44 optional INFO), plus broken images.
3. Dead JS buttons.
4. Form wiring capture — client half only, intercept and abort.

Precision reported **per brand**, hand-classified, same standard as the text checks: nothing ships
as ERROR below 80%.
