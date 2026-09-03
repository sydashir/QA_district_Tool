"""What the client report chooses to show, and why the shot pass follows it.

Written after the whole picture feature rendered ZERO images: the shot pass photographed anything
carrying a selector, the report showed the top rows per section, and on GL those two populations
did not intersect at all — 61 pictures taken, 0 displayed. The report's choice is authoritative;
these tests pin that and the ordering rules that make it fair.
"""
from __future__ import annotations

import importlib.util as _ilu
import pathlib

import pytest

_spec = _ilu.spec_from_file_location(
    "cr", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "client_report.py")
cr = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(cr)


def row(check, cls, sev, pages, issue="i", fp=None):
    # (check, cls, severity, issue, url, snippet, suggestion, pages, first_seen, shot, absent, fp)
    return (check, cls, sev, issue, "https://x.invalid/p", None, None, pages,
            None, None, None, fp or f"{check}:{cls}:{issue}:{pages}")


def test_every_class_gets_a_slot_before_any_class_gets_a_second():
    """A dead 'Verify Insurance' on one page matters more to a reader than a footer link on 3,000.
    Sorting purely by page_count let one class take every slot: on GL `actions:dead_cta` had 41
    findings and 25 photographs and appeared zero times."""
    rows = ([row("broken_links", "broken", "error", 3000 - i, f"b{i}") for i in range(8)]
            + [row("actions", "dead_cta", "error", 1, "cta")])
    shown = cr.select_shown(rows, 8)
    assert any(f[1] == "dead_cta" for f in shown), "the one-page class was shut out again"


def test_severity_is_never_inverted_to_make_room():
    """The first version of select_shown dealt round-robin across all classes at once and put an
    ERROR below two warnings on GL. A harm-ordered report cannot do that."""
    rows = ([row("phone", "stale_retired", "error", 3000, "e1"),
             row("phone", "stale_retired", "error", 7, "e2"),
             row("phone", "dials_retired", "error", 2, "e3")]
            + [row("phone", "display_dial_mismatch", "warning", 60 - i, f"w{i}") for i in range(5)]
            + [row("brands", "sister_brand", "warning", 1, "w9")])
    shown = cr.select_shown(rows, 8)
    rank = {"error": 0, "warning": 1, "info": 2}
    seq = [rank[f[2]] for f in shown]
    assert seq == sorted(seq), f"severity inverted: {[f[2] for f in shown]}"
    assert all(f[2] == "error" for f in shown[:3]), "all three errors must come first"


def test_a_short_list_is_returned_whole():
    rows = [row("phone", "stale_retired", "error", 5)]
    assert cr.select_shown(rows, 8) == rows


def test_the_flagship_phone_defect_has_a_section():
    """`display_dial_mismatch` is the COC bug that opened the ticket — a page printing one number
    and dialling another. Until 2026-09-03 it reached the sheet and the database but no section of
    the client report, so the client never saw it."""
    assert cr._matches("phone", "display_dial_mismatch",
                       [k for _h, _w, ks in cr.SECTIONS for k in ks])


def test_the_page_count_is_printed_once_not_twice():
    """The checks append their own count to the issue text because the spreadsheet has no column
    for it; the report has one, and printing both gave
    `button goes nowhere: "View All" — on 1343 pages on 1,343 pages`."""
    assert cr._clean('button goes nowhere: "View All" — on 1343 pages') == \
        'button goes nowhere: "View All"'
    assert cr._clean("the same paragraph appears 2 times on this page — on 566 pages") == \
        "the same paragraph appears 2 times on this page"
    # ...and text that merely mentions pages is untouched.
    assert cr._clean("a link has no readable name (/profile/N)") == \
        "a link has no readable name (/profile/N)"


@pytest.mark.parametrize("prev_pages,cur_pages,flagged", [
    (77, 7967, True),      # RR: a refused run's 77-page baseline produced "11,259 new" vs 128 real
    (886, 1458, True),     # COC: a SUCCESSFUL but short run, 1,334 reported vs 52 real
    (3346, 3353, False),   # GL: a normal previous run
    (1505, 1458, False),   # a slightly larger baseline is fine
])
def test_a_baseline_that_saw_far_less_is_declared(prev_pages, cur_pages, flagged):
    """The rule is about PAGES, not run status — COC's poisoning came from an `ok` run."""
    assert (prev_pages < cur_pages * cr.BASELINE_FLOOR) is flagged
