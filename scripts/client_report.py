"""Generate a per-brand client report — a single HTML file they can open without logging in.

Why this exists: the only client-facing artefact was a hand-written markdown file from weeks ago.
The dashboard needs an account and a running server; the Google Sheet needs access and knowledge of
which tab to read. This is a file you can email.

Ordered by HARM, not by count. The largest categories are rarely the most urgent — 4,599 duplicate
meta descriptions matter far less than one button dialling a competitor, and a report sorted by
volume buries exactly the findings someone needs to act on this week.

Deliberately NOT a dump of every row. A brand with 17,000 findings gets the top of each category
and an honest count of the rest, because a report nobody finishes is a report that changed nothing.
"""
from __future__ import annotations

import html
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.db import SessionLocal
from sqlalchemy import text as sql

# Harm order. Each entry: (heading, plain-English why it matters, [check/class keys]).
SECTIONS: list[tuple[str, str, list[str]]] = [
    ("Someone could be sent to the wrong company",
     "A visitor calls, or a search engine lists, a number or a page belonging to a different brand. "
     "This also covers a page that PRINTS one number but DIALS another, so a visitor who taps it "
     "reaches somewhere other than the number they read.",
     # `display_dial_mismatch` belongs here and its absence was an oversight, not a decision: it is
     # the COC defect that opened the ticket — the facility page shipped with a phone link that
     # dialled a different number from the one printed beside it, and neither manual QA nor the old
     # tool caught it. It is the flagship check, and until 2026-09-03 it reached the sheet and the
     # database but never the client report. Added on Syed's explicit approval.
     ["phone:cross_brand_dial", "schema:phone_cross_brand", "phone:dials_retired",
      "phone:stale_retired", "brands:sister_brand", "phone:display_dial_mismatch"]),
    ("A visitor cannot get through",
     "A button that leads nowhere, a broken link, or a click-to-call nobody can identify.",
     ["actions:dead_cta", "broken_links:broken", "actions:social_misrouted",
      "broken_links:fused_url", "broken_links:malformed_link", "accessibility:link-name",
      "accessibility:label"]),
    # RENDERED-PAGE findings. They had nowhere to go until 2026-08-31 and would have been silently
    # dropped from every report: `_matches` needs a section key, and contrast / tap_target /
    # broken_image matched none. Placed here, after "cannot get through", because the harm is the
    # same shape — a visitor who cannot read the text or hit the button is stopped just as surely as
    # one following a dead link — and ahead of housekeeping, which would undersell it.
    ("Some visitors cannot read or use the page",
     "Text too faint to read against its background, or an image that fails to load. These are "
     "found by loading the page in a real browser, so they are what a visitor actually gets rather "
     "than what the code says.",
     # `tap_target:*` stays in the key list so that if it is ever published it lands in the right
     # section, but it is NOT described above: the pass discards tap-target findings, and a blurb
     # promising something the report never shows is the same defect Section D had.
     ["contrast:*", "tap_target:*", "broken_image:*"]),
    ("The page shows something that was never meant to be published",
     "Template code, placeholder text, or an unfinished page that reached the public site.",
     ["placeholder:*", "empty_slot:lorem_ipsum", "empty_slot:not_found_sentinel",
      "broken_links:staging_link", "broken_links:cruft_link"]),
    ("The wording is visibly broken",
     "Text that a reader can see is wrong — missing words, fused words, damaged sentences.",
     ["empty_slot:*", "misspelling:*", "scope:county_for_country"]),
    ("Search engines are being given the wrong information",
     "What machines read about this business, which is invisible when looking at the site.",
     ["schema:missing", "schema:invalid_json", "meta:*", "enumeration:sitemap_dead",
      "enumeration:indexable_unsitemapped",
      # Both added 2026-08-31 and both are search-engine problems rather than visitor-facing ones:
      # a redirected internal link still works but bleeds link equity and pins the old URL alive,
      # and a `-2` address is usually a duplicate page competing with the original.
      "broken_links:redirected_internal"]),
    ("Structural and housekeeping",
     "Real, lower urgency: heading order, repeated content, blank sections.",
     ["heading_structure:*", "duplication:*", "blank:*", "empty_row:*", "accessibility:*"]),
]
TOP_N = 8          # examples shown per category; the rest are counted, never silently dropped

# Classes that are a LIST OF URLS rather than distinct defects. Each row differs only by which page
# it names, so grouping on the message alone leaves dozens of near-identical lines — and it left the
# LEAST important section as the longest thing in the report. These collapse to one row per class.
URL_LIST_CLASSES = {
    "enumeration:sitemap_dead", "enumeration:indexable_unsitemapped",
    "enumeration:noindex_unsitemapped", "enumeration:rest_404",
    "broken_links:unverified_external",
}

# FIXED AT SOURCE — `auditor/checks/phone.py::_NAP_SOURCE` no longer emits the stale caveat, and
# `scripts/backfill_nap_caveat.py` rewrote the rows that existed when it ran. This stays as a net
# for rows written by runs that PREDATE the source fix, and there are more of those than you would
# expect: RR run 107 finished after the backfill and left 67 fresh stale rows, and any run already
# executing when the fix lands will do the same. Re-run the backfill after such a run; until then
# this keeps the wrong text off the client's page. Delete it once a query for "sheet ID unverified"
# returns zero and no pre-fix run can still land.
STALE_CAVEATS = [
    ("(NAP 2026-07-02 snapshot; sheet ID unverified)", "(from your NAP sheet)"),
    ("NAP 2026-07-02 snapshot; sheet ID unverified", "from your NAP sheet"),
]


# The checks append their own page count to the issue text ("... — on 1343 pages") because the
# spreadsheet has no separate column for it. The report DOES have one, and printing both gives
# `button goes nowhere: "View All" — on 1343 pages on 1,343 pages`. Strip the check's copy and let
# the report's own formatted count stand: it is the one that gets thousands separators, and it is
# the one that stays right after grouping merges several rows into a larger span.
_INLINE_PAGE_COUNT = re.compile(r"\s*[—-]\s*on\s+[\d,]+\s+pages?\s*$", re.I)


# E.164 is how the checks store and compare numbers, and it is the right internal form — it is what
# makes "+18445760144" and "(844) 576-0144" comparable at all. It is not how a client reads a phone
# number, and the report was printing raw +18006929850 in issue text, snippets and suggestions.
# Rewritten HERE rather than in `auditor/checks/phone.py`, which is hashed: changing that file
# would invalidate every brand's resume cache and cost a full re-crawl to fix a display detail.
_E164_US = re.compile(r"\+1(\d{3})(\d{3})(\d{4})\b")


def _humanise_phones(text: str) -> str:
    return _E164_US.sub(r"(\1) \2-\3", text)


def _clean(text: str) -> str:
    for old, new in STALE_CAVEATS:
        text = text.replace(old, new)
    return _humanise_phones(_INLINE_PAGE_COUNT.sub("", text))


def _trim(text: str, limit: int = 400) -> str:
    """Trim at a sentence end, never mid-word. The first version cut at a fixed 300 characters and
    produced findings ending 'Menus, footers, pages that list se'."""
    text = _clean((text or "").strip())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for stop in (". ", "! ", "? "):
        i = cut.rfind(stop)
        if i > limit * 0.5:
            return cut[:i + 1]
    return cut.rsplit(" ", 1)[0] + "\u2026"


def _matches(check: str, cls: str | None, keys: list[str]) -> bool:
    for k in keys:
        c, _, want = k.partition(":")
        if check != c:
            continue
        if want == "*" or want == (cls or ""):
            return True
    return False


# A baseline covering this fraction of the current run is treated as sound. Below it, "new since
# last time" is measuring the gap in COVERAGE, not a change on the site.
BASELINE_FLOOR = 0.70


def poisoned_baseline(session, brand_code: str, run_id: int, pages: int) -> str | None:
    """Was this run's "new since last time" measured against a baseline that saw far less?

    Two real cases, and the rule has to cover both — which is why it is about PAGES, not status:
      * RR run 119 was throttled to 77 of 8,029 pages. It was correctly refused for reporting, but
        `run_audit` writes history BEFORE `server/jobs.py` applies the crawl verdict, so a 77-page
        baseline survived. The next good run reported **11,259 new**; the real figure was 128.
      * COC run 114 was a perfectly successful run that happened to cover 886 pages against a
        normal 1,505. Nothing failed, and it still inflated the next run to **1,334 new** against a
        real 52.
    GL is the control: its run 127 died at a reboot with 0 pages and NO report, so it never wrote a
    baseline at all and GL's numbers are sound. Hence `report_dir IS NOT NULL` — a run that wrote no
    report wrote no history either.

    Detected, never hardcoded, so it clears itself on the next comparable run.
    """
    prev = session.execute(sql("""
        SELECT r.pages_audited, r.status FROM runs r JOIN brands b ON b.id = r.brand_id
        WHERE b.code = :c AND r.id < :r AND r.report_dir IS NOT NULL
        ORDER BY r.started_at DESC LIMIT 1"""), {"c": brand_code.upper(), "r": run_id}).first()
    if not prev or not pages or not prev[0]:
        return None
    if prev[0] >= pages * BASELINE_FLOOR:
        return None
    return (f"<strong>Ignore the &ldquo;new since last time&rdquo; figure on this report.</strong> "
            f"The audit it is compared against covered only {prev[0]:,} pages, where this one "
            f"covered {pages:,} — so most items counted as new were already there and already "
            f"reported. The list of problems below is correct; it is only the new-versus-old split "
            f"that is wrong, and it corrects itself on the next full audit.")


def fetch(session, brand_code: str):
    row = session.execute(sql("""
        SELECT r.id, r.started_at, r.pages_audited, r.partial_sample, b.name, b.base_url
        FROM runs r JOIN brands b ON b.id = r.brand_id
        WHERE b.code = :c AND r.status = 'ok'
        ORDER BY r.started_at DESC LIMIT 1"""), {"c": brand_code.upper()}).first()
    if not row:
        return None, []
    findings = session.execute(sql("""
        SELECT f.check, f.details->>'class' AS cls, f.severity, f.issue, f.url,
               f.snippet, f.suggestion, COALESCE(f.page_count, 1) AS pages, f.first_seen,
               f.details->>'shot' AS shot, f.details->>'shot_absent' AS shot_absent,
               f.fingerprint AS fp
        FROM findings f
        WHERE f.run_id = :r AND f.status IN ('new','persisting')
        ORDER BY f.severity, COALESCE(f.page_count,1) DESC"""), {"r": row[0]}).fetchall()
    return row, findings


# Embedded-image budget for one report. Screenshots are base64 PNGs inline, so the report stays a
# single file that still shows its pictures after someone forwards it — a referenced folder does
# not survive an email, and that is how these are actually delivered. 4 MB is far above the measured
# need (~20 KB a shot, a handful of shots per brand) and exists so a pathological run cannot produce
# a report nobody can open. A budget that is hit is ANNOUNCED, never silently applied.
# Brands whose audit deliberately covers PART of the site. A report that silently covers 4% of a
# site reads as a report on the site, so the scope is stated in the report itself rather than living
# in a plan document nobody opens.
#
# Each note must carry its EVIDENCE and its DATE. A scope claim without either rots into an
# assertion, and the next person cannot tell whether it is still true or how it was established.
SCOPE_NOTES = {
    "AR": (
        "This audit covers <strong>474 pages</strong>. Alliance Recovery's sitemap advertises about "
        "<strong>11,200 URLs</strong> — the other ~10,746 are <code>/city-data/</code> pages that "
        "were deliberately left out, because they are duplicates of one another rather than "
        "11,000 different pages. Two things establish that, both checked on 2026-09-02: every one "
        "of those URLs ends in <code>-2</code>, which is what WordPress adds when a web address is "
        "already taken, and the ones we followed all redirect to the Alliance Recovery homepage "
        "rather than showing city content. Auditing 11,000 copies of the same redirect would fill "
        "this report without telling you anything new. "
        "<strong>They are still worth your attention as a group:</strong> a sitemap that lists "
        "~10,746 redirecting duplicate addresses is telling search engines to crawl 11,000 pages "
        "that do not exist as distinct content. Separately, "
        "<code>alliancerecovery.com/page-sitemap.xml</code> returns a server error (HTTP 500) and "
        "has done so on every run we have recorded, so the pages it should list are invisible both "
        "to us and to search engines."
    ),
}


# The single source of truth for "why is there no picture", imported rather than re-typed: the
# capture layer decides the reason and this report prints it, and two copies of that wording would
# drift the moment one side gained a case. render/shots.py imports only stdlib, so this does not
# drag playwright into a report that has to render on a laptop with no browser installed.
from render.shots import ABSENCE_REASONS                              # noqa: E402

SHOT_BUDGET_BYTES = 4 * 1024 * 1024


def select_shown(rows: list, n: int) -> list:
    """Choose the n findings a section actually shows: every CLASS gets a slot before any class
    gets a second.

    Sorting purely by (severity, page_count) is right for the DATA and wrong for the READER. A dead
    "Verify Insurance" button on one page matters more than a footer link repeated across 3,000, but
    page_count puts the footer first by construction — so per-page classes lost every slot. On GL,
    `actions:dead_cta` had 41 findings and 25 photographs and appeared ZERO times, because collapsed
    link findings covering thousands of pages filled all eight slots.

    So: rank the classes by their own best row, then deal one slot to each in turn. Severity still
    leads — an error class is dealt before a warning class — but a class present in the section can
    no longer be shut out by another class's page counts. What is left over is still counted in the
    "and N more of this kind" line, never silently dropped.
    """
    if len(rows) <= n:
        return list(rows)

    # WITHIN A SEVERITY TIER, never across it. The first version of this dealt round-robin over all
    # classes at once and pushed an ERROR below two warnings on GL — a harm-ordered report cannot
    # invert severity to make room for variety. So: exhaust the error classes, then the warnings,
    # then info. Representation is guaranteed inside each tier, which is where the unfairness was.
    rank = {"error": 0, "warning": 1, "info": 2}
    shown: list = []
    for tier in (0, 1, 2):
        if len(shown) >= n:
            break
        tier_rows = [f for f in rows if rank.get(f[2], 3) == tier]
        if not tier_rows:
            continue
        by_class: dict[str, list] = {}
        for f in tier_rows:
            by_class.setdefault(f"{f[0]}:{f[1]}", []).append(f)
        # `rows` arrives sorted by (severity, -page_count), so each class list is too, and the class
        # order inherits that ranking from its own best row.
        order = sorted(by_class, key=lambda k: tier_rows.index(by_class[k][0]))
        while len(shown) < n and any(by_class[k] for k in order):
            for k in order:
                if len(shown) >= n:
                    break
                if by_class[k]:
                    shown.append(by_class[k].pop(0))
    return shown


def _shot_html(shot: str | None, budget: list[int], omitted: list[int],
               absent: str | None = None) -> str:
    """One <img>, if it fits — otherwise a sentence saying why there is none.

    Silence is not an option here and this is the whole point of the parameter. Only about half of
    the `display_dial_mismatch` findings on a brand yield a picture (measured on GL: the locator
    resolves 99% of them, but 51% produce an image; the rest are mobile/desktop duplicates that are
    not visible at the width we photograph). If those simply had no image and no explanation, a
    reader would conclude either that the feature is broken or — far worse — that a finding without
    a photograph is a finding without evidence.

    So every branch below ends by saying the same thing in different words: the defect is what the
    audit read in the page's own code. The picture is corroboration, never the proof.
    """
    if not shot:
        why = ABSENCE_REASONS.get(absent or "")
        if not why:
            return ""
        return f"<div class='noshot'>{html.escape(why)}</div>"
    cost = len(shot)
    if cost > budget[0]:
        omitted[0] += 1
        return (f"<div class='noshot'>{html.escape(ABSENCE_REASONS['budget'])}</div>")
    budget[0] -= cost
    # No lazy-loading and no external anything: this has to render offline, from a file on a laptop
    # with the network off, which is the situation a forwarded report is opened in.
    return (f"<div class='shot'><img alt='the element this finding is about' "
            f"src='{html.escape(shot, quote=True)}'></div>")



# The section built from a RENDERED SAMPLE, and the only one whose numbers are not a census. Every
# other section comes from the full crawl; this one comes from ~30 pages loaded in a browser,
# because rendering RR's 8,000 pages would take days. Saying so next to the findings is not
# optional: a count from a sample read as a site total is the single most likely way this section
# misleads someone.
SAMPLED_SECTION = "Some visitors cannot read or use the page"

# Measured 2026-09-08 by photographing the flagged element and comparing the ACTUAL PIXELS with the
# colours axe reported — a check independent of the DOM axe reads. Combined: 58 decided, 54 true,
# 4 false = 93%. Per brand, only two reached the n>=10 needed for a verdict of their own.
PRECISION_NOTES: dict[str, str] = {
    "COC": "Checked on this site: 20 of its colour findings were verified against the actual "
           "pixels and 17 held up — 85%.",
    "TDRC": "Checked on this site: 10 of its colour findings were verified against the actual "
            "pixels and all 10 held up.",
    "CAD": "<strong>Treat this site's colour findings with more caution than the others.</strong> "
           "Only 5 of its colour findings could be verified against the actual pixels, one of "
           "those was wrong, and this site produced far more raw colour warnings than any other "
           "(4,073 from 60 pages). That is too small a check to give this site a pass of its own, "
           "so it is not being given one.",
}

_SAMPLE_CAVEAT = (
    "<strong>These come from a sample, not the whole site.</strong> About 30 pages were loaded in "
    "a real browser and inspected; the rest of the site was not. So this section cannot tell you "
    "how many pages are affected, cannot be read as a site-wide total, and an empty section here "
    "never means &ldquo;no problems&rdquo; — it means these pages, at this width, on this day. "
    "Colour findings are also re-checked against the page&rsquo;s own pixels before being shown, "
    "and any that the pixels contradict are dropped rather than printed."
)


def sampled_note(brand_code: str) -> str:
    """The caveat block for the rendered section, plus whatever this brand's own check supports."""
    extra = PRECISION_NOTES.get(brand_code.upper(), "")
    return (f"<p class='caveat'>{_SAMPLE_CAVEAT}</p>"
            + (f"<p class='caveat'>{extra}</p>" if extra else ""))


def section_rows(findings: list, keys: list[str], top_n: int = None) -> tuple[list, list]:
    """(all merged rows for this section, the ones it will show).

    Extracted so that ONE piece of code decides what the client sees. The shot pass used to
    photograph any finding carrying a selector and hope it overlapped this selection; on GL the two
    populations barely intersected and not one picture reached a report. The report's choice is
    authoritative — so it is made here, once, and the shot pass reads it.
    """
    rank = {"error": 0, "warning": 1, "info": 2}
    rows = [f for f in findings if _matches(f[0], f[1], keys)]
    if not rows:
        return [], []
    # Group identical findings before showing them. DBH's cross-brand dial is the same defect on 17
    # pages; listed one per URL it filled the most important section with eight copies of one
    # sentence. The engine collapses TEMPLATE-wide findings, but a per-page finding repeated across
    # pages still arrives as many rows.
    grouped: dict[tuple, list] = {}
    for f in rows:
        key = (f[0], f[1], f[3], f[5])
        if f"{f[0]}:{f[1]}" in URL_LIST_CLASSES:
            key = (f[0], f[1], "", "")      # one row for the whole class
        grouped.setdefault(key, []).append(f)
    merged = []
    for (_c, _cls, _issue, _snip), g in grouped.items():
        first = g[0]
        span = max(sum(x[7] for x in g), len(g))
        merged.append(tuple(first[:7]) + (span,) + tuple(first[8:]))
    merged.sort(key=lambda f: (rank.get(f[2], 3), -f[7]))
    return merged, select_shown(merged, TOP_N if top_n is None else top_n)


def selected_fingerprints(session, brand_code: str) -> list[str]:
    """Exactly the findings this brand's report will display, as fingerprints.

    The shot pass photographs these and nothing else.
    """
    run, findings = fetch(session, brand_code)
    if not run:
        return []
    out: list[str] = []
    for _heading, _why, keys in SECTIONS:
        _all, shown = section_rows(findings, keys)
        out.extend(f[11] for f in shown if len(f) > 11 and f[11])
    return out


# ---------------------------------------------------------------- what the audit cannot see
# DERIVED, never hand-written prose. Section D was wrong on 2026-09-08 in the way that matters
# most — it told clients "a genuinely dead button would not appear in this report" while
# `actions:dead_cta` was one of the most-reported classes in the run (379 findings). It also
# claimed nothing checks colour and contrast, when `render/a11y.py` implements exactly that.
#
# So each topic below names the finding CLASSES that would cover it, and the state is worked out
# from the code and the run rather than asserted:
#   * a covering class appears in THIS run   -> the topic is covered; it is dropped from the list
#     entirely, because it is in the body of the report above.
#   * no finding, but the class exists in source -> "built, not switched on for these reports".
#     That is the honest state of contrast, broken images and tap targets: `render/markup.py`
#     excludes them from the published pass because they need a rendered page, so the capability
#     exists and nothing publishes it. Saying "we do not check it" would be false; saying "we
#     check it" would be worse.
#   * no covering class anywhere in source   -> a genuine limitation, stated plainly.
# Add a check and its class string appears in source, so this list corrects itself.
LIMIT_TOPICS: list[tuple[str, list[str], str, str]] = [
    ("Colour and contrast", ["color-contrast", "contrast_coverage"],
     "Whether text is readable against the colour behind it.",
     "The check is built and measured but is not part of these reports — it needs the page drawn "
     "on a screen, and this audit reads the HTML."),
    ("Images that fail to load", ["broken_image"],
     "An image that 404s looks identical in the HTML to one that loads perfectly.",
     "The check is built — it re-requests each image — but is not part of these reports."),
    # BOTH ids, and the pair matters. `render/a11y.py:117` sets details["class"] = the axe RULE id,
    # so the finding this pass actually produces is `target-size` (WCAG 2.5.8 AA, 24x24).
    # `target_size_enhanced` is a SEPARATE, additional 44px AAA advisory (a11y.py:363). Keying the
    # topic on the advisory alone meant Section D would keep saying tap targets are not checked
    # while the report body listed them — exactly the drift this derivation exists to stop.
    ("Tap targets that are too small to hit", ["target-size", "target_size_enhanced"],
     "Buttons and links too small or too close together to press on a phone.",
     "The check is built but is not part of these reports."),
    ("Whether a button wired in JavaScript works", ["dead_cta"],
     "A link carries its destination in the HTML, so a dead one is reported above. A button whose "
     "behaviour lives entirely in JavaScript is not exercised — this audit does not run it.", ""),
    ("Mobile layout", [],
     "Anything that breaks only at phone width — a widget that collapses, a button that "
     "disappears, a form that is cut off.", ""),
    ("Forms", [],
     "Whether a form submits, and whether the enquiry reaches anyone. Deliberately not tested: we "
     "will not send test enquiries into a live intake system.", ""),
    ("Page speed", [],
     "How fast a page paints requires actually painting it.", ""),
    ("Call-tracking numbers", [],
     "The number hard-coded in each element is checked; what your call-tracking script swaps it to "
     "for a live visitor is not.", ""),
]


def _source_classes() -> set[str]:
    """Every finding class the codebase can emit, read from the source rather than remembered."""
    import re
    found: set[str] = set()
    for d in ("auditor/checks", "render"):
        for f in (ROOT / d).glob("*.py"):
            t = f.read_text(encoding="utf-8")
            found |= set(re.findall(r'"class":\s*"([a-z0-9_-]+)"', t))
            found |= set(re.findall(r'cls\s*=\s*"([a-z0-9_-]+)"', t))
            # A class assigned from a VARIABLE is invisible to the two patterns above, and that is
            # not hypothetical: `render/a11y.py` does `details={"class": rule, ...}` where `rule` is
            # the axe rule id, so `target-size` — a real, shipped check — scanned as non-existent
            # and Section D would have gone on denying tap-target coverage after it was published.
            # Axe rule ids are declared as a literal tuple, so read them from there.
            for group in re.findall(r'DEFAULT_RULES[^=]*=\s*\(([^)]*)\)', t):
                found |= set(re.findall(r'"([a-z0-9-]+)"', group))
    return found


def limits_html(findings: list) -> str:
    """Section D, built from what this run actually produced and what the code can produce."""
    present = {(f[1] or "") for f in findings}
    in_source = _source_classes()
    rows = []
    for title, classes, what, unpublished in LIMIT_TOPICS:
        if any(c in present for c in classes):
            continue                       # covered, and shown in the body above
        note = ""
        if classes and unpublished and any(c in in_source for c in classes):
            note = f" <em>{html.escape(unpublished)}</em>"
        rows.append(f"  <li><strong>{html.escape(title)}</strong> {html.escape(what)}{note}</li>")
    return "\n".join(rows)


def render(brand_code: str, run, findings, _baseline_warning: str | None = None) -> str:
    _, started, pages, partial, name, base_url = run
    sev_rank = {"error": 0, "warning": 1, "info": 2}
    _shot_budget = [SHOT_BUDGET_BYTES]
    _shots_omitted = [0]
    out = []
    total = len(findings)

    for heading, why, keys in SECTIONS:
        rows, shown = section_rows(findings, keys)
        if not rows:
            continue
        items = []
        for f in shown:
            pages_txt = (f"<span class='pages'>on {f[7]:,} pages</span>" if f[7] > 1 else "")
            snippet = html.escape(_trim(f[5] or "", 220))
            items.append(
                f"<li class='{html.escape(f[2])}'>"
                f"<div class='issue'>{html.escape(_clean(f[3]))} {pages_txt}</div>"
                + (f"<div class='snippet'>{snippet}</div>" if snippet else "")
                + f"<div class='where'><a href='{html.escape(f[4])}'>{html.escape(f[4][:96])}</a></div>"
                + (f"<div class='fix'>{html.escape(_trim(f[6] or ''))}</div>" if f[6] else "")
                + _shot_html(f[9] if len(f) > 9 else None, _shot_budget, _shots_omitted,
                             f[10] if len(f) > 10 else None)
                + "</li>")
        more = ""
        if len(rows) > len(shown):
            more = (f"<p class='more'>and {len(rows) - len(shown):,} more of this kind. "
                    f"The complete list for every category is in the audit spreadsheet — ask "
                    f"Syed Ashir for access if you do not already have it.</p>")
        errs = sum(1 for f in rows if f[2] == "error")
        out.append(
            f"<section><h2>{html.escape(heading)}</h2>"
            f"<p class='why'>{html.escape(why)}</p>"
            + (sampled_note(brand_code) if heading == SAMPLED_SECTION else "")
            + f"<p class='count'>{len(rows):,} finding(s), {errs:,} of them certain "
              f"(proven by the page's own code, not a judgement call).</p>"
            + f"<ul>{''.join(items)}</ul>{more}</section>")

    # A budget that was hit must SAY so. A report quietly missing half its pictures reads as a
    # report about findings that happen not to have any — the same silent-cap failure the project
    # rules out everywhere else.
    # Said ONCE, plainly, wherever any finding lacks an image. Without it a reader compares two
    # findings — one with a photograph, one without — and silently ranks the second as less real.
    no_image_note = ""
    if any((f[10] if len(f) > 10 else None) for f in findings):
        no_image_note = (
            "<section><p class='caveat'><strong>Some findings have no picture, and that does not "
            "make them less certain.</strong> Every finding in this report was established by "
            "reading the page's own code — the text it displays and the number it dials. A "
            "photograph is corroboration for a human skimming the report, never the proof. Where "
            "one is missing, the reason is printed under the finding; the commonest is that the "
            "element is a mobile/desktop duplicate that is not visible at the width we "
            "photograph.</p></section>")

    if no_image_note:
        out.append(no_image_note)

    if _shots_omitted[0]:
        out.append(
            f"<section><p class='caveat'>{_shots_omitted[0]:,} screenshot(s) were left out of this "
            f"file to keep it small enough to open and email. The findings themselves are all "
            f"here — only the pictures were dropped, and every one of them is still in the audit "
            f"spreadsheet.</p></section>")

    # The accessibility pass runs SEPARATELY from the crawl (it needs a browser), so it attaches
    # its findings to whichever run was latest when IT ran. The next crawl makes a new run and those
    # findings are not on it. Without this note the section simply vanishes and its silence reads as
    # "nothing wrong", when the truth is "nobody looked" — the same distinction the contrast
    # coverage caveat protects. Cheap to state, and it is how anyone notices the drift.
    # Stated before everything else in this section: a reader who does not know the scope cannot
    # interpret anything below it.
    # Stated with the scope caveats, above the findings: a reader who takes the new-count at face
    # value has already misread the report by the time they reach the list.
    baseline_note = (f"<p class='caveat'>{_baseline_warning}</p>") if _baseline_warning else ""

    limits_rows = limits_html(findings)

    _sn = SCOPE_NOTES.get(brand_code.upper())
    scope_note = (f"<p class='caveat'><strong>Scope of this audit.</strong> {_sn}</p>") if _sn else ""

    acc_note = ""
    if not any(f[0] == "accessibility" for f in findings):
        acc_note = ("<p class='caveat'><strong>The accessibility checks were not run against this "
                    "crawl.</strong> They are a separate pass, so their results belong to an "
                    "earlier crawl of this site and are not included below. Nothing here says "
                    "anything about screen readers, link names or form labels either way — it "
                    "means nobody looked this time, not that there was nothing to find.</p>")

    caveat = ""
    if partial:
        caveat = ("<p class='caveat'><strong>This was a partial sample.</strong> Only part of this "
                  "site was looked at, so anything on the pages it did not reach is not counted "
                  "here. It is not a clean bill of health for the rest.</p>")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(name)} — site audit</title><style>
:root{{--ink:#16181d;--mut:#5b6270;--line:#e3e6ec;--err:#b3261e;--warn:#8a6100;--info:#4a5568;--bg:#fff}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif}}
.wrap{{max-width:820px;margin:0 auto;padding:40px 22px 80px}}
h1{{font-size:30px;margin:0 0 4px}} h2{{font-size:20px;margin:0 0 6px}}
.sub{{color:var(--mut);margin:0 0 26px}}
.summary{{display:flex;gap:26px;flex-wrap:wrap;border:1px solid var(--line);border-radius:10px;padding:16px 20px;margin-bottom:30px}}
.summary div span{{display:block;font-size:26px;font-weight:600}}
.summary div small{{color:var(--mut)}}
section{{border-top:1px solid var(--line);padding-top:22px;margin-top:26px}}
.why{{color:var(--mut);margin:0 0 4px}} .count{{color:var(--mut);font-size:14px;margin:0 0 14px}}
ul{{list-style:none;padding:0;margin:0}}
li{{border-left:3px solid var(--info);padding:10px 0 10px 14px;margin-bottom:16px}}
li.error{{border-left-color:var(--err)}} li.warning{{border-left-color:var(--warn)}}
.issue{{font-weight:600}} .pages{{font-weight:400;color:var(--mut);font-size:14px}}
.snippet{{font-family:ui-monospace,Menlo,monospace;font-size:13px;background:#f6f7f9;
  border-radius:6px;padding:8px 10px;margin:7px 0;white-space:pre-wrap;word-break:break-word}}
.where a{{color:var(--mut);font-size:13px;word-break:break-all}}
.fix{{font-size:14px;color:var(--mut);margin-top:6px}}
.shot{{margin-top:10px}}
/* Deliberately quiet, and deliberately NOT styled as a warning. A missing picture is a fact about
   the photograph, not about the defect, and colouring it like a caveat would imply the finding is
   weaker than the ones that happen to have an image. */
.noshot{{margin-top:8px;font-size:13px;color:var(--mut);font-style:italic}}
/* max-width, never a fixed width: these are 2x-scale crops of a 390px mobile viewport, so they must
   shrink on a phone and never force the page to scroll sideways. */
.shot img{{max-width:100%;height:auto;border:1px solid var(--bd);border-radius:6px;display:block}}
.more{{color:var(--mut);font-size:14px}}
.caveat{{background:#fff8e6;border:1px solid #f0d999;border-radius:8px;padding:12px 14px}}
footer{{margin-top:44px;color:var(--mut);font-size:13px;border-top:1px solid var(--line);padding-top:16px}}
@media(prefers-color-scheme:dark){{:root{{--ink:#e8eaf0;--mut:#9aa2b1;--line:#2a2f3a;--bg:#14161a}}\n section.limits{{background:#1a1d23}}
 .snippet{{background:#1c1f26}} .caveat{{background:#2a2313;border-color:#5a4a1e}}}}
</style></head><body><div class="wrap">
<h1>{html.escape(name)}</h1>
<p class="sub">Automated site audit &middot; {html.escape(base_url)} &middot;
 {started.strftime('%d %B %Y')}</p>
{caveat}
<div class="summary">
  <div><span>{pages:,}</span><small>pages checked</small></div>
  <div><span>{total:,}</span><small>open findings</small></div>
  <div><span>{sum(1 for f in findings if f[2] == 'error'):,}</span><small>certain problems &mdash; proven wrong, not a judgement call</small></div>
</div>
<p>Ordered by how much each problem matters, not by how many there are. Every item links to the
page it was found on.</p>
{''.join(out)}
<section class="limits"><h2>What this audit cannot see</h2>
{baseline_note}{scope_note}{acc_note}
<p class="why">Being told a category is empty is only useful alongside what was never looked at.
The audit reads the HTML each page sends to a browser. It does not draw the page, so anything that
only exists once the page is on a screen is invisible to it — and silence below does
<strong>not</strong> mean these are fine.</p>
<ul class="limits">
{limits_rows}
</ul>
<p class="why">Most of the above are best caught by opening one page of each template on a phone and
a desktop — an hour or two of human checking covers what no amount of re-running this can.</p>
</section>
<footer>Generated {datetime.now(timezone.utc).strftime('%d %B %Y')} from the automated audit.
Findings marked <em>certain</em> were verified deterministically; the rest are worth a look.</footer>
</div></body></html>"""


def main(brands: list[str], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as s:
        for code in brands:
            run, findings = fetch(s, code)
            if not run:
                print(f"  {code.upper():<5} no completed run — skipped")
                continue
            path = out_dir / f"{code.lower()}-audit.html"
            warn = poisoned_baseline(s, code, run[0], run[2])
            path.write_text(render(code, run, findings, warn), encoding="utf-8")
            print(f"  {code.upper():<5} {len(findings):>6,} findings -> {path.name} "
                  f"({path.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    args = sys.argv[1:] or ["rr", "gl", "ah", "coc", "cad", "ar", "tdrc", "dbh", "mhd"]
    main(args, Path("reports/_client"))
