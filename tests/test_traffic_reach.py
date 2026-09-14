"""Ranking findings by search traffic — the judgements that would mislead a reader if they slipped.

Design: docs/plans/2026-08-31-traffic-weighting-design.md. Every test here pins one way the obvious
implementation is wrong on real data: summing a group's reach, printing a sample as a measurement,
letting traffic outrank harm, calling a Google-invented zero a zero, and calling a page that cannot
be in search "unmatched".
"""
from __future__ import annotations

from datetime import datetime

from scripts import client_report as cr
from server.traffic import (NOT_CONNECTED, NO_SEARCH_BY_DESIGN, BrandTraffic, affected, reach,
                            ranks_on_impressions, sentence, url_key)

PERIOD = "2026-06-13..2026-09-12"
HOST = "https://www.gratitudelodge.com"


def row(check, cls, sev, pages, url, issue="i", snippet=None, sources=None, fp=None):
    # (check, cls, severity, issue, url, snippet, suggestion, pages, first_seen, shot, absent, fp,
    #  sources) — the shape `client_report.fetch` returns.
    return (check, cls, sev, issue, url, snippet, None, pages, None, None, None,
            fp or f"{check}:{cls}:{issue}:{url}", sources)


def traffic(**pages) -> BrandTraffic:
    """pages: path -> (clicks, impressions)."""
    return BrandTraffic(period=PERIOD, measured=False,
                        pages={url_key(f"{HOST}/{p}"): v for p, v in pages.items()})


def order(findings, keys, t):
    merged, _shown = cr.section_rows(findings, keys, top_n=50, traffic=t)
    return [f[3] for f in merged]


# --------------------------------------------------------------------------- union, not sum
def test_a_group_on_one_page_counts_that_page_once():
    """GL had a `dead_cta` group of 13 findings on ONE url. It printed "on 13 pages", and weighted by
    traffic a sum would have counted that page's visits thirteen times."""
    rows = [row("actions", "dead_cta", "error", 1, f"{HOST}/insurance", issue="dead button",
                fp=f"cta#{i}") for i in range(13)]
    merged, _ = cr.section_rows(rows, ["actions:*"], traffic=traffic(insurance=(100, 900)))
    assert len(merged) == 1
    assert merged[0][7] == 1                              # one page, not 13
    assert merged[0][13].clicks == 100                    # its visits once, not 1,300


def test_a_group_across_pages_counts_each_page():
    rows = [row("phone", "cross_brand_dial", "error", 1, f"{HOST}/{p}", issue="dials RR")
            for p in ("a", "b", "c")]
    merged, _ = cr.section_rows(rows, ["phone:*"], traffic=traffic(a=(10, 0), b=(5, 0)))
    assert merged[0][7] == 3
    assert merged[0][13].clicks == 15 and merged[0][13].matched == 2


# --------------------------------------------------------------------------- partial is partial
def test_a_sampled_page_list_is_never_presented_as_the_reach():
    sample = [f"{HOST}/p{i}" for i in range(8)]
    r = row("empty_row", "empty_row", "warning", 1343, sample[0], sources=sample)
    merged, _ = cr.section_rows([r], ["empty_row:*"], traffic=traffic(p0=(40, 400), p3=(2, 20)))
    text = sentence(merged[0][13])
    assert merged[0][7] == 1343
    assert "at least 42 visits" in text
    assert "8 of 1,343" in text


def test_a_complete_page_list_is_not_hedged():
    keys, complete = affected(f"{HOST}/a", [f"{HOST}/a", f"{HOST}/b"], 2)
    assert complete
    text = sentence(reach(keys, 2, traffic(a=(7, 70)), "phone", "x"))
    assert "at least" not in text and "of 2" not in text


# --------------------------------------------------------------------------- harm first
# A real section class, so `render` and `selected_fingerprints` place them in a section at all.
DIAL = "cross_brand_dial"
QUIET_ERROR = row("phone", DIAL, "error", 1, f"{HOST}/quiet", issue="quiet error")
BUSY_WARNING = row("phone", DIAL, "warning", 1, f"{HOST}/busiest", issue="busy warning")
BUSY_ERROR = row("phone", DIAL, "error", 1, f"{HOST}/busy", issue="busy error")
WIDE_ERROR = row("phone", DIAL, "error", 900, f"{HOST}/wide", issue="wide error")
FOUR = [QUIET_ERROR, BUSY_WARNING, BUSY_ERROR, WIDE_ERROR]
TRAFFIC = traffic(busiest=(50_000, 90_000), busy=(900, 2_000))


def test_traffic_orders_within_a_severity_never_across_it():
    """A button dialling a competitor on a quiet page still outranks a faint colour on a busy one."""
    assert order(FOUR, ["phone:*"], TRAFFIC) == [
        "busy error",      # error, and the only error with traffic
        "wide error",      # error, unweighted: falls back to page count
        "quiet error",
        "busy warning",    # 50,000 visits, and still below every error
    ]


def test_without_traffic_data_the_order_is_exactly_what_it_was():
    # Severity, then page count; the two single-page errors tie and are settled by fingerprint
    # ("busy…" before "quiet…"), never by the order they arrived in.
    assert order(FOUR, ["phone:*"], None) == ["wide error", "busy error", "quiet error",
                                              "busy warning"]


def test_the_search_section_ranks_on_impressions():
    """A bad title does its damage in Google's results, before anyone clicks — so a page shown
    90,000 times outranks one clicked 500 times. A phone fault hurts someone ON the page: visits."""
    t = traffic(clicked=(500, 1_000), shown=(5, 90_000))
    meta = [row("meta", "x", "warning", 1, f"{HOST}/clicked", issue="clicked"),
            row("meta", "x", "warning", 1, f"{HOST}/shown", issue="shown")]
    phone = [row("phone", "x", "warning", 1, f"{HOST}/clicked", issue="clicked"),
             row("phone", "x", "warning", 1, f"{HOST}/shown", issue="shown")]
    assert order(meta, ["meta:*"], t) == ["shown", "clicked"]
    assert order(phone, ["phone:*"], t) == ["clicked", "shown"]


def test_the_search_section_ranks_on_impressions_and_no_other_does():
    """server/traffic.py decides the metric per finding; the report groups by section. If the two
    drift, part of a section would rank on one number and part on the other."""
    search = "Search engines are being given the wrong information"
    for heading, _why, keys in cr.SECTIONS:
        for k in keys:
            check, _, cls = k.partition(":")
            cls = "anything" if cls == "*" else cls
            assert ranks_on_impressions(check, cls) == (heading == search), (heading, k)


# --------------------------------------------------------------------------- the no-data states
def test_a_page_that_cannot_be_in_search_is_explained_not_called_unmatched():
    """915 current findings are no-indexed pages. They have no impressions because they are
    no-indexed; counting them as match failures would bury the real gaps. And the reason is PRINTED,
    so nobody later wonders why they are never weighted and adds them back."""
    t = traffic(hidden=(40, 400))                        # even when Google has a row for it
    r = row("enumeration", "noindex_unsitemapped", "warning", 1, f"{HOST}/hidden")
    merged, _ = cr.section_rows([r], ["enumeration:*"], traffic=t)
    assert merged[0][13].state == "by_design"
    assert merged[0][13].rank(False) == (1, 0, 0)          # not weighted
    assert sentence(merged[0][13]) == NO_SEARCH_BY_DESIGN["noindex_unsitemapped"]


def test_a_csv_zero_is_not_reported_as_nobody_came():
    """Google writes missing figures as zeros in the CSV download."""
    r = reach({url_key(f"{HOST}/z")}, 1, traffic(z=(0, 0)), "phone", "x")
    assert r.state == "zero"
    assert r.rank(False) == (1, 0, 0)
    assert "not proof" in sentence(r)
    assert "had no search impressions in the period" not in sentence(r)


def test_each_no_data_state_says_something_different():
    t = traffic(z=(0, 0))
    unmatched = reach({url_key(f"{HOST}/elsewhere")}, 1, t, "phone", "x")
    zero = reach({url_key(f"{HOST}/z")}, 1, t, "phone", "x")
    by_design = reach({url_key(f"{HOST}/z")}, 1, t, "enumeration", "sitemap_dead")
    not_connected = reach({url_key(f"{HOST}/z")}, 1, None, "phone", "x")
    said = [sentence(unmatched), sentence(zero), sentence(by_design)]
    assert len(set(said)) == 3 and all(said)
    assert "gap in our matching" in said[0]
    # Not connected is said once for the whole report, not under every finding.
    assert sentence(not_connected) == ""


# --------------------------------------------------------------------------- the report
RUN = (1, datetime(2026, 9, 4), 100, False, "Gratitude Lodge", HOST)


def test_a_report_with_no_traffic_data_says_it_does_not_mean_quiet():
    out = cr.render("GL", RUN, [QUIET_ERROR], None, None)
    assert NOT_CONNECTED.split(". ")[-1].rstrip(".") in out          # "It does not mean these pages are quiet"


def test_a_report_with_traffic_states_how_much_of_it_matched():
    out = cr.render("GL", RUN, FOUR, None, TRAFFIC)
    assert "2 of the 4 findings here (50%)" in out
    assert "13 Jun – 12 Sep 2026" in out
    assert "900 visits from Google" in out


def test_the_shot_pass_photographs_the_findings_the_traffic_order_shows(monkeypatch):
    """`selected_fingerprints` decides what gets photographed. If it ignored traffic, the pictures
    would go to the findings the report used to show, not the ones it shows now."""
    rows = [row("phone", DIAL, "error", 900 - i, f"{HOST}/wide{i}", issue=f"wide {i}")
            for i in range(8)] + [BUSY_ERROR]
    monkeypatch.setattr(cr, "fetch", lambda s, code: (RUN, rows))
    monkeypatch.setattr(cr, "load_brand_traffic", lambda s, code: TRAFFIC)
    fps = cr.selected_fingerprints(None, "GL")
    assert BUSY_ERROR[11] in fps
    assert fps.index(BUSY_ERROR[11]) == 0


# --------------------------------------------------------------------------- how far the order reaches
def test_a_capped_export_says_the_ranking_only_reaches_the_busiest_pages():
    """RR leaves 81% of its findings unweighted and GL 74%, both on exports cut off at 1,000 rows.
    A ranking that reaches a fifth of the findings orders the head of the site and nothing else."""
    capped = BrandTraffic(period=PERIOD, pages=TRAFFIC.pages, measured=False, capped=True)
    quiet = [row("phone", DIAL, "error", 1, f"{HOST}/q{i}", issue=f"q{i}") for i in range(4)]
    out = cr.render("GL", RUN, FOUR + quiet, None, capped)
    assert "only works for the busiest part of the site" in out
    assert "only 2 of the 8 findings here (25%)" in out


def test_a_complete_export_is_never_blamed_on_the_cap():
    """AR exported 518 rows — everything Google had — and still leaves 62% unweighted. Saying the
    1,000-row cap caused that would be false in AR's own report."""
    complete = BrandTraffic(period=PERIOD, pages=TRAFFIC.pages, measured=False, capped=False)
    out = cr.render("GL", RUN, FOUR, None, complete)
    assert "1,000 pages" not in out
    assert "export for this site was complete" in out
    nowhere = {url_key(f"{HOST}/nowhere")}
    assert "most likely had no search impressions" in sentence(reach(nowhere, 1, complete, "phone", "x"))
    capped = BrandTraffic(period=PERIOD, pages=TRAFFIC.pages, measured=False, capped=True)
    assert "1,000 pages with the most clicks" in sentence(reach(nowhere, 1, capped, "phone", "x"))


def test_the_report_states_its_picture_count():
    """428 findings shown, 3 pictures, and nothing on the page said so."""
    shot = "data:image/png;base64,iVBORw0KGgo="
    pictured = BUSY_ERROR[:9] + (shot,) + BUSY_ERROR[10:]
    out = cr.render("GL", RUN, [pictured, QUIET_ERROR, WIDE_ERROR], None, None)
    assert "1 of the 3 findings shown in this report has a photograph" in out


# --------------------------------------------------------------------------- a stable, single listing
def test_the_selection_does_not_depend_on_the_order_rows_arrive_in():
    """Measured 2026-09-15: the shot pass photographed 12 findings the report selected, then the
    report embedded 8 — because writing the pictures changed the order Postgres returned rows in,
    and ties on (severity, page count) were resolved by that order. What the client sees must not
    change because a picture was saved."""
    import random
    rows = [row("phone", DIAL, "error", 1, f"{HOST}/t{i}", issue=f"tie {i}") for i in range(20)]
    first = [f[11] for f in cr.section_rows(rows, ["phone:*"], top_n=8)[1]]
    for seed in range(5):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        assert [f[11] for f in cr.section_rows(shuffled, ["phone:*"], top_n=8)[1]] == first


def test_a_finding_is_listed_in_one_section_only():
    """`accessibility:link-name` matched both "A visitor cannot get through" and the housekeeping
    wildcard `accessibility:*`, so the same finding was printed and counted twice — 25 duplicate
    slots across the nine reports."""
    link = row("accessibility", "link-name", "error", 1, f"{HOST}/a", issue="a link has no name")
    out = cr.render("GL", RUN, [link], None, None)
    assert out.count("a link has no name") == 1
    fps = []
    import types
    mp = types.SimpleNamespace()
    orig_fetch, orig_load = cr.fetch, cr.load_brand_traffic
    try:
        cr.fetch = lambda s, code: (RUN, [link])
        cr.load_brand_traffic = lambda s, code: None
        fps = cr.selected_fingerprints(None, "GL")
    finally:
        cr.fetch, cr.load_brand_traffic = orig_fetch, orig_load
    assert fps == [link[11]]
