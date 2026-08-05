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


def _f(fp, url="https://x/a/", check="phone", sev=Severity.ERROR, suggestion="sug", issue="i"):
    return Finding(url=url, check=check, severity=sev, fingerprint=fp,
                   issue=issue, location="l", snippet="s", suggestion=suggestion, details={})


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


# --- trimming must not ask to clear rows that do not exist ---

def test_replace_tab_skips_the_trim_when_content_fills_the_grid():
    """Writing auto-expands the grid, so clearing from row N+1 of an N-row grid starts past the
    last row and Sheets answers 400. AR hit this live at 1,184 rows."""
    from auditor.sheets import SheetsClient
    sent = []

    class C(SheetsClient):
        def _send(self, method, url, **kw):
            sent.append((method, url))

            class R:
                status_code = 200

                @staticmethod
                def raise_for_status():
                    return None

                @staticmethod
                def json():
                    return {"sheets": [{"properties": {"title": "T",
                                                       "gridProperties": {"rowCount": 3}}}]}
            return R()

    c = C(spreadsheet_id="s", credentials_path="x")
    c.replace_tab("T", ["h"], [["a"], ["b"]])            # 3 rows total == grid height
    assert not any(":clear" in u for _, u in sent), "asked to clear rows beyond the grid"

    sent.clear()
    c.replace_tab("T", ["h"], [["a"]])                   # 2 rows, grid is 3 -> one row to trim
    assert any(":clear" in u for _, u in sent), "left a stale trailing row"


# --- reordering columns must not eat the client's triage ---

OLD_HEADER = ["url", "check", "severity", "issue", "location", "snippet", "suggestion",
              "fingerprint", "first_seen", "age_days", "status", "note"]


def test_triage_written_in_the_OLD_column_order_still_survives():
    """The sheet already holds tabs written in the old order. The new code must read those by
    column NAME and carry the client's status/note across — if it read by position it would pick up
    'age_days' as the status and silently destroy a month of their notes."""
    from auditor.sheets import OPEN_HEADER
    row = [""] * len(OLD_HEADER)
    row[OLD_HEADER.index("fingerprint")] = "fp1"
    row[OLD_HEADER.index("status")] = "wontfix"
    row[OLD_HEADER.index("note")] = "agreed with client"
    fake = FakeSheets(existing=(OLD_HEADER, [row]))
    _publish(fake, [_f("fp1")])
    header, rows = fake.written
    assert header == OPEN_HEADER, "did not write the new order"
    assert rows[0][header.index("status")] == "wontfix"
    assert rows[0][header.index("note")] == "agreed with client"


def test_the_readers_columns_come_before_the_fingerprint():
    from auditor.sheets import OPEN_HEADER
    assert OPEN_HEADER.index("status") < OPEN_HEADER.index("fingerprint")
    assert OPEN_HEADER.index("note") < OPEN_HEADER.index("fingerprint")
    assert OPEN_HEADER.index("suggestion") < OPEN_HEADER.index("status")


def test_the_sheet_shows_human_labels_not_module_names():
    fake = FakeSheets(existing=None, tab_missing=True)
    _publish(fake, [_f("fp1", check="blank", suggestion="", issue="missing <h1>")])
    header, rows = fake.written
    assert rows[0][header.index("check")] == "Page content"
    assert rows[0][header.index("suggestion")].strip(), "shipped an empty suggestion"
