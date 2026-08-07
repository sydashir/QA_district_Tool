# What the audit does not check

**For the client. Written 2026-08-08.**

The automated audit reads the HTML each page sends to a browser. That is what lets it check
every page of all nine sites — but it also means there are real problems it structurally
**cannot** see. This page lists them, because being told "the audit found nothing" is only
useful if you also know what it was never looking at.

Everything below was **actually reported by Blue Media** between June and July 2026. None of it
is hypothetical, and none of it is covered by the tool.

---

## Why there is a boundary at all

A web page reaches a browser as HTML: text, links, and instructions. The browser then *renders*
it — downloads the stylesheets, runs the JavaScript, applies the screen size, loads the images,
and paints the result. The audit reads the HTML and stops there.

So the rule is simple:

* **If a problem exists in the words and links** — a wrong phone number, a broken link, a
  button with no destination, an unfilled template field, a misspelling, a sister brand named in
  the wrong site's copy — the audit finds it, on every page, every time.
* **If a problem only exists once the page is drawn on a screen** — a colour, a size, a
  position, a mobile layout, an image that fails to load — the audit cannot see it. The page it
  reads is identical whether that problem is there or not.

---

## The five things we cannot see

### 1. Anything about how it looks
Colour, contrast, size, spacing, alignment. Reported: *"the button text is unreadable against
its background"*, *"this content is bold and oversized"*, *"the widget formatting is broken"*.
Colours live in stylesheets and are applied by the browser; the HTML is the same either way.

### 2. Anything that depends on screen size
Reported: the Faculty Members widget failing on mobile, a missing Therapists button on mobile,
the `/our-facilities/` layout breaking on mobile, broken image icons in the mobile mega-menu.
Every page sends the *same* HTML to a phone and a desktop — what differs is how the browser
draws it. The audit has no screen.

### 3. Forms and widgets that break when drawn
Reported: *"the form is visually cut off"*. The form's HTML is present and correct; the problem
appears only when it is laid out.

### 4. Whether a `<button>` actually works
This one is subtle and worth stating plainly. The audit **does** report a link that goes
nowhere — that is one of the biggest categories it found, including the `Verify Insurance` and
`Learn More` buttons on the District Behavioral Health homepage.

But a `<button>` is different from a link. A link carries its destination in the HTML, so a
missing destination is visible. A button carries **no** destination — what it does is written in
JavaScript, which the audit does not run. Every button the tool examined on your live sites was
working (the scholarship form's Submit, the "Load More" on Connections, a popup's close button),
so it deliberately reports links only. **A genuinely dead button would not be reported.**

### 5. Speed
Reported: a Largest Contentful Paint regression on mobile. That is a measurement of how fast a
page paints, which requires actually painting it.

---

## What this means in practice

**These need a human or a different tool.** They are not covered by any run of the audit, no
matter how often it runs. Two practical options:

1. **A short manual pass on the templates, not the pages.** Nearly every visual defect above was
   a *template* problem showing on many pages at once. Checking one page of each template on a
   phone and a desktop covers most of it in an hour or two.
2. **Browser-based testing**, if this becomes a recurring need. It is a genuinely different tool
   — it opens a real browser per page, so it is far slower and more expensive, and would realistically
   be pointed at a sample of key templates rather than all ~16,500 pages. Worth doing only if
   visual regressions keep recurring; say the word and we will scope it.

---

## What the audit *does* cover, for contrast

So the boundary is clear in both directions, the audit checks every page of all nine sites for:

* phone numbers that are missing, malformed, out of date, or that **dial a different brand**
* broken links, redirects, links to leftover `-old`/`-copy`/`-delete` pages, and malformed addresses
* buttons and links with **no destination**, and social icons pointing at the **wrong network**
* unfilled template fields — both the visible `[acf field=…]` kind and the invisible kind that
  leaves broken wording behind ("In , the involving substances such as")
* placeholder text that reached the live page ("No content found")
* blank or thin pages, blank cells in an otherwise filled table
* the same paragraph or link repeated within one page
* heading structure, duplicate H1s, missing or duplicated search-engine titles and descriptions
* misspellings, including in page addresses
* a sister brand named in another brand's copy
* pages that are live but missing from the sitemap

If it is in the words or the links, it is covered. If it is in the paint, it is not.
