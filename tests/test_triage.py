"""Triage preservation — the client's own edits must survive a rewrite.

The `<BRAND> — open` tab is rewritten every run. It also carries two HUMAN columns (`status`,
`note`). So every run must READ what the client wrote, then re-attach it by fingerprint. Getting
this wrong does not lose a night of updates, it silently wipes a month of the client's triage — the
same trust failure as a partial sitemap producing confident coverage claims, in a different costume.
"""
from __future__ import annotations

import pytest

from auditor.report import Finding, Severity
from auditor.triage import (TriageRow, merge_triage, parse_triage_columns, sort_key,
                            untriaged_error_summary)

HEADER = ["url", "check", "severity", "issue", "location", "snippet", "suggestion",
          "fingerprint", "first_seen", "age_days", "status", "note"]


def _f(fp, url="https://x/a/", check="phone", sev=Severity.ERROR):
    return Finding(url=url, check=check, severity=sev, fingerprint=fp,
                   issue="i", location="l", snippet="s", suggestion="sug", details={})


def _row(fp, status="", note=""):
    r = [""] * len(HEADER)
    r[HEADER.index("fingerprint")] = fp
    r[HEADER.index("status")] = status
    r[HEADER.index("note")] = note
    return r


# --- reading what the client wrote ---

def test_reads_status_and_note_by_fingerprint():
    got = parse_triage_columns(HEADER, [_row("fp1", "wontfix", "by design")])
    assert got == {"fp1": TriageRow("fp1", "wontfix", "by design")}


def test_an_unknown_status_is_preserved_verbatim_not_dropped():
    # someone types "wontfx". Dropping it loses their intent; "correcting" it invents one.
    got = parse_triage_columns(HEADER, [_row("fp1", "wontfx", "typo here")])
    assert got["fp1"].status == "wontfx"


def test_a_row_with_no_fingerprint_is_skipped_and_donates_to_nobody():
    rows = [_row("", "wontfix", "orphan"), _row("fp2", "", "")]
    got = parse_triage_columns(HEADER, rows)
    assert "" not in got and got["fp2"].status == ""
    merged = merge_triage([_f("fp2")], got, changed_checks=set(), today="2026-08-04")
    assert merged[0].status == "", "an orphaned row must not lend its status to another finding"


def test_duplicate_fingerprints_keep_the_first_and_do_not_crash():
    got = parse_triage_columns(HEADER, [_row("fp1", "wontfix", "first"), _row("fp1", "", "second")])
    assert got["fp1"].status == "wontfix"


def test_missing_status_column_entirely_is_tolerated():
    # the client may have deleted or reordered columns
    hdr = ["url", "fingerprint"]
    got = parse_triage_columns(hdr, [["https://x/a/", "fp1"]])
    assert got["fp1"] == TriageRow("fp1", "", "")


def test_short_rows_do_not_raise():
    # Sheets omits trailing empty cells, so a row can be shorter than the header
    got = parse_triage_columns(HEADER, [["https://x/a/", "phone"]])
    assert got == {}


# --- re-attaching it ---

def test_status_and_note_survive_a_rewrite():
    existing = {"fp1": TriageRow("fp1", "acknowledged", "waiting on dev")}
    out = merge_triage([_f("fp1")], existing, changed_checks=set(), today="2026-08-04")
    assert out[0].status == "acknowledged" and out[0].note == "waiting on dev"


def test_a_finding_with_no_prior_triage_is_blank():
    out = merge_triage([_f("fp9")], {}, changed_checks=set(), today="2026-08-04")
    assert out[0].status == "" and out[0].note == ""


def test_rule_change_resets_triage_AND_SAYS_SO_IN_THE_NOTE():
    # The fingerprint is scoped to check-version, so changing a rule re-keys the finding and the
    # prior judgement no longer applies — correct behaviour. But the client must not watch their
    # "wontfix" vanish with no explanation and conclude the tool ate it.
    existing = {"old-fp": TriageRow("old-fp", "wontfix", "acceptable for PPC")}
    out = merge_triage([_f("new-fp")], existing, changed_checks={"phone"}, today="2026-08-04")
    assert out[0].status == "", "a rule change must reset the judgement, not carry it forward"
    assert "triage reset" in out[0].note and "rule changed" in out[0].note
    assert "2026-08-04" in out[0].note
    assert "wontfix" in out[0].note and "acceptable for PPC" in out[0].note


def test_rule_change_only_reattaches_within_the_same_url_and_check():
    existing = {"old-fp": TriageRow("old-fp", "wontfix", "n")}
    # same check changed, but the orphan came from a different page
    out = merge_triage([_f("new-fp", url="https://x/OTHER/")],
                       {**existing}, changed_checks={"phone"}, today="2026-08-04",
                       orphan_urls={"old-fp": ("https://x/a/", "phone")})
    assert out[0].note == "", "an orphan from another page must not explain this finding's reset"


def test_an_untouched_check_does_not_get_a_reset_note():
    existing = {"old-fp": TriageRow("old-fp", "wontfix", "n")}
    out = merge_triage([_f("new-fp")], existing, changed_checks=set(), today="2026-08-04")
    assert out[0].note == ""


# --- ordering: the tab must not look identical every day ---

def test_untriaged_errors_sort_first_then_oldest():
    rows = [
        ("wontfix", Severity.ERROR, 90),
        ("", Severity.WARNING, 5),
        ("", Severity.ERROR, 3),
        ("", Severity.ERROR, 40),
        ("acknowledged", Severity.ERROR, 60),
    ]
    ordered = sorted(rows, key=lambda r: sort_key(r[0], r[1], r[2]))
    assert ordered[0] == ("", Severity.ERROR, 40), "oldest untriaged ERROR must be at the top"
    assert ordered[1] == ("", Severity.ERROR, 3)
    assert ordered[2] == ("", Severity.WARNING, 5)
    assert ordered[-1][0] == "wontfix", "deliberate wontfix sorts last"


def test_an_unknown_status_sorts_as_untriaged():
    assert sort_key("wontfx", Severity.ERROR, 10) == sort_key("", Severity.ERROR, 10)


# --- the digest line that makes non-movement visible ---

def test_digest_counts_untriaged_errors_and_the_oldest():
    out = merge_triage([_f("a"), _f("b"), _f("c", sev=Severity.WARNING)],
                       {"a": TriageRow("a", "wontfix", "")}, changed_checks=set(),
                       today="2026-08-04")
    for r, age in zip(out, (10, 45, 99)):
        r.age_days = age
    n, oldest = untriaged_error_summary(out)
    assert n == 1 and oldest == 45, "wontfix is excluded; WARNINGs are not counted as errors"


def test_digest_on_a_clean_board():
    assert untriaged_error_summary([]) == (0, 0)
