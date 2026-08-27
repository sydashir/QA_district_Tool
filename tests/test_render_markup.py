"""Markup-only accessibility rules over already-fetched HTML.

This pass is deliberately NOT rendering: the HTML was fetched by httpx (no JavaScript), and it is
analysed in a local browser with every request blocked, so no client server sees a browser and no
tracker can fire. These tests hold that line, and hold the write-time collapse that makes the
output readable.
"""
from __future__ import annotations

import pytest

from auditor.report import Severity
from render.markup import NEEDS_RENDERING, _shape, to_findings
from render.safety import SafetyNotArmed


# --------------------------------------------------------------------------- what is in scope
@pytest.mark.parametrize("rule", ["color-contrast", "target-size"])
def test_rules_that_need_a_rendered_page_are_out_of_scope(rule):
    """Without stylesheets a contrast answer is fiction and a tap-target box has no size. These
    stay behind the rendering hold, where they belong."""
    assert rule in NEEDS_RENDERING


# --------------------------------------------------------------------------- the collapse
def _v(rule, html, help_="Links must have discernible text"):
    return {"rule": rule, "help": help_, "html": html}


def test_the_same_broken_link_across_many_pages_is_one_row():
    """Measured on live pages: link-name alone produced 2,981 raw violations, ~11 on every page of
    all nine brands, because the offenders are template furniture. Raw, it buries everything else."""
    raw = [(f"https://x/page-{i}/", _v("link-name", '<a href="https://x.com"></a>'))
           for i in range(40)]
    out = to_findings(raw, "https://x.com")
    assert len(out) == 1
    assert out[0].details["page_count"] == 40
    assert out[0].details["template_collapsed"] is True
    assert "40 page(s)" in out[0].suggestion


def test_different_targets_stay_separate():
    raw = [("https://x/a/", _v("link-name", '<a href="https://x.com"></a>')),
           ("https://x/b/", _v("link-name", '<a href="tel:555-1234"></a>'))]
    assert len(to_findings(raw, "https://x.com")) == 2


def test_collapsing_keeps_a_bounded_list_of_example_pages():
    raw = [(f"https://x/p{i}/", _v("link-name", '<a href="/x"></a>')) for i in range(50)]
    out = to_findings(raw, "https://x.com")[0]
    assert out.details["page_count"] == 50
    assert len(out.details["sources"]) == 8, "the finding must not carry 50 urls"


@pytest.mark.parametrize("html,expected", [
    ('<a href="https://www.gratitudelodge.com/authors/x/"></a>', "/authors/x/"),
    ('<a href="/check-my-insurance/aetna/"></a>', "/check-my-insurance/aetna/"),
    # digits are generalised, so /page-1/ and /page-2/ are one template defect, not two
    ('<a href="/blog/post-12345/"></a>', "/blog/N/"),
    ('<div role="button"></div>', "<div>"),
])
def test_shape_is_what_the_element_points_at(html, expected):
    assert _shape(html) == expected


# --------------------------------------------------------------------------- severity + wording
def test_an_unlabelled_click_to_call_is_named_for_what_it_is():
    """The accessibility twin of a dead CTA: on a healthcare site, a screen-reader user hears a
    link with no name and cannot tell it dials the clinic. Live on RR: <a href="tel:629-299-2329">."""
    out = to_findings([("https://x/p/", _v("link-name", '<a href="tel:629-299-2329"></a>'))],
                      "https://x.com")[0]
    assert out.severity is Severity.ERROR
    assert "click-to-call" in out.suggestion
    assert "screen reader" in out.suggestion


def test_an_unlabelled_link_is_an_error_and_a_list_role_problem_is_a_warning():
    rows = to_findings([("https://x/p/", _v("link-name", '<a href="/a"></a>')),
                        ("https://x/p/", _v("aria-required-children", '<div role="list"></div>'))],
                       "https://x.com")
    by = {f.details["class"]: f.severity for f in rows}
    assert by["link-name"] is Severity.ERROR
    assert by["aria-required-children"] is Severity.WARNING


def test_findings_are_written_for_whoever_reads_the_sheet():
    out = to_findings([("https://x/p/", _v("label", '<input name="city">',
                                           "Form elements must have labels"))], "https://x.com")[0]
    assert "a form field has no label" in out.issue


# --------------------------------------------------------------------------- the network guard
def test_the_pass_refuses_to_run_if_the_guard_is_not_attached(monkeypatch):
    """A markup pass that silently began fetching is exactly what we told the client we do not do.
    Proven with a canary rather than by counting real traffic: DBH's headless rebuild references no
    external assets at all, so it legitimately blocks zero and a count-based check false-alarms."""
    import render.markup as m

    class _Page:
        def route(self, *a, **k): pass            # accepts the handler, never calls it
        def set_content(self, *a, **k): pass
        def wait_for_timeout(self, *a, **k): pass
        def evaluate(self, *a, **k): return []
    class _Browser:
        def new_page(self): return _Page()
        def close(self): pass
    class _PW:
        chromium = type("C", (), {"launch": staticmethod(lambda *a, **k: _Browser())})()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(m, "sync_playwright", lambda: _PW(), raising=False)
    monkeypatch.setitem(__import__("sys").modules, "playwright.sync_api",
                        type("M", (), {"sync_playwright": lambda: _PW()}))
    with pytest.raises(SafetyNotArmed, match="canary"):
        m.audit_html([("https://x/p/", "<html></html>")], "https://x.com")
