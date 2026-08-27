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
            grouped.setdefault((f[0], f[1], f[3], f[5]), []).append(f)
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
            snippet = html.escape((f[5] or "")[:220])
            items.append(
                f"<li class='{html.escape(f[2])}'>"
                f"<div class='issue'>{html.escape(f[3])} {pages_txt}</div>"
                + (f"<div class='snippet'>{snippet}</div>" if snippet else "")
                + f"<div class='where'><a href='{html.escape(f[4])}'>{html.escape(f[4][:96])}</a></div>"
                + (f"<div class='fix'>{html.escape((f[6] or '')[:300])}</div>" if f[6] else "")
                + "</li>")
        more = ""
        if len(rows) > TOP_N:
            more = (f"<p class='more'>and {len(rows) - TOP_N:,} more of this kind — "
                    f"the full list is in the shared sheet.</p>")
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
@media(prefers-color-scheme:dark){{:root{{--ink:#e8eaf0;--mut:#9aa2b1;--line:#2a2f3a;--bg:#14161a}}
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
<footer>Generated {datetime.now(timezone.utc).strftime('%d %B %Y')} from the automated audit.
Findings marked <em>certain</em> were verified deterministically; the rest are worth a look.
Anything the audit cannot see is listed in "What the audit does not check".</footer>
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
