"""Import a Google Search Console CSV export and rank findings by who is actually affected.

**v1 IS THE CSV PATH, DELIBERATELY.** The API would need the service account added as a user on all
nine Search Console properties — a request through the client, nine separate grants, nine chances
for one to be missed. The UI export needs none of that, so the ranking can be proved useful before
anyone is asked for anything. See docs/plans/2026-08-31-traffic-weighting-design.md.

**THE RULE THAT SHAPES THIS FILE.** Google's CSV writes MISSING values as zeros — its own
documentation states that values shown as `~` or `-` in the report "will be zeros in the downloaded
data". So a zero in a CSV cannot be distinguished from a real zero, and a zero that means "we do not
know" must never rank a finding as unimportant. **Every CSV row is stored `value_state='unknown'`.**
Only the API can ever produce `measured`.

Usage:
    python3 scripts/traffic_import.py gl ~/Downloads/gl-pages.csv --period 2026-06..2026-08
    python3 scripts/traffic_import.py --match-report          # coverage per brand, no import
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text as sql          # noqa: E402

from server.db import SessionLocal                  # noqa: E402
from server.models import Brand, PageTraffic, TrafficImport  # noqa: E402
# The join key and the duplicate-row rule live in server/traffic.py, because the report and the API
# read traffic through the same key the importer writes it with. Two copies would drift.
from server.traffic import aggregate, url_key       # noqa: E402,F401

# Header names seen on Google's per-page export. Matched case-insensitively, first hit wins.
# NOT a guess-and-hope: an export whose headers match none of these RAISES, naming what it saw,
# because a mis-detected column would rank every finding on the wrong number in silence.
_URL_HEADERS = ("top pages", "page", "url", "landing page", "address")
_CLICK_HEADERS = ("clicks", "url clicks")
_IMPR_HEADERS = ("impressions", "impr.", "impressions ")
_POS_HEADERS = ("position", "average position", "avg. pos")


def _num(raw: str) -> float | None:
    """A cell to a number. Handles '1,234', '2.86%', '' and '-'."""
    if raw is None:
        return None
    t = str(raw).strip().replace(",", "").replace("%", "")
    if not t or t in {"-", "~"}:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _pick(headers: list[str], wanted: tuple[str, ...]) -> str | None:
    low = {h.strip().lower(): h for h in headers}
    for w in wanted:
        if w in low:
            return low[w]
    return None


def parse_csv(path: Path) -> list[dict]:
    """GSC export -> rows. Raises ValueError naming the headers if the shape is unrecognised."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        url_h = _pick(headers, _URL_HEADERS)
        if not url_h:
            raise ValueError(
                f"no page/URL column in {path.name}. Saw headers: {headers}. "
                f"Expected one of {list(_URL_HEADERS)} — if Google has renamed it, add the new name "
                f"to _URL_HEADERS rather than letting a column be guessed.")
        click_h = _pick(headers, _CLICK_HEADERS)
        impr_h = _pick(headers, _IMPR_HEADERS)
        pos_h = _pick(headers, _POS_HEADERS)

        rows = []
        for r in reader:
            raw = (r.get(url_h) or "").strip()
            if not raw or not raw.lower().startswith("http"):
                continue
            clicks = _num(r.get(click_h)) if click_h else None
            impr = _num(r.get(impr_h)) if impr_h else None
            pos = _num(r.get(pos_h)) if pos_h else None
            rows.append({
                "url": raw, "url_key": url_key(raw),
                "clicks": int(clicks) if clicks is not None else None,
                "impressions": int(impr) if impr is not None else None,
                "position": pos,
                # ALWAYS unknown from a CSV. See the module docstring — this is not a default that
                # a caller may override, it is the honest state of every number in this file.
                "value_state": "unknown",
            })
    return rows


def store(brand_code: str, rows: list[dict], period: str) -> int:
    with SessionLocal() as s:
        brand = s.scalar(select(Brand).where(Brand.code == brand_code.upper()))
        if brand is None:
            print(f"  unknown brand {brand_code}"); return 0
        s.execute(sql("""delete from page_traffic
                         where brand_id=:b and period=:p and source='gsc_csv'"""),
                  {"b": brand.id, "p": period})
        # A CSV lists one page several times (jump-link anchors, utm variants, slashed and bare).
        # `aggregate` sums the visits and keeps the largest impression row — see its docstring.
        objs = [PageTraffic(
                    brand_id=brand.id, url_key=r["url_key"], period=period,
                    impressions=r["impressions"], clicks=r["clicks"], position=r["position"],
                    source="gsc_csv", value_state=r["value_state"])
                for r in aggregate(rows)]
        s.bulk_save_objects(objs)
        # How big the export was, so a report can tell a CUT-OFF export from a complete one.
        s.execute(sql("""delete from traffic_imports
                         where brand_id=:b and period=:p and source='gsc_csv'"""),
                  {"b": brand.id, "p": period})
        s.add(TrafficImport(brand_id=brand.id, period=period, source="gsc_csv",
                            rows_read=len(rows), pages_stored=len(objs)))
        s.commit()
        return len(objs)


MATCH_FLOOR = 60        # percent of finding pages matched, below which the report explains why


def diagnose(rate: float, export_pages: int, export_matched: int, finding_pages: int) -> str:
    """Why a match rate is low — because the two causes need opposite fixes.

    The first version said "check the property type" for every low rate. On the 2026-09-14 exports
    that was the wrong advice for five brands: GL matched 27% of its finding pages, but 971 of the
    996 pages in its export matched — the join worked, and the export simply stops at 1,000 rows
    while GL has 3,595 pages with findings. Telling someone to re-check the property there sends
    them after a problem that does not exist.

    So look at the export's side of the join. If most of ITS pages matched, the key and the property
    are right and the export is just smaller than the site. If few did, the export is about some
    other set of pages, and the property type is the thing to check.
    """
    if rate >= MATCH_FLOOR:
        return ""
    if export_pages and export_matched / export_pages >= 0.5:
        return (f"   LIMITED BY THE EXPORT — {export_matched:,} of its {export_pages:,} pages matched, "
                f"so the join works; it lists fewer pages than the {finding_pages:,} with findings. "
                f"Google's UI export stops at 1,000 rows; the API returns up to 25,000.")
    return "   LOW — few of the export's pages matched; check the property type (domain vs URL-prefix)"


def match_report(brand_codes: list[str]) -> None:
    """How many pages carrying findings actually matched traffic data — per brand.

    Without this an unmatched page is weighted zero and is indistinguishable from a page with no
    traffic. The number has to be visible or the ranking cannot be trusted.
    """
    LATEST = "(select max(id) from runs where status='ok' group by brand_id)"
    with SessionLocal() as s:
        print(f"  {'brand':<6}{'pages w/ findings':>19}{'matched':>10}{'rate':>8}   state")
        for code in brand_codes:
            brand = s.scalar(select(Brand).where(Brand.code == code.upper()))
            if brand is None:
                continue
            urls = [r[0] for r in s.execute(sql(f"""
                select distinct f.url from findings f
                where f.brand_id=:b and f.run_id in {LATEST}"""), {"b": brand.id})]
            have = {r[0] for r in s.execute(sql(
                "select url_key from page_traffic where brand_id=:b"), {"b": brand.id})}
            if not have:
                print(f"  {code.upper():<6}{len(urls):>19}{'-':>10}{'-':>8}   NOT CONNECTED")
                continue
            hit = sum(1 for u in urls if url_key(u) in have)
            rate = hit / len(urls) * 100 if urls else 0
            flag = diagnose(rate, len(have), len(have & {url_key(u) for u in urls}), len(urls))
            print(f"  {code.upper():<6}{len(urls):>19}{hit:>10}{rate:>7.0f}%{flag}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("brand", nargs="?")
    ap.add_argument("csv", nargs="?")
    ap.add_argument("--period", default="unspecified")
    ap.add_argument("--match-report", action="store_true")
    a = ap.parse_args()
    ALL = ["gl", "rr", "cad", "coc", "ah", "ar", "tdrc", "dbh", "mhd"]
    if a.match_report:
        match_report(ALL)
    else:
        if not (a.brand and a.csv):
            ap.error("give a brand and a CSV, or --match-report")
        rows = parse_csv(Path(a.csv).expanduser())
        n = store(a.brand, rows, a.period)
        print(f"  {a.brand.upper()}: {len(rows)} row(s) read, {n} stored for period {a.period!r}")
        print("  every value stored as 'unknown' — Google writes missing values as zeros in CSV.")
