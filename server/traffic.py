"""Search traffic joined to findings at READ time — the one definition the report and the API share.

Design: docs/plans/2026-08-31-traffic-weighting-design.md. The rules below are the parts of it that
turn into code, and each one exists because the obvious implementation is wrong on real data:

* **Harm first, traffic second.** Traffic orders findings WITHIN a severity, never across it. A
  button dialling a competitor on a quiet page still outranks a faint colour on a busy one.
* **Reach is a UNION over distinct pages, never a sum of member reaches.** GL has a `dead_cta` group
  of 13 findings on exactly one URL; summing would multiply that page's traffic by 13.
* **A partial page list is never presented as a measurement.** A finding on 1,343 pages that names 8
  of them gets "at least", and says 8 of 1,343.
* **Five no-data states, five different sentences.** Not connected, unmatched, a zero Google may
  have invented, a page that cannot have search traffic by design, and a real number. Collapsing
  any two of them tells a reader something false about a page.
* **A zero from a CSV is not a zero.** Google writes missing figures as zeros in the download, so a
  CSV zero ranks as unweighted and is described as "not proof", never as "nobody came".

Kept out of `auditor/` on purpose: nothing here is hashed, so iterating on it costs no re-crawl.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse

from sqlalchemy import text as sql


def url_key(url: str) -> str:
    """The join key: lowercased host + path, no scheme, no trailing slash, no query, no fragment.

    Measured against our own corpus (15,959 pages): we store 100% https, 0% trailing slash, 0%
    query, 0% fragment, 0% mixed case — but the LIVE sites serve the slashed form on 96.7-100% of
    pages, so Google reports `…/mescaline/` where we store `…/mescaline`. Raw equality between the
    two matches ~0.2%; this normalisation is what makes the join possible at all.

    `www` is KEPT on purpose. GL and RR are 100% `www` and the other seven are 100% bare host, so
    stripping it would merge two different hosts — and a key that over-merges invents traffic,
    which is worse than having none.
    """
    p = urlparse(url.strip())
    host = (p.netloc or "").lower()
    path = (p.path or "/").rstrip("/")
    return f"{host}{path}"


def aggregate(rows: list[dict]) -> list[dict]:
    """One row per url_key: clicks SUMMED, impressions and position from the row with most impressions.

    Google's export lists one page several times — Elementor table-of-contents anchors
    (`#elementor-toc__heading-anchor-3`), `?utm_source=` variants, slashed and bare forms. The first
    importer kept whichever row came first, and rows arrive in click order, so it threw away the
    anchor rows: measured on the 2026-09-14 exports that undercounted impressions by 26% on AR, 20%
    on CAD and 12% on DBH.

    Summing everything is wrong too, and in the other direction. A searcher clicks ONE result, so
    clicks on the variants are separate visits and add up. But a page and its own jump links are
    shown together in the SAME result, so their impressions overlap — summing them would count one
    appearance several times. The largest single row is the defensible floor.
    """
    out: dict[str, dict] = {}
    for r in rows:
        k = r["url_key"]
        cur = out.get(k)
        if cur is None:
            out[k] = dict(r)
            continue
        if r.get("clicks") is not None:
            cur["clicks"] = (cur.get("clicks") or 0) + r["clicks"]
        if (r.get("impressions") or 0) > (cur.get("impressions") or 0):
            cur["impressions"], cur["position"] = r["impressions"], r.get("position")
    return list(out.values())


# ---------------------------------------------------------------------------- what cannot be weighted
# Enumeration findings are largely about pages that CANNOT appear in search, and counting them as
# matching failures would make the match rate look broken and bury the genuine gaps. The reason is
# printed on the finding rather than silently applied: a silent exclusion is a rule nobody can see,
# and the next person to wonder why these are never weighted would add them back.
_NOINDEX = "This page is no-indexed, so it has no search impressions by design — not weighted."
_ERRORS = "This page returns an error, so nobody reaches it from search — not weighted."
NO_SEARCH_BY_DESIGN: dict[str, str] = {
    "noindex_unsitemapped": _NOINDEX,
    "cruft_noindex": _NOINDEX,
    "sitemap_dead": _ERRORS,
    "rest_404": _ERRORS,
    "redirects_off_brand": ("This address redirects to a different website, so any search traffic "
                            "belongs to that site — not weighted."),
    "crawl_incomplete": ("This is about the audit's own coverage, not a page visitors land on — "
                         "not weighted."),
}

# Where the damage happens IN the search results page, the right weight is impressions: a bad title
# costs the clicks it never got, which no click count can show. Everything else hurts a visitor who
# is already on the page, so it ranks on visits. Kept in step with the report's search section by
# `test_the_search_section_ranks_on_impressions_and_no_other_does`.
IMPRESSION_KEYS = ("meta:*", "schema:missing", "schema:invalid_json",
                   "enumeration:indexable_unsitemapped", "enumeration:sitemap_dead",
                   "broken_links:redirected_internal")


def ranks_on_impressions(check: str, cls: str | None) -> bool:
    for k in IMPRESSION_KEYS:
        c, _, want = k.partition(":")
        if check == c and (want == "*" or want == (cls or "")):
            return True
    return False


# ---------------------------------------------------------------------------- loading
# Google's Search Console UI export stops here. Five of the seven 2026-09-14 exports hit it exactly.
EXPORT_ROW_CAP = 1000


@dataclass(frozen=True)
class BrandTraffic:
    period: str
    pages: dict[str, tuple[int, int]]        # url_key -> (clicks, impressions)
    measured: bool                           # False for every CSV import — see the module docstring
    # True: the export hit Google's row cap, so a page missing from it may still be busy. False: the
    # export was complete. None: not recorded (an import older than `traffic_imports`).
    capped: bool | None = None

    @property
    def label(self) -> str:
        return period_label(self.period)


def period_label(period: str) -> str:
    """'2026-06-13..2026-09-12' -> '13 Jun – 12 Sep 2026'. Anything else is printed as given."""
    try:
        a, b = (date.fromisoformat(p) for p in period.split(".."))
    except ValueError:
        return period
    if a.year == b.year:
        return f"{a.day} {a:%b} – {b.day} {b:%b %Y}"
    return f"{a.day} {a:%b %Y} – {b.day} {b:%b %Y}"


def load_brand_traffic(session, brand_code: str) -> BrandTraffic | None:
    """The brand's most recently imported period, or None when it has no traffic data at all."""
    newest = session.execute(sql("""
        SELECT t.period FROM page_traffic t JOIN brands b ON b.id = t.brand_id
        WHERE b.code = :c GROUP BY t.period ORDER BY max(t.fetched_at) DESC LIMIT 1"""),
        {"c": brand_code.upper()}).first()
    if not newest:
        return None
    pages: dict[str, tuple[int, int]] = {}
    measured = True
    # API rows (if any ever exist) are read last so they win over a CSV row for the same page.
    for key, clicks, impr, state in session.execute(sql("""
            SELECT t.url_key, t.clicks, t.impressions, t.value_state
            FROM page_traffic t JOIN brands b ON b.id = t.brand_id
            WHERE b.code = :c AND t.period = :p
            ORDER BY CASE WHEN t.source = 'gsc_api' THEN 1 ELSE 0 END"""),
            {"c": brand_code.upper(), "p": newest[0]}):
        pages[key] = (clicks or 0, impr or 0)
        measured = measured and state == "measured"
    rows_read = session.execute(sql("""
        SELECT max(i.rows_read) FROM traffic_imports i JOIN brands b ON b.id = i.brand_id
        WHERE b.code = :c AND i.period = :p AND i.source = 'gsc_csv'"""),
        {"c": brand_code.upper(), "p": newest[0]}).scalar()
    capped = None if rows_read is None else rows_read >= EXPORT_ROW_CAP
    return BrandTraffic(period=newest[0], pages=pages, measured=measured, capped=capped)


# ---------------------------------------------------------------------------- reach
def affected(url: str, sources, page_count: int) -> tuple[set[str], bool]:
    """The page keys a finding names, and whether they are ALL of the pages it is on.

    `sources` is the stored page list when there is one; a single-page finding has none and is on
    its own URL. Complete means the list is as long as the page count — a template finding on 1,343
    pages stores 8, and those 8 are a sample, not the reach.
    """
    names = [u for u in sources if u] if isinstance(sources, list) and sources else [url]
    distinct = set(names)
    return {url_key(u) for u in distinct}, len(distinct) >= (page_count or 1)


def union(members: list[tuple[set[str], bool, int]]) -> tuple[set[str], int, bool]:
    """Merge several findings into one row: (page keys, pages affected, complete).

    When every member names all of its pages, the page count IS the number of distinct pages. When
    any member is a sample, the distinct count is only a floor and the old summed count stays — it
    may overlap, but replacing it with the sample would understate a 1,343-page fault as 8 pages.
    """
    keys: set[str] = set()
    for k, _complete, _n in members:
        keys |= k
    if all(c for _k, c, _n in members):
        return keys, max(len(keys), 1), True
    return keys, max(sum(n for _k, _c, n in members), len(keys)), False


@dataclass(frozen=True)
class Reach:
    state: str            # weighted | zero | unmatched | by_design | not_connected
    known: int = 1        # affected pages the audit can name
    total: int = 1        # affected pages there are
    clicks: int = 0
    impressions: int = 0
    busiest: int = 0      # visits to the busiest matched page
    matched: int = 0      # named pages found in the traffic data
    reason: str = ""
    period: str = ""
    measured: bool = False
    capped: bool | None = None

    @property
    def partial(self) -> bool:
        return self.known < self.total

    def rank(self, by_impressions: bool) -> tuple[int, int, int]:
        """Sort key, smaller first. Only a real number ranks; every no-data state ties behind it."""
        if self.state != "weighted":
            return (1, 0, 0)
        a, b = ((self.impressions, self.clicks) if by_impressions
                else (self.clicks, self.impressions))
        return (0, -a, -b)


def reach(keys: set[str], total: int, traffic: BrandTraffic | None,
          check: str, cls: str | None) -> Reach:
    known = len(keys) or 1
    total = max(total or 1, known)
    if traffic is None:
        return Reach("not_connected", known, total)
    base = dict(known=known, total=total, period=traffic.label, measured=traffic.measured,
                capped=traffic.capped)
    if check == "enumeration" and (cls or "") in NO_SEARCH_BY_DESIGN:
        return Reach("by_design", reason=NO_SEARCH_BY_DESIGN[cls or ""], **base)
    hits = [traffic.pages[k] for k in keys if k in traffic.pages]
    if not hits:
        return Reach("unmatched", **base)
    clicks = sum(c for c, _i in hits)
    impressions = sum(i for _c, i in hits)
    # A CSV zero may be a figure Google withheld, so it is not a number to rank on.
    state = "weighted" if (clicks or impressions) else "zero"
    return Reach(state, clicks=clicks, impressions=impressions,
                 busiest=max(c for c, _i in hits), matched=len(hits), **base)


# ---------------------------------------------------------------------------- the words
NOT_CONNECTED = ("Traffic data is not connected for this site, so these findings are listed in the "
                 "usual order and none of them are weighted. It does not mean these pages are quiet.")


def _n(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def sentence(r: Reach) -> str:
    """What a reader is told about this finding's traffic. Empty when the brand is not connected —
    that is said ONCE for the whole report, not under every finding."""
    sample = (f" Checked on the {r.known:,} of {r.total:,} affected pages this audit lists by name."
              if r.partial else "")
    many = r.known > 1
    if r.state == "not_connected":
        return ""
    if r.state == "by_design":
        return r.reason
    if r.state == "unmatched":
        # What "not in the data" means depends on whether the export was cut off. The join itself
        # matches 90-100% of the pages an export contains, so on a capped export the honest cause
        # is the cap, and on a complete one it is most likely a page Google never showed.
        if r.capped:
            return (("These pages are not in Google's traffic export, which stops at the site's "
                     "1,000 pages with the most clicks, so they are unweighted. That says nothing "
                     "about how busy the pages are." if many else
                     "This page is not in Google's traffic export, which stops at the site's 1,000 "
                     "pages with the most clicks, so it is unweighted. That says nothing about how "
                     "busy the page is.") + sample)
        if r.capped is False:
            return (("These pages are not in Google's traffic data for the period, so they are "
                     "unweighted. The export for this site was complete, so they most likely had no "
                     "search impressions, but a missing row cannot prove that." if many else
                     "This page is not in Google's traffic data for the period, so it is unweighted. "
                     "The export for this site was complete, so the page most likely had no search "
                     "impressions, but a missing row cannot prove that.") + sample)
        return (("We could not match these pages to the traffic data, so they are unweighted. That "
                 "is a gap in our matching, not a measurement of the pages." if many else
                 "We could not match this page to the traffic data, so it is unweighted. That is a "
                 "gap in our matching, not a measurement of the page.") + sample)
    if r.state == "zero":
        if r.measured:
            return ("This page had no search impressions in the period. It may be new, no-indexed, "
                    "or reached another way." + sample)
        return (f"Google's traffic export shows no search visits or impressions for "
                f"{'these pages' if many else 'this page'}, {r.period}. That export writes missing "
                f"figures as zero, so this is not proof nobody found "
                f"{'them' if many else 'it'}." + sample)
    lead = "at least " if r.partial else ""
    out = (f"Search traffic, {r.period}: {lead}{_n(r.clicks, 'visit', 'visits')} from Google and "
           f"{_n(r.impressions, 'impression', 'impressions')}.")
    if r.matched > 1:
        out += f" The busiest affected page had {_n(r.busiest, 'visit', 'visits')}."
    return out + sample


def coverage(weighted: int, total: int, capped: bool | None,
             noun: str = "findings here") -> str:
    """How much of a list the traffic order actually reaches. Said wherever that order is used.

    Measured 2026-09-14: RR leaves 81% of its findings unweighted and GL 74%, both on capped exports.
    A ranking that reaches a fifth of a site's findings orders the busiest part of it and nothing
    else, and a reader who is not told reads everything below the weighted rows as "quieter".
    """
    if not total:
        return ""
    pct = round(weighted / total * 100)
    share = f"{weighted:,} of the {total:,} {noun} ({pct}%)"
    if capped:
        lead = ("This traffic ranking only works for the busiest part of the site. "
                if weighted * 2 < total else "")
        return (f"{lead}Google's export stops at the site's 1,000 pages with the most clicks, so "
                f"only {share} are on pages it covers and can be ranked by traffic. The other "
                f"{100 - pct}% keep the usual order, which says nothing about how busy their "
                f"pages are.")
    if capped is False:
        return (f"{share} are on pages in the traffic data and are ranked by it. Google's export "
                f"for this site was complete, so the other {100 - pct}% are most likely on pages "
                f"with no search traffic in the period; they keep the usual order.")
    return (f"{share} are on pages in the traffic data and are ranked by it. The other "
            f"{100 - pct}% keep the usual order, which says nothing about how busy their pages are.")


def short(r: Reach) -> str | None:
    """The compact form for a table row. The full sentence goes in its tooltip and on the detail page."""
    return {
        "weighted": (f"{'at least ' if r.partial else ''}{r.clicks:,} search visits · "
                     f"{r.impressions:,} impressions"),
        "zero": "no search visits recorded",
        "unmatched": "not in the traffic data",
        "by_design": "not weighted",
    }.get(r.state)
