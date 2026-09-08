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


# -------------------------------------------------- Section D must track the check registry
# The whole point of deriving Section D is that a check which EXISTS can never be listed as
# not-done. That guarantee is only as good as `_source_classes()`, and it had a hole: it matched
# `"class": "literal"` but not `details={"class": rule}`, where a11y.py assigns the axe rule id from
# a variable. So `target-size` — a real, shipped check — scanned as non-existent, and the tap-target
# topic was keyed on `target_size_enhanced`, the separate 44px AAA advisory. Had the render pass
# ever published, the report body would have listed tap-target findings while Section D denied them.

# Every class the RENDER layer can emit must be accounted for by Section D — either it covers a
# topic, or it is explicitly declared as not being a Section D subject. Anything else is a hole:
# a shipped check that Section D goes on denying. This is the invariant the first version of this
# test missed — it only checked that named classes EXIST, which `target_size_enhanced` did, so the
# real bug (the topic naming the advisory instead of the shipped rule) sailed straight through.
NOT_A_LIMITS_TOPIC = {
    # Not a client-facing "we cannot see this" subject: it is a coverage caveat about the contrast
    # pass itself, rendered inside the contrast section rather than as its own limitation.
    "contrast_coverage",
}


def test_every_class_the_render_layer_can_emit_is_accounted_for_in_section_d():
    """A render class named by no topic is a check Section D will keep denying after it ships."""
    import re
    from pathlib import Path

    emitted = set()
    for f in (Path(__file__).resolve().parent.parent / "render").glob("*.py"):
        t = f.read_text(encoding="utf-8")
        emitted |= set(re.findall(r'"class":\s*"([a-z0-9_-]+)"', t))
        for group in re.findall(r'DEFAULT_RULES[^=]*=\s*\(([^)]*)\)', t):
            emitted |= set(re.findall(r'"([a-z0-9-]+)"', group))

    named = {c for _t, classes, _w, _n in cr.LIMIT_TOPICS for c in classes}
    unaccounted = sorted(emitted - named - NOT_A_LIMITS_TOPIC)
    assert not unaccounted, (
        f"the render layer can emit {unaccounted} and no Section D topic names them — "
        f"the report would deny a check it ships")


def test_the_registry_scan_sees_classes_assigned_from_a_variable():
    """`render/a11y.py` does `details={"class": rule}` — the literal never appears next to "class"."""
    src = cr._source_classes()
    assert "target-size" in src, "axe rule ids declared in DEFAULT_RULES are invisible to the scan"
    assert "color-contrast" in src


def test_a_topic_clears_when_its_class_appears_in_the_run():
    """The mechanism itself: a covering class present means the topic is dropped, not softened."""
    covered = [row("phone", "color-contrast", "error", 3)]
    assert "Colour and contrast" not in cr.limits_html(covered)
    assert "Colour and contrast" in cr.limits_html([row("phone", "stale_retired", "error", 3)])


def test_an_unpublished_but_built_check_says_so_rather_than_denying_itself():
    """The honest middle state: built, measured, not switched on. Neither 'we check it' nor 'we
    do not' would have been true."""
    html_out = cr.limits_html([row("phone", "stale_retired", "error", 3)])
    assert "Colour and contrast" in html_out
    assert "built" in html_out, "a built-but-unpublished check must not read as absent"
