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
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.db import SessionLocal
from sqlalchemy import text as sql

# Harm order. Each entry: (heading, plain-English why it matters, [check/class keys]).
SECTIONS: list[tuple[str, str, list[str]]] = [
    ("Someone could be sent to the wrong company",
     "A visitor calls, or a search engine lists, a number or a page belonging to a different brand.",
     ["phone:cross_brand_dial", "schema:phone_cross_brand", "phone:dials_retired",
      "phone:stale_retired", "brands:sister_brand"]),
    ("A visitor cannot get through",
     "A button that leads nowhere, a broken link, or a click-to-call nobody can identify.",
     ["actions:dead_cta", "broken_links:broken", "actions:social_misrouted",
      "broken_links:fused_url", "broken_links:malformed_link", "accessibility:link-name",
      "accessibility:label"]),
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
      "enumeration:indexable_unsitemapped"]),
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


def _clean(text: str) -> str:
    for old, new in STALE_CAVEATS:
        text = text.replace(old, new)
    return text


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
               f.snippet, f.suggestion, COALESCE(f.page_count, 1) AS pages, f.first_seen
        FROM findings f
        WHERE f.run_id = :r AND f.status IN ('new','persisting')
        ORDER BY f.severity, COALESCE(f.page_count,1) DESC"""), {"r": row[0]}).fetchall()
    return row, findings


def render(brand_code: str, run, findings) -> str:
    _, started, pages, partial, name, base_url = run
    sev_rank = {"error": 0, "warning": 1, "info": 2}
    out = []
    total = len(findings)

    for heading, why, keys in SECTIONS:
        rows = [f for f in findings if _matches(f[0], f[1], keys)]
        if not rows:
            continue
        # Group identical findings before showing them. DBH's cross-brand dial is the same defect
        # on 17 pages; listed one per URL it filled the most important section with eight copies of
        # one sentence and pushed everything else off the page. The engine collapses TEMPLATE-wide
        # findings, but a per-page finding repeated across pages still arrives as many rows.
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
        merged.sort(key=lambda f: (sev_rank.get(f[2], 3), -f[7]))
        rows = merged

        items = []
        for f in rows[:TOP_N]:
            pages_txt = (f"<span class='pages'>on {f[7]:,} pages</span>" if f[7] > 1 else "")
            snippet = html.escape(_trim(f[5] or "", 220))
            items.append(
                f"<li class='{html.escape(f[2])}'>"
                f"<div class='issue'>{html.escape(_clean(f[3]))} {pages_txt}</div>"
                + (f"<div class='snippet'>{snippet}</div>" if snippet else "")
                + f"<div class='where'><a href='{html.escape(f[4])}'>{html.escape(f[4][:96])}</a></div>"
                + (f"<div class='fix'>{html.escape(_trim(f[6] or ''))}</div>" if f[6] else "")
                + "</li>")
        more = ""
        if len(rows) > TOP_N:
            more = (f"<p class='more'>and {len(rows) - TOP_N:,} more of this kind. "
                    f"The complete list for every category is in the audit spreadsheet — ask "
                    f"Syed Ashir for access if you do not already have it.</p>")
        errs = sum(1 for f in rows if f[2] == "error")
        out.append(
            f"<section><h2>{html.escape(heading)}</h2>"
            f"<p class='why'>{html.escape(why)}</p>"
            f"<p class='count'>{len(rows):,} finding(s), {errs:,} of them certain.</p>"
            f"<ul>{''.join(items)}</ul>{more}</section>")

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
  <div><span>{sum(1 for f in findings if f[2] == 'error'):,}</span><small>certain problems</small></div>
</div>
<p>Ordered by how much each problem matters, not by how many there are. Every item links to the
page it was found on.</p>
{''.join(out)}
<section class="limits"><h2>What this audit cannot see</h2>
<p class="why">Being told a category is empty is only useful alongside what was never looked at.
The audit reads the HTML each page sends to a browser. It does not draw the page, so anything that
only exists once the page is on a screen is invisible to it — and silence below does
<strong>not</strong> mean these are fine.</p>
<ul class="limits">
  <li><strong>Colour and contrast.</strong> Whether text is readable against its background.
      Nothing here checks it.</li>
  <li><strong>Mobile layout.</strong> Anything that breaks only at phone width — a widget that
      collapses, a button that disappears, a form that is cut off.</li>
  <li><strong>Images that fail to load.</strong> A missing image looks identical in the HTML to one
      that loads perfectly.</li>
  <li><strong>Whether a button actually works.</strong> A link carries its destination in the HTML,
      so a broken one is reported above. A <em>button</em> is wired up in JavaScript, which this
      audit does not run — a genuinely dead button would not appear in this report.</li>
  <li><strong>Forms.</strong> Whether a form submits, and whether the enquiry reaches anyone.
      Deliberately not tested: we will not send test enquiries into a live intake system.</li>
  <li><strong>Page speed.</strong> How fast a page paints requires actually painting it.</li>
  <li><strong>Call-tracking numbers.</strong> The number hard-coded in each element is checked; what
      your call-tracking script swaps it to for a live visitor is not.</li>
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
            path.write_text(render(code, run, findings), encoding="utf-8")
            print(f"  {code.upper():<5} {len(findings):>6,} findings -> {path.name} "
                  f"({path.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    args = sys.argv[1:] or ["rr", "gl", "ah", "coc", "cad", "ar", "tdrc", "dbh", "mhd"]
    main(args, Path("reports/_client"))
