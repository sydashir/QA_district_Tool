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

from scripts.traffic_import import parse_csv, url_key


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
