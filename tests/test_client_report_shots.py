"""The client report's screenshot rendering: it must embed, it must bound itself, and when it
drops something it must say so.

The images are base64 PNGs inline so the report stays a single file that still shows its pictures
after being forwarded — a referenced folder does not survive an email, and email is how these are
actually delivered.
"""
from __future__ import annotations

import base64

import pytest

from scripts.client_report import SHOT_BUDGET_BYTES, _shot_html, render

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 200).decode()


def _run():
    from datetime import datetime, timezone
    return (1, datetime(2026, 8, 31, tzinfo=timezone.utc), 19, False, "The District", "https://x/")


def _finding(shot=None, issue="text is too faint to read"):
    # (check, cls, severity, issue, url, snippet, suggestion, pages, first_seen, shot)
    return ("contrast", "color-contrast", "error", issue, "https://x/p/",
            "<h3>x</h3>", "darken the colour", 1, None, shot)


def test_a_finding_with_a_shot_embeds_the_image():
    out = render("TDRC", _run(), [_finding(shot=PNG)])
    assert "<img alt='the element this finding is about'" in out
    assert PNG[:40] in out


def test_a_finding_without_a_shot_renders_normally():
    out = render("TDRC", _run(), [_finding()])
    assert "<img" not in out
    assert "too faint" in out


def test_the_budget_is_generous_enough_for_real_reports():
    """Measured: ~20 KB a shot, a handful per brand. The cap exists for a pathological run, not to
    ration normal use — if it ever binds in practice, that is the signal to look, not to raise it."""
    assert SHOT_BUDGET_BYTES >= 1_000_000


def test_an_oversized_shot_is_dropped_rather_than_breaking_the_report():
    budget, omitted = [100], [0]
    assert _shot_html(PNG, budget, omitted) == ""
    assert omitted[0] == 1
    assert budget[0] == 100, "a dropped image must not be charged to the budget"


def test_each_embedded_shot_is_charged_to_the_budget():
    budget, omitted = [SHOT_BUDGET_BYTES], [0]
    _shot_html(PNG, budget, omitted)
    assert budget[0] == SHOT_BUDGET_BYTES - len(PNG)
    assert omitted[0] == 0


def test_dropping_images_is_announced_to_the_reader():
    """A report quietly missing its pictures reads as a report about findings that have none."""
    import scripts.client_report as cr

    original = cr.SHOT_BUDGET_BYTES
    cr.SHOT_BUDGET_BYTES = 10          # force the cap
    try:
        out = cr.render("TDRC", _run(), [_finding(shot=PNG)])
    finally:
        cr.SHOT_BUDGET_BYTES = original
    assert "screenshot(s) were left out" in out
    assert "<img" not in out
    assert "too faint" in out, "the finding itself must survive; only the picture is dropped"


def test_the_image_cannot_force_horizontal_scrolling():
    """The shots are 2x crops of a 390px viewport. A fixed width would break the report on a phone."""
    out = render("TDRC", _run(), [_finding(shot=PNG)])
    assert "max-width:100%" in out


# --- the accessibility pass is separate from the crawl, and that drifts ---------------------------
# It attaches findings to whichever run was latest WHEN IT RAN. The next crawl creates a new run, the
# report reads that one, and the accessibility findings simply are not there. Silence then reads as
# "nothing wrong" when the truth is "nobody looked" — the same distinction the contrast coverage
# caveat exists to protect, and the reason a stale-vs-missing note is not optional.

def _acc_finding():
    return ("accessibility", "link-name", "error", "a link has no readable name (/)",
            "https://x/p/", "<a></a>", "give the link text", 7, None, None)


def test_a_report_whose_run_has_no_accessibility_findings_says_so():
    from scripts.client_report import render

    out = render("TDRC", _run(), [_finding()])
    assert "accessibility checks were not run" in out.lower()


def test_a_report_that_has_accessibility_findings_does_not_claim_they_are_missing():
    from scripts.client_report import render

    out = render("TDRC", _run(), [_finding(), _acc_finding()])
    assert "accessibility checks were not run" not in out.lower()


def test_the_missing_notice_sits_with_the_other_limits_not_in_the_findings():
    """It is a statement about coverage, not a defect on the site. Putting it in the findings list
    would make it look like something to fix."""
    from scripts.client_report import render

    out = render("TDRC", _run(), [_finding()])
    limits = out.split("What this audit cannot see")[1]
    assert "accessibility checks were not run" in limits.lower()
