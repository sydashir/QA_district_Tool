#!/usr/bin/env python3
"""Fill an EMPTY database with synthetic demo data, so the product can be opened without a crawl.

Why this exists: a full pass over the nine brands is about twelve hours, and until it finishes the
product is nine empty cards. That makes it impossible to demo, and impossible to check on a fresh
machine that the migrations, API and SPA are actually wired to each other. This writes a small
report tree and imports it through the REAL importer (`server/importer.py`), so what gets exercised
is the same code path a real run uses — not a second, parallel way of getting rows into Postgres.

Two rules it keeps, both deliberate:

1. **Demo data must LOOK fake.** Every URL is on `<code>.demo.invalid` — `.invalid` is reserved by
   RFC 2606 and can never resolve, so a demo row can never be mistaken for a finding about a real
   client page, in the UI or in an exported sheet. The brand names and phone numbers are real
   because they come from the real config files, which is the point: the demo shows the real fleet.
2. **It refuses to touch a database that already has findings.** Seeding over real run history
   would be unrecoverable without a re-crawl. `--force` exists, and says what it will destroy.

The defect catalogue is not invented: every (check, severity, issue) below was taken from findings
the auditor actually produced, so the demo shows the real shape of the output — including the
collapsed form (`page_count` + `sources`), which is the thing about this product that most needs
explaining to a first-time reader.

Usage:
    # against a scratch database on a fresh machine
    DATABASE_URL=postgresql+psycopg://district:...@127.0.0.1:5432/district_demo \\
        python3 scripts/seed_demo.py

    python3 scripts/seed_demo.py --runs 8 --anchor 2026-09-01
    python3 scripts/seed_demo.py --clean          # delete the demo report tree, leave the DB alone
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from sqlalchemy import func, select                                    # noqa: E402
from sqlalchemy.orm import Session                                     # noqa: E402

from server.db import SessionLocal                                     # noqa: E402
from server.importer import ensure_brands, import_run, upsert_pages    # noqa: E402
from server.models import Finding, Page, PageTraffic, Run                                 # noqa: E402

# Reports go under reports/_demo/ rather than reports/<code>/, and that is load-bearing: the
# backfill importer scans reports/<code>/ for every brand code, so a demo directory sitting there
# would be swept into the real database the next time somebody backfills. `_demo` is not a brand
# code, so it is invisible to that scan.
DEMO_ROOT = REPO / "reports" / "_demo"

BRAND_CODES = ["rr", "gl", "ah", "coc", "cad", "ar", "tdrc", "dbh", "mhd"]

# Rough page counts per brand, so the demo's "pages checked" column is proportionate to the real
# fleet (RR is an order of magnitude bigger than TDRC, and the product should show that).
DEMO_PAGES = {"rr": 812, "gl": 640, "mhd": 900, "coc": 288, "cad": 244,
              "ar": 94, "ah": 176, "dbh": 118, "tdrc": 19}

# Paths reused across brands. Real slugs in shape, on a domain that cannot exist.
FIXED_PATHS = [
    "", "about-us", "admissions", "contact", "insurance-verification",
    "programs/detox", "programs/residential", "programs/outpatient",
    "alcohol-rehab/orange-county", "blog/what-to-expect-in-detox",
    "blog/paying-for-treatment", "locations", "team/clinical-director", "faq",
]

# The fleet is mostly geo pages — one template repeated per city — and that is why a single
# template fault reaches thousands of pages. A demo without them would not show the shape of the
# problem the product exists to find.
CITIES = [
    "long-beach", "huntington-beach", "irvine", "anaheim", "santa-ana", "costa-mesa",
    "newport-beach", "fullerton", "orange", "garden-grove", "tustin", "westminster",
    "laguna-beach", "mission-viejo", "san-clemente", "yorba-linda", "buena-park",
    "lake-forest", "cypress", "brea", "placentia", "stanton", "seal-beach", "dana-point",
]


# Pages that appear only in the newest run, so the "new pages discovered" panel has something in
# it. A brand-new page with a defect on it is the case worth looking at hardest — nobody has ever
# eyeballed it — so a demo that never shows one hides the panel's whole reason for existing.
NEW_PATHS = ["programs/family-support", "drug-rehab/costa-mesa-2", "blog/fall-2026-update"]


def _paths(code: str) -> list[str]:
    """Every path this brand's demo pages live on, capped by the brand's page count."""
    geo = [f"drug-rehab/{c}" for c in CITIES] + [f"alcohol-rehab/{c}" for c in CITIES[:12]]
    return (FIXED_PATHS + geo)[: max(8, min(DEMO_PAGES[code], 60))]

# Every row here was copied from the shape of a real finding: same check name, same severity, same
# issue wording. Only the URLs and page counts are synthetic. `spread` is how many pages the defect
# collapses across — 1 means a single page, >1 renders as "on N pages, one fix".
# `per_page=True` marks the checks that genuinely emit ONE ROW PER PAGE rather than collapsing:
# a too-long title or a missing <h1> is a fact about that one page, and the real reports carry
# 15,135 and 1,551 of them respectively. Mixing both forms is what makes the findings list look
# like the real thing instead of a tidy list of twenty.
CATALOGUE = [
    dict(check="phone", severity="error", spread=90,
         issue="phone number dials a different District brand",
         location="a[href^=tel:]", snippet="tel:+18665551212",
         suggestion="This number belongs to another District brand — replace it with this brand's "
                    "canonical number from the NAP sheet.",
         details={"source": "NAP sheet, verified 2026-08-03"}),
    dict(check="actions", severity="error", spread=64,
         issue='button goes nowhere: "Verify Insurance"',
         location="page body", snippet='<a class="btn">Verify Insurance</a>',
         suggestion="The link has no destination — a visitor who clicks it stays where they are.",
         details={}),
    dict(check="empty_slot", severity="error", spread=41,
         issue="empty template variable left broken text on the page",
         location="page body",
         snippet="There are at least  outpatient drug rehab programs available within  of the area",
         suggestion="A template field rendered empty, leaving a sentence with a hole in it.",
         details={}),
    dict(check="misspelling", severity="error", spread=28, per_page=True,
         issue="confirmed misspelling in page text: 'Inpateint' -> 'inpatient'",
         location="page body", snippet="Inpateint treatment options",
         suggestion="Confirmed misspelling — correct it to 'inpatient'.", details={}),
    dict(check="broken_links", severity="error", spread=1,
         issue="HTTP 404", location="page body",
         snippet="https://%(host)s/programs/soberliving-old",
         suggestion="This link is dead. Point it at the live page or remove it.", details={}),
    dict(check="broken_links", severity="error", spread=12,
         issue="malformed / truncated URL in page content", location="footer",
         snippet="https://www.instagram.comhttps://www.linkedin.com/company/example",
         suggestion="Two web addresses have been joined into one broken link — a single footer "
                    "field, so one edit fixes every page.", details={}),
    dict(check="placeholder", severity="error", spread=3,
         issue='a widget\'s "nothing here" message is showing as page content',
         location="page body", snippet="No content found.",
         suggestion="A widget is rendering its empty state to visitors.", details={}),
    dict(check="scope", severity="error", spread=7,
         issue='"county" where "country" is meant (national page)',
         location="page body", snippet="the leading provider in the county",
         suggestion="A national page says 'county'. Check whether 'country' was meant.", details={}),
    dict(check="blank", severity="error", spread=9, per_page=True,
         issue="missing <h1>", location="head", snippet=None,
         suggestion="The page has no top-level heading.", details={}),
    dict(check="schema", severity="error", spread=5,
         issue="this page declares no business in its structured data",
         location="head", snippet=None,
         suggestion="Structured data names no business, so search engines cannot attribute the "
                    "page to this brand.", details={}),
    dict(check="heading_structure", severity="warning", spread=9,
         issue="duplicate H1 across pages", location="H1",
         snippet="Drug and Alcohol Rehab",
         suggestion="Several pages share one H1, so search engines cannot tell them apart.",
         details={}),
    dict(check="meta", severity="warning", spread=33, per_page=True,
         issue="title length out of bounds", location="head",
         snippet="Drug and Alcohol Rehab Treatment Center Serving the Whole of Southern California",
         suggestion="Title is longer than search results will show.", details={}),
    dict(check="phone", severity="warning", spread=16, per_page=True,
         issue="tel: href has URL-encoded characters", location="tel:(888)%20555-0100",
         snippet="(888)%20555-0100",
         suggestion="tel: href is URL-encoded; clean the encoding.",
         details={"raw": "(888)%20555-0100", "decoded": "(888) 555-0100"}),
    dict(check="duplication", severity="warning", spread=21,
         issue="the same paragraph appears 2 times on this page", location="page body",
         snippet="Our admissions team is available 24 hours a day.",
         suggestion="The same paragraph is printed twice.", details={}),
    dict(check="blank", severity="warning", spread=1,
         issue="thin content", location="page body", snippet=None,
         suggestion="Almost no visible text on the page.", details={}),
    dict(check="enumeration", severity="warning", spread=1,
         issue="sitemap page could not be fetched (timeout/transport)", location="sitemap",
         snippet=None, suggestion="Listed in the sitemap but did not respond.", details={}),
    dict(check="broken_links", severity="info", spread=48,
         issue="an internal link goes through a redirect instead of straight to the page",
         location="page body", snippet="https://%(host)s/blog/",
         suggestion="Point the link at its final destination.", details={}),
    dict(check="heading_structure", severity="info", spread=22, per_page=True,
         issue="skipped level H1->H3", location="H3", snippet="What we treat",
         suggestion="Heading level skipped — a minor document-outline nit, not a barrier.",
         details={}),
    dict(check="enumeration", severity="info", spread=1,
         issue="noindex page missing from sitemap", location="sitemap", snippet=None,
         suggestion="Live but absent from the sitemap; it is noindex, so this is expected.",
         details={}),
]


def _fingerprint(item: dict, scope: str, n: int) -> str:
    """Stable identity for one defect.

    `scope` is a URL for a per-page finding and the HOST for a collapsed one, which mirrors the
    real checks: a too-long title belongs to a page, a wrong footer link belongs to the template.
    Triage in the product is keyed on this, so it has to stay identical across runs — that is what
    lets a finding marked "won't fix" stay marked after the next crawl.
    """
    return f"{item['check']}:demo{n}:{scope}:{item['issue']}"


def _findings_for_run(code: str, host: str, rng: random.Random, run_index: int, last_index: int,
                      stamp: str, first_stamp: str) -> list[dict]:
    """One run's worth of findings.

    Runs share most of their findings so the trend line is stable, and differ at the edges so the
    "what changed" view has something in it. The timing is chosen so the NEWEST run is the
    interesting one: a defect is fixed in it and another appears in it, because "what changed" is
    the screen somebody opens first and an empty one teaches nothing.
    """
    rows: list[dict] = []
    paths = _paths(code)
    # A brand's own slice of the catalogue: the same items every run, chosen by brand so the nine
    # cards do not all look identical.
    pick = list(CATALOGUE)
    rng.shuffle(pick)
    pick = pick[: 8 + (len(code) * 2) % 7]

    def url_at(i: int) -> str:
        return f"https://{host}/{paths[i % len(paths)]}".rstrip("/")

    for n, item in enumerate(pick):
        # Item 0 is fixed in the newest run, item 1 one run earlier, item 2 arrives in the newest.
        if n == 0 and run_index >= last_index:
            continue
        if n == 1 and run_index >= last_index - 1:
            continue
        if n == 2 and run_index < last_index:
            continue

        spread = max(1, min(item["spread"], DEMO_PAGES[code], len(paths)))
        snippet = item["snippet"]
        if snippet and "%(host)s" in snippet:
            snippet = snippet % {"host": host}

        # A finding that has been there since the first demo run must SAY so: the detail screen's
        # history is the part that tells a reader whether anything is actually being fixed.
        seen_first = stamp if (n == 2 or run_index == 0) else first_stamp
        status = "new" if (n == 2 or run_index == 0) else "persisting"

        if item.get("per_page"):
            # One row per affected page — separate defects that happen to share a description.
            for i in range(spread):
                url = (f"https://{host}/{NEW_PATHS[i % len(NEW_PATHS)]}" if n == 2 and i < 3
                       else url_at(n * 5 + i))
                rows.append({
                    "url": url, "check": item["check"],
                    "fingerprint": _fingerprint(item, url, n),
                    "severity": item["severity"], "issue": item["issue"],
                    "location": item["location"], "snippet": snippet,
                    "suggestion": item["suggestion"], "details": dict(item["details"]),
                    "first_seen": seen_first, "last_seen": stamp, "status": status,
                })
            continue

        urls = ([f"https://{host}/{q}" for q in NEW_PATHS] if n == 2
                else [url_at(n * 3 + i) for i in range(spread)])
        # A collapsed finding stores a capped sample of its sources plus the TRUE page count, which
        # is exactly what the real checks do — the UI reads page_count, never len(sources).
        details = dict(item["details"])
        page_count = max(len(urls), min(item["spread"], DEMO_PAGES[code]))
        if page_count > 1:
            details["page_count"] = page_count
            details["sources"] = urls[:5]
            if page_count > len(urls[:5]):
                details["sources_truncated"] = True

        issue = item["issue"] if page_count == 1 else f"{item['issue']} — on {page_count} pages"
        rows.append({
            "url": urls[0],
            "check": item["check"],
            "fingerprint": _fingerprint(item, host, n),
            "severity": item["severity"],
            "issue": issue,
            "location": item["location"],
            "snippet": snippet,
            "suggestion": item["suggestion"],
            "details": details,
            "first_seen": seen_first,
            "last_seen": stamp,
            "status": status,
        })

    # The two dropped items come back as resolved rows in the run that dropped them, because that
    # is how the real diff writes them — a resolved finding is IN the report, tagged, not absent.
    for n, item in enumerate(pick[:2]):
        # `>= 1` because a "fixed" row is only meaningful if the finding was OPEN in an earlier
        # run. With runs<=2 the drop window and this tail collapse onto run 0, and the first-ever
        # run would report a repair for something no run ever observed as open.
        if run_index < 1:
            continue
        if (n == 0 and run_index == last_index) or (n == 1 and run_index == last_index - 1):
            if item.get("per_page"):
                spread = max(1, min(item["spread"], DEMO_PAGES[code], len(paths)))
                gone = [(url_at(n * 5 + i), _fingerprint(item, url_at(n * 5 + i), n))
                        for i in range(spread)]
            else:
                gone = [(url_at(n * 3), _fingerprint(item, host, n))]
            # Substitute the host here TOO. The open-finding loop above does it; this tail did
            # not, so two catalogue entries carrying %(host)s wrote a literal unsubstituted token
            # into a resolved row — demo content that is itself an example of the `empty_slot`
            # defect this product exists to flag, on a URL that is not *.demo.invalid.
            gone_snippet = item["snippet"]
            if gone_snippet and "%(host)s" in gone_snippet:
                gone_snippet = gone_snippet % {"host": host}
            for url, fp in gone:
                rows.append({
                    "url": url, "check": item["check"], "fingerprint": fp,
                    "severity": item["severity"], "issue": item["issue"],
                    "location": item["location"], "snippet": gone_snippet,
                    "suggestion": item["suggestion"], "details": {},
                    "first_seen": first_stamp, "last_seen": stamp, "status": "resolved",
                })
    return rows


def write_report(code: str, when: datetime, run_index: int, last_index: int,
                 first_when: datetime) -> Path:
    """Write one synthetic report directory in the same shape a real run writes."""
    host = f"{code}.demo.invalid"
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S")
    d = DEMO_ROOT / code / when.strftime("%Y%m%d-%H%M%S")
    d.mkdir(parents=True, exist_ok=True)

    # Seeded on the BRAND, never on the run: the shuffle decides which defects this brand has, and
    # a per-run seed would reshuffle them, changing every fingerprint and making each run look like
    # a brand-new set of problems with nothing persisting.
    rng = random.Random(code)
    rows = _findings_for_run(code, host, rng, run_index, last_index, stamp,
                             first_when.strftime("%Y-%m-%dT%H:%M:%S"))

    with (d / "findings.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    open_rows = [r for r in rows if r["status"] != "resolved"]
    by_sev: dict[str, int] = {}
    by_check: dict[str, int] = {}
    for r in open_rows:
        by_sev[r["severity"]] = by_sev.get(r["severity"], 0) + 1
        by_check[r["check"]] = by_check.get(r["check"], 0) + 1

    pages = DEMO_PAGES[code]
    summary = {
        "brand": code.upper(),
        "base_url": f"https://{host}",
        "run_at": stamp,
        "pages_audited": pages,
        "pages_enumerated": pages + 4,
        "history_written": True,
        # DBH really is crawled from a fixed list and MHD really is a capped sample; the demo keeps
        # both, because those two labels are the product's most important honesty feature and a demo
        # that hides them misrepresents the tool.
        "urls_file": "config/urls/dbh.txt" if code == "dbh" else None,
        "audit_scope": {"sitemap": pages, "rest_only_added": 4, "union": pages + 4},
        "demo": True,
        "rollup": {"total": len(open_rows), "by_severity": by_sev, "by_check": by_check},
    }
    (d / "summary.json").write_text(json.dumps(summary, indent=1))
    return d


def seed(session: Session, runs: int, anchor: datetime, echo=print) -> dict:
    # NAIVE UTC, deliberately. Report timestamps on disk are naive strings and `importer` stamps
    # them `tzinfo=UTC` when it reads them back, so the whole pipeline already treats naive as UTC.
    # Using naive LOCAL here and labelling it UTC would shift every demo run by the machine's offset
    # (+5h on this box) and put the newest ones in the future — which is the bug this clamp exists
    # to prevent, reintroduced one line further down.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    brands = ensure_brands(session)
    session.commit()
    stats = {"brands": len(brands), "runs": 0, "findings": 0, "pages": 0}

    for code in BRAND_CODES:
        brand = brands.get(code.upper())
        if brand is None:
            echo(f"  {code.upper():5s} no config on disk — skipped")
            continue
        n_findings = 0
        first_when = anchor - timedelta(days=5 * (runs - 1))
        for i in range(runs):
            # Newest last, roughly a run every five days, at a plausible small-hours time.
            when = anchor - timedelta(days=5 * (runs - 1 - i), hours=i % 3)
            d = write_report(code, when, i, runs - 1, first_when)
            run, n = import_run(session, brand, d)
            if run is not None:
                run.pages_audited = DEMO_PAGES[code]
                # Clamped to `now` and stamped UTC. Two separate problems, both real:
                # the duration pushes a big brand's newest run PAST the anchor (MHD finishes at
                # anchor+2h12m), and `server/jobs.py` computes the cooldown as now() - finished_at,
                # so a future timestamp makes the next real audit sleep for hours before touching
                # the network. Everything else that writes these columns writes tz-aware UTC, so a
                # naive value also mixes two conventions in one column.
                done = min(when + timedelta(minutes=20 + DEMO_PAGES[code] // 8), now)
                run.started_at = when.replace(tzinfo=timezone.utc)
                run.finished_at = done.replace(tzinfo=timezone.utc)
                if code == "mhd":
                    run.max_pages = 900
                    run.partial_sample = True
                stats["runs"] += 1
                n_findings += n
        stats["findings"] += n_findings
        stats["pages"] += upsert_pages(session, brand)
        session.commit()
        echo(f"  {code.upper():5s} {runs} runs, {n_findings} findings")

    # The two states the Runs page exists to tell apart, which no successful run can demonstrate:
    # a brand that could not be reached at all, and a run that died part-way. Both must read as
    # "we do not know", never as a clean result — that is the whole point of having them.
    for code, status, err in (
        ("mhd", "refused", "no pages could be enumerated, so nothing was audited. The website was "
                           "unreachable or its page index is missing. Previous results are "
                           "unchanged — this brand has NOT been given a clean bill of health."),
        ("rr", "failed", "the audit reached the site but was throttled part-way, so it produced no "
                         "usable result. The run is marked failed because it cannot be reported."),
    ):
        brand = brands.get(code.upper())
        if brand is None:
            continue
        when = anchor - timedelta(days=2, hours=6)
        session.add(Run(brand_id=brand.id, started_at=when.replace(tzinfo=timezone.utc),
                        finished_at=(when + timedelta(minutes=3)).replace(tzinfo=timezone.utc),
                        status=status, error_text=err, pages_audited=0, pages_enumerated=0))
        stats["runs"] += 1
    session.commit()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=6, help="runs of history per brand (default 6)")
    ap.add_argument("--anchor", default=None,
                    help="date of the newest demo run, YYYY-MM-DD (default: today)")
    ap.add_argument("--force", action="store_true",
                    help="seed even though the database already holds findings")
    ap.add_argument("--clean", action="store_true",
                    help="delete reports/_demo and exit; the database is not touched")
    args = ap.parse_args()

    if args.clean:
        if DEMO_ROOT.exists():
            shutil.rmtree(DEMO_ROOT)
            print(f"removed {DEMO_ROOT.relative_to(REPO)}")
        else:
            print("nothing to remove")
        return 0

    anchor = (datetime.strptime(args.anchor, "%Y-%m-%d") if args.anchor
              else datetime.now(timezone.utc).replace(tzinfo=None, hour=2, minute=10,
                                                      second=0, microsecond=0))

    dsn = os.environ.get("DATABASE_URL", "(DATABASE_URL unset — server/db.py default)")
    # Print it before writing anything. Seeding the wrong database is the one unrecoverable
    # mistake this script can make, and the DSN is the only thing that distinguishes them.
    print(f"target database: {dsn.split('@')[-1] if '@' in dsn else dsn}")

    with SessionLocal() as session:
        # COUNT EVERY TABLE THAT HOLDS REAL WORK, not just findings. Counting findings alone looked
        # sufficient and is not: `server/api.py` commits the Run row at POST time and findings are
        # only inserted when the crawl finishes, so for the whole ~10h of a first RR crawl the
        # database holds real runs and ZERO findings. `failed` and `refused` runs stay that way
        # permanently. `page_traffic` is imported from GSC exports with no run at all. Any of those
        # would have let the guard wave the seed through onto real data — and `seed()` is not
        # additive: `ensure_brands` rewrites brand rows and resets MHD's 900-page cap to None.
        counts = {
            "findings": session.scalar(select(func.count()).select_from(Finding)) or 0,
            "runs": session.scalar(select(func.count()).select_from(Run)) or 0,
            "pages": session.scalar(select(func.count()).select_from(Page)) or 0,
            "traffic": session.scalar(select(func.count()).select_from(PageTraffic)) or 0,
        }
        occupied = {k: v for k, v in counts.items() if v}
        if occupied and not args.force:
            detail = ", ".join(f"{v:,} {k}" for k, v in occupied.items())
            print(f"REFUSED: this database already holds {detail}. Seeding would mix synthetic rows "
                  f"into real history, and it is not additive — it also rewrites brand rows, "
                  f"including MHD's 900-page sample cap.\n"
                  f"         Point DATABASE_URL at a scratch database, or pass --force if you are "
                  f"certain this one is disposable.", file=sys.stderr)
            return 1
        if occupied:
            print("--force: writing demo rows alongside "
                  + ", ".join(f"{v:,} existing {k}" for k, v in occupied.items()))

        print(f"seeding {args.runs} runs per brand, newest {anchor:%Y-%m-%d}")
        stats = seed(session, args.runs, anchor)

    print(f"\ndone: {stats['brands']} brands, {stats['runs']} runs, {stats['findings']:,} findings, "
          f"{stats['pages']:,} pages")
    print("every demo URL is on *.demo.invalid, which cannot resolve — that is how you tell demo "
          "rows from real ones.")
    print(f"reports written to {DEMO_ROOT.relative_to(REPO)}; remove with --clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
