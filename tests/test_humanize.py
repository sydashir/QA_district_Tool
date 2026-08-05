"""The sheet must be readable by whoever does the fixing, not by us.

25% of findings shipped with an EMPTY `suggestion` — the column that says what to DO — and the
`check` column showed our module names. "missing <h1>" is as useless to a QA reader as a blank cell:
same failure, different column.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from auditor.humanize import CHECK_LABELS, check_label, plain_issue, suggestion_for

# every (check, issue) pair the network run actually produced, worst-first
REAL = [
    ("heading_structure", "skipped level H1->H3"),
    ("meta", "title length out of bounds"),
    ("meta", "missing meta description"),
    ("enumeration", "indexable page missing from sitemap"),
    ("heading_structure", "multiple <h1>"),
    ("heading_structure", "empty heading"),
    ("blank", "missing <h1>"),
    ("phone", "unknown phone number (not in NAP)"),
    ("heading_structure", "duplicate H1 across pages"),
    ("meta", "duplicate title across pages"),
    ("meta", "duplicate meta description across pages"),
    ("placeholder", "unresolved [acf field] token in visible text"),
    ("phone", "malformed tel: number"),
    ("broken_links", "external link unverified — host returned HTTP 403 (bot-block)"),
    ("enumeration", "sitemap page returns HTTP 404"),
    ("scope", '"county" where "country" is meant (national page)'),
]


@pytest.mark.parametrize("check,issue", REAL)
def test_every_real_finding_gets_a_suggestion(check, issue):
    got = suggestion_for(check, issue, "")
    assert got.strip(), f"{check}/{issue} still has no suggestion"
    assert len(got) > 40, f"{check}/{issue} suggestion is too thin to act on: {got!r}"


@pytest.mark.parametrize("check,issue", REAL)
def test_no_reader_facing_string_contains_our_jargon(check, issue):
    text = f"{check_label(check)} {plain_issue(check, issue)}"
    for jargon in ("<h1>", "acf field", "tel:", "href", "wp-rest", "noindex"):
        assert jargon.lower() not in text.lower(), (
            f"{check}/{issue} still shows '{jargon}' to the reader: {text!r}")


def test_a_checks_own_suggestion_is_never_overwritten():
    mine = "Replace 800-692-9850 with 844-576-0144 across the footer template."
    assert suggestion_for("phone", "non-canonical phone number", mine) == mine


def test_unknown_check_still_gets_something_actionable():
    assert suggestion_for("brand_new_check", "something odd", "").strip()
    assert plain_issue("brand_new_check", "something odd") == "something odd"


def test_every_check_module_has_a_human_label():
    """Compare the CHECK constants the modules actually emit, not their filenames — links.py emits
    'broken_links' and structure.py emits 'heading_structure', so filenames would pass a test the
    sheet fails."""
    import importlib
    names = set()
    for f in sorted((pathlib.Path("auditor") / "checks").glob("*.py")):
        if f.stem == "__init__":
            continue
        mod = importlib.import_module(f"auditor.checks.{f.stem}")
        if hasattr(mod, "CHECK"):
            names.add(mod.CHECK)
    missing = names - set(CHECK_LABELS)
    assert not missing, f"checks with no human label: {sorted(missing)}"
    assert names, "found no CHECK constants at all — the test is not testing anything"
