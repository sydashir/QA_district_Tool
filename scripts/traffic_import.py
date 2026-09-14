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

**THE API PATH (2026-09-15) replaces the CSV where it is connected.** The UI export stops at 1,000
rows, which left RR 81% and GL 74% of findings unweighted. With the service account granted on all
nine properties, `--api` pages through every row Google has. The CSV path stays for a brand whose
property is not connected.

Usage:
    python3 scripts/traffic_import.py --api --period 2026-06-13..2026-09-12   # all nine, via the API
    python3 scripts/traffic_import.py gl --api                                  # one brand, last ~3 months
    python3 scripts/traffic_import.py gl ~/Downloads/gl-pages.csv --period 2026-06..2026-08
    python3 scripts/traffic_import.py --match-report          # coverage per brand, no import
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse

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


def store(brand_code: str, rows: list[dict], period: str, source: str = "gsc_csv") -> int:
    with SessionLocal() as s:
        brand = s.scalar(select(Brand).where(Brand.code == brand_code.upper()))
        if brand is None:
            print(f"  unknown brand {brand_code}"); return 0
        s.execute(sql("""delete from page_traffic
                         where brand_id=:b and period=:p and source=:s"""),
                  {"b": brand.id, "p": period, "s": source})
        # Google lists one page several times (jump-link anchors, utm variants, slashed and bare) —
        # in the CSV and in the API alike. `aggregate` sums the visits and keeps the largest
        # impression row — see its docstring.
        objs = [PageTraffic(
                    brand_id=brand.id, url_key=r["url_key"], period=period,
                    impressions=r["impressions"], clicks=r["clicks"], position=r["position"],
                    source=source, value_state=r["value_state"])
                for r in aggregate(rows)]
        s.bulk_save_objects(objs)
        # How big the import was, so a report can tell a CUT-OFF export from a complete one.
        s.execute(sql("""delete from traffic_imports
                         where brand_id=:b and period=:p and source=:s"""),
                  {"b": brand.id, "p": period, "s": source})
        s.add(TrafficImport(brand_id=brand.id, period=period, source=source,
                            rows_read=len(rows), pages_stored=len(objs)))
        s.commit()
        return len(objs)


# ---------------------------------------------------------------------------- the API path
API = "https://www.googleapis.com/webmasters/v3"
API_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
# Google's maximum per request; everything past it is reached by startRow, per Google's own guide.
API_PAGE_ROWS = 25_000
_RETRYABLE = {429, 500, 502, 503, 504}


def pick_property(base_url: str, site_entries: list[dict]) -> dict | None:
    """The Search Console property that holds the pages we audit, chosen from `sites.list`.

    NEVER ASKED, NEVER HARDCODED (design doc §1): the property is read from what Google says the
    account can see. An exact URL-prefix property on the audited host wins; otherwise a domain
    property covering that host. A URL-prefix property on a DIFFERENT host is never used — one on
    the bare host while we audit www returns zero rows and looks exactly like a failed grant.
    """
    host = (urlparse(base_url).netloc or base_url).lower().split(":")[0]
    by_url = {e.get("siteUrl"): e for e in site_entries}
    for cand in (f"https://{host}/", f"http://{host}/"):
        if cand in by_url:
            return by_url[cand]
    labels = host.split(".")
    for i in range(len(labels) - 1):
        cand = "sc-domain:" + ".".join(labels[i:])
        if cand in by_url:
            return by_url[cand]
    return None


def fetch_pages(session, site_url: str, start: str, end: str, *, page_size: int = API_PAGE_ROWS,
                sleep=time.sleep, attempts: int = 5) -> list[dict]:
    """Every page row Google has for the period, paged the way Google's guide says to.

    Stops ONLY on a response with 0 rows — Google's stated end signal. A short page is not taken as
    the end, and a rate-limit or server error is retried with backoff rather than read as "no more
    rows": either shortcut would silently truncate the site, which is the failure this path exists
    to remove. Anything else Google refuses raises, with Google's own message.
    """
    url = f"{API}/sites/{quote(site_url, safe='')}/searchAnalytics/query"
    rows: list[dict] = []
    start_row = 0
    while True:
        body = {"startDate": start, "endDate": end, "dimensions": ["page"], "type": "web",
                "dataState": "final", "rowLimit": page_size, "startRow": start_row}
        for attempt in range(attempts):
            resp = session.post(url, json=body, timeout=120)
            if resp.status_code not in _RETRYABLE:
                break
            sleep(2 ** (attempt + 1))
        if not resp.ok:
            try:
                msg = resp.json().get("error", {}).get("message") or resp.text
            except ValueError:
                msg = resp.text
            raise RuntimeError(f"Search Console API HTTP {resp.status_code} for {site_url}: "
                               f"{str(msg)[:300]}")
        batch = resp.json().get("rows") or []
        if not batch:
            return rows
        rows.extend(batch)
        start_row += page_size


def api_rows(raw: list[dict]) -> list[dict]:
    """API rows -> the same row shape the CSV parser produces, keyed the same way."""
    out = []
    for r in raw:
        u = ((r.get("keys") or [""])[0] or "").strip()
        if not u.lower().startswith("http"):
            continue
        out.append({"url": u, "url_key": url_key(u),
                    "clicks": int(round(r.get("clicks") or 0)),
                    "impressions": int(round(r.get("impressions") or 0)),
                    "position": r.get("position"),
                    # Numbers Google reported, not a download that writes missing values as zero.
                    "value_state": "measured"})
    return out


def default_api_period(today: date | None = None) -> str:
    """The last 92 days of FINAL data. Google: "Data is typically available after 2-3 days"."""
    end = (today or date.today()) - timedelta(days=3)
    return f"{end - timedelta(days=91)}..{end}"


def _api_session():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    # The same key, found the same way, as the sheet export — one definition of where it lives.
    from auditor.publish import resolve_credentials
    creds = service_account.Credentials.from_service_account_file(resolve_credentials(),
                                                                  scopes=[API_SCOPE])
    return AuthorizedSession(creds)


def import_api(codes: list[str], period: str) -> None:
    start, _, end = period.partition("..")
    session = _api_session()
    resp = session.get(f"{API}/sites", timeout=60)
    if not resp.ok:
        raise RuntimeError(f"sites.list HTTP {resp.status_code}: {resp.text[:300]}")
    sites = resp.json().get("siteEntry", [])
    print(f"  the service account can see {len(sites)} Search Console "
          f"propert{'y' if len(sites) == 1 else 'ies'}; period {period}")
    with SessionLocal() as s:
        bases = {b.code: b.base_url for b in s.scalars(select(Brand))}
    for code in (c.upper() for c in codes):
        base = bases.get(code)
        prop = pick_property(base, sites) if base else None
        if prop is None:
            # Said, never skipped in silence: a brand with no property reads "not connected" in
            # its report, which is true; an empty import would read as a site with no traffic.
            print(f"  {code:<5} NOT CONNECTED — none of the account's properties covers {base}")
            continue
        rows = api_rows(fetch_pages(session, prop["siteUrl"], start, end))
        n = store(code, rows, period, source="gsc_api")
        print(f"  {code:<5} {prop['siteUrl']} ({prop['permissionLevel']}): {len(rows):,} row(s) "
              f"read, {n:,} page(s) stored")


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
    ap.add_argument("--api", action="store_true",
                    help="fetch every page from the Search Console API instead of reading a CSV")
    ap.add_argument("--match-report", action="store_true")
    a = ap.parse_args()
    ALL = ["gl", "rr", "cad", "coc", "ah", "ar", "tdrc", "dbh", "mhd"]
    if a.match_report:
        match_report(ALL)
    elif a.api:
        period = default_api_period() if a.period == "unspecified" else a.period
        try:
            _s, _, _e = period.partition("..")
            date.fromisoformat(_s), date.fromisoformat(_e)
        except ValueError:
            ap.error(f"--api needs --period YYYY-MM-DD..YYYY-MM-DD, got {period!r}")
        import_api([a.brand] if a.brand else ALL, period)
    else:
        if not (a.brand and a.csv):
            ap.error("give a brand and a CSV, or --match-report")
        rows = parse_csv(Path(a.csv).expanduser())
        n = store(a.brand, rows, a.period)
        print(f"  {a.brand.upper()}: {len(rows)} row(s) read, {n} stored for period {a.period!r}")
        print("  every value stored as 'unknown' — Google writes missing values as zeros in CSV.")
