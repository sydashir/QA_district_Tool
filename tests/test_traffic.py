"""Traffic ranking, v1: the Google Search Console CSV path.

Chosen deliberately over the API for v1 because it needs NO credentials and no request to the
client — nine manual exports prove the ranking is useful before anyone is asked for nine grants.

The rule that shapes everything here: **Google's CSV writes MISSING values as zeros.** Its own
documentation says values shown as `~` or `-` in the report "will be zeros in the downloaded data".
So a zero from a CSV cannot be told apart from a real zero, and a zero that means "we do not know"
must never rank a finding as unimportant. Every CSV row is `unknown`.
"""
from __future__ import annotations

import pytest

from scripts.traffic_import import aggregate, diagnose, parse_csv, url_key


# --- the match key ---------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    # Google reports the slashed form the sites actually serve; we store it stripped.
    ("https://www.gratitudelodge.com/drug-info/", "https://www.gratitudelodge.com/drug-info"),
    ("http://x.com/a/", "https://x.com/a"),
    ("https://X.com/a", "https://x.com/a"),          # HOST case is not significant
    ("https://x.com/a?utm_source=g", "https://x.com/a"),
    ("https://x.com/a#top", "https://x.com/a"),
])
def test_forms_of_the_same_page_share_a_key(a, b):
    assert url_key(a) == url_key(b)


def test_path_case_is_preserved_because_paths_are_case_sensitive():
    """Hosts are case-insensitive; paths are not (RFC 3986). Our corpus is 0% mixed-case so this
    changes nothing today, but lowercasing the path would merge two genuinely different pages if
    one ever appeared — and a key that over-merges INVENTS traffic, which is worse than none."""
    assert url_key("https://x.com/A") != url_key("https://x.com/a")


def test_www_is_kept_because_it_distinguishes_brands():
    """GL and RR are 100% www; the other seven are 100% bare. Stripping it would merge two
    different notions of a host, and a key that over-merges invents traffic."""
    assert url_key("https://www.gratitudelodge.com/a") != url_key("https://gratitudelodge.com/a")


def test_the_key_drops_the_scheme_but_never_the_host():
    assert url_key("https://x.com/a") == url_key("http://x.com/a")
    assert url_key("https://x.com/a") != url_key("https://y.com/a")


def test_a_bare_homepage_and_a_slashed_homepage_are_one_page():
    """All nine homepages are stored bare; GSC returns them slashed. Without this the busiest page
    on every site matches nothing."""
    assert url_key("https://x.com") == url_key("https://x.com/")


# --- parsing, and the zero rule ---------------------------------------------------------------

CSV = (
    "Top pages,Clicks,Impressions,CTR,Position\n"
    "https://www.gratitudelodge.com/a/,120,4200,2.86%,8.4\n"
    "https://www.gratitudelodge.com/b/,0,0,0%,0\n"
)


def test_rows_parse_into_numbers(tmp_path):
    f = tmp_path / "gsc.csv"; f.write_text(CSV)
    rows = parse_csv(f)
    assert len(rows) == 2
    assert rows[0]["clicks"] == 120 and rows[0]["impressions"] == 4200
    assert rows[0]["position"] == pytest.approx(8.4)


def test_every_csv_row_is_unknown_never_measured(tmp_path):
    """The whole point. Google writes missing values as zeros, so no CSV number can be trusted as
    a measurement — not even a non-zero one, since the row it sits on may be truncated."""
    f = tmp_path / "gsc.csv"; f.write_text(CSV)
    assert {r["value_state"] for r in parse_csv(f)} == {"unknown"}


def test_a_csv_zero_is_not_reported_as_a_measured_zero(tmp_path):
    f = tmp_path / "gsc.csv"; f.write_text(CSV)
    zero = [r for r in parse_csv(f) if r["clicks"] == 0][0]
    assert zero["value_state"] == "unknown"


def test_an_unrecognised_export_fails_loudly_with_the_headers_it_saw(tmp_path):
    """Google has changed these export headers before and will again. Guessing a column would
    silently rank every finding on the wrong number; refusing names the problem in one line."""
    f = tmp_path / "bad.csv"; f.write_text("Seite,Klicks\nhttps://x.com/a,5\n")
    with pytest.raises(ValueError) as e:
        parse_csv(f)
    assert "Seite" in str(e.value)


def test_percentages_and_thousands_separators_survive(tmp_path):
    f = tmp_path / "gsc.csv"
    f.write_text('Top pages,Clicks,Impressions,CTR,Position\n"https://x.com/a/","1,234","45,678",2.7%,3.1\n')
    r = parse_csv(f)[0]
    assert r["clicks"] == 1234 and r["impressions"] == 45678


# --- the same page listed more than once ------------------------------------------------------

def test_duplicate_rows_sum_visits_and_keep_the_largest_impression_row():
    """Measured on the 2026-09-14 exports: keeping the first row per page dropped Elementor
    jump-link rows and undercounted impressions by 26% on AR. But a page and its own jump links
    appear in the SAME search result, so summing impressions would count one appearance twice.
    Visits are separate clicks and do add up."""
    k = url_key("https://x.com/a/")
    rows = [
        {"url_key": k, "clicks": 10, "impressions": 100, "position": 4.0, "value_state": "unknown"},
        {"url_key": k, "clicks": 1, "impressions": 5000, "position": 9.0, "value_state": "unknown"},
        {"url_key": k, "clicks": 2, "impressions": 50, "position": 3.0, "value_state": "unknown"},
    ]
    [one] = aggregate(rows)
    assert one["clicks"] == 13
    assert one["impressions"] == 5000
    assert one["position"] == 9.0          # the position that belongs to the kept impressions


def test_a_page_listed_once_is_unchanged():
    rows = [{"url_key": "x.com/a", "clicks": 3, "impressions": 30, "position": 2.0},
            {"url_key": "x.com/b", "clicks": None, "impressions": None, "position": None}]
    assert aggregate(rows) == rows


# --- why a match rate is low ------------------------------------------------------------------

def test_a_capped_export_is_not_blamed_on_the_property_type():
    """GL on 2026-09-14: 27% of its 3,595 finding pages matched, but 971 of the 996 pages in the
    export did. The join worked; the export stops at 1,000 rows. "Check the property type" sent the
    reader after a problem that did not exist."""
    said = diagnose(27, export_pages=996, export_matched=971, finding_pages=3595)
    assert "property type" not in said
    assert "LIMITED BY THE EXPORT" in said and "25,000" in said


def test_an_export_about_other_pages_still_points_at_the_property():
    said = diagnose(2, export_pages=1000, export_matched=20, finding_pages=3000)
    assert "property type" in said


def test_a_good_match_rate_says_nothing():
    assert diagnose(79, export_pages=187, export_matched=92, finding_pages=117) == ""
