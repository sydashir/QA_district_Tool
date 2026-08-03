"""Sheets publishing — and the one rule that matters more than publishing.

A degraded read must never produce a confident overwrite. The `open` tab carries the client's own
triage, so if we cannot read what is there, we must NOT rewrite it with blanks. Losing a night of
updates is fine; wiping a month of somebody's triage is not. Same principle already applied to a
partial sitemap (withhold coverage findings) and partial CSS (mark findings low-confidence).
"""
from __future__ import annotations

import pytest

from auditor.report import Finding, Severity
from auditor.sheets import TriageReadFailed, publish_open_tab
from auditor.triage import ACKNOWLEDGED

HEADER = ["url", "check", "severity", "issue", "location", "snippet", "suggestion",
          "fingerprint", "first_seen", "age_days", "status", "note"]


def _f(fp, url="https://x/a/", check="phone", sev=Severity.ERROR):
    return Finding(url=url, check=check, severity=sev, fingerprint=fp,
                   issue="i", location="l", snippet="s", suggestion="sug", details={})


class FakeSheets:
    """Minimal stand-in for the Sheets v4 client."""

    def __init__(self, existing=None, read_raises=None, tab_missing=False):
        self.existing = existing            # (header, rows) or None
        self.read_raises = read_raises
        self.tab_missing = tab_missing
        self.written = None                 # (header, rows) actually pushed
        self.reads = 0

    def read_tab(self, tab):
        self.reads += 1
        if self.read_raises:
            raise self.read_raises
        if self.tab_missing:
            return None                     # first run — nothing to lose
        return self.existing

    def replace_tab(self, tab, header, rows):
        self.written = (header, rows)


def _publish(fake, findings, changed=frozenset()):
    return publish_open_tab(fake, "sheet-id", "GL — open", findings,
                            changed_checks=set(changed), today="2026-08-04",
                            first_seen={}, run_date="2026-08-04")


# --- the rule ---

def test_a_failed_read_does_not_overwrite_the_tab():
    fake = FakeSheets(read_raises=TimeoutError("network"))
    with pytest.raises(TriageReadFailed):
        _publish(fake, [_f("fp1")])
    assert fake.written is None, "the tab was rewritten after a failed read — triage would be lost"


def test_a_failed_read_reports_why():
    fake = FakeSheets(read_raises=TimeoutError("network went away"))
    with pytest.raises(TriageReadFailed) as e:
        _publish(fake, [_f("fp1")])
    assert "network went away" in str(e.value)


def test_a_missing_tab_is_a_first_run_not_a_failure():
    # never written before: there is no triage to lose, so writing is safe and correct
    fake = FakeSheets(tab_missing=True)
    _publish(fake, [_f("fp1")])
    assert fake.written is not None


# --- preservation through a normal run ---

def test_existing_triage_survives_the_rewrite():
    prior = [[""] * len(HEADER) for _ in range(1)]
    prior[0][HEADER.index("fingerprint")] = "fp1"
    prior[0][HEADER.index("status")] = ACKNOWLEDGED
    prior[0][HEADER.index("note")] = "dev ticket 41"
    fake = FakeSheets(existing=(HEADER, prior))
    _publish(fake, [_f("fp1")])
    header, rows = fake.written
    assert rows[0][header.index("status")] == ACKNOWLEDGED
    assert rows[0][header.index("note")] == "dev ticket 41"


def test_the_read_happens_before_the_write():
    fake = FakeSheets(existing=(HEADER, []))
    _publish(fake, [_f("fp1")])
    assert fake.reads == 1 and fake.written is not None


def test_rows_are_ordered_untriaged_errors_first():
    prior = [[""] * len(HEADER)]
    prior[0][HEADER.index("fingerprint")] = "old"
    prior[0][HEADER.index("status")] = "wontfix"
    fake = FakeSheets(existing=(HEADER, prior))
    findings = [_f("old"), _f("new", sev=Severity.ERROR)]
    _publish(fake, findings)
    header, rows = fake.written
    assert rows[0][header.index("fingerprint")] == "new", "untriaged must sort above wontfix"


# --- the digest line: the sheet must not have to create urgency ---

def test_digest_names_the_stale_backlog():
    line = __import__("auditor.sheets", fromlist=["digest_line"]).digest_line(
        "GL", {"new": 2, "resolved": 0, "open": 41}, (12, 63))
    assert "2 new" in line and "0 fixed" in line and "41 open" in line
    assert "12 ERRORs untriaged, oldest is 63 days" in line


def test_digest_on_a_clean_board_says_so():
    line = __import__("auditor.sheets", fromlist=["digest_line"]).digest_line(
        "TDRC", {"new": 0, "resolved": 3, "open": 0}, (0, 0))
    assert "nothing untriaged" in line


def test_summary_row_marks_a_run_as_running_before_it_finishes():
    from auditor.sheets import SUMMARY_HEADER, summary_row
    r = summary_row(brand="RR", status="running", started="2026-08-04T02:00:00")
    assert len(r) == len(SUMMARY_HEADER)
    assert r[SUMMARY_HEADER.index("status")] == "running"
    assert r[SUMMARY_HEADER.index("run_finished")] == "", "an unfinished run must look unfinished"
