"""Publishing must be safe to re-run, and must never half-update a brand.

A nightly run publishes nine brands. If it dies on brand four, re-running must produce exactly the
same sheet as an uninterrupted run — no duplicated Summary rows, no tab left empty, no brand
partially updated. Nobody is awake at 3am to reconcile it by hand.
"""
from __future__ import annotations

import pytest

from auditor.publish import publish_brand
from auditor.report import Finding, Severity
from auditor.publish import SUMMARY_HEADER_WITH_RUN as SUMMARY_HEADER


def _f(fp, sev=Severity.ERROR):
    return Finding(url="https://x/a/", check="phone", severity=sev, fingerprint=fp,
                   issue="i", location="l", snippet="s", suggestion="sug", details={})


class FakeSheets:
    """Records every call and can be told to explode at a chosen one."""

    def __init__(self, fail_on=None):
        self.tabs: dict[str, tuple[list, list]] = {}
        self.calls: list[str] = []
        self.fail_on = fail_on            # e.g. "replace:GL — new"

    def _maybe_fail(self, label):
        self.calls.append(label)
        if self.fail_on == label:
            raise RuntimeError(f"boom at {label}")

    def read_tab(self, tab):
        self._maybe_fail(f"read:{tab}")
        return self.tabs.get(tab)

    def ensure_tab(self, tab):
        self._maybe_fail(f"ensure:{tab}")
        self.tabs.setdefault(tab, (["fingerprint"], []))

    def replace_tab(self, tab, header, rows):
        self._maybe_fail(f"replace:{tab}")
        self.tabs[tab] = (header, [list(r) for r in rows])

    def append_row(self, tab, row):
        self._maybe_fail(f"append:{tab}")
        if tab not in self.tabs or not self.tabs[tab][0] or self.tabs[tab][0] == ["fingerprint"]:
            self.tabs[tab] = (SUMMARY_HEADER, [])
        self.tabs[tab][1].append(list(row))

    def update_row(self, tab, row_index, row):
        self._maybe_fail(f"update:{tab}:{row_index}")
        self.tabs[tab][1][row_index] = list(row)


def _run(fake, run_id="run-1", findings=None, fail_on=None):
    fake.fail_on = fail_on
    return publish_brand(fake, brand="GL", run_id=run_id,
                         findings=findings if findings is not None else [_f("a"), _f("b")],
                         delta={"new": 1, "resolved": 0, "open": 2},
                         changed_checks=set(), first_seen={}, run_date="2026-08-04",
                         started="2026-08-04T02:00:00", pages=10)


def _summary(fake):
    return fake.tabs.get("Summary", (SUMMARY_HEADER, []))[1]


# --- idempotence ---

def test_a_clean_rerun_does_not_duplicate_the_summary_row():
    fake = FakeSheets()
    _run(fake)
    _run(fake)                                     # same run_id, e.g. a manual re-run after a crash
    rows = _summary(fake)
    assert len(rows) == 1, f"re-running the same run duplicated Summary rows: {len(rows)}"
    assert rows[0][SUMMARY_HEADER.index("status")] == "ok"


def test_a_new_run_id_appends_a_new_summary_row():
    fake = FakeSheets()
    _run(fake, run_id="run-1")
    _run(fake, run_id="run-2")
    assert len(_summary(fake)) == 2, "each real run must leave its own history row"


def test_rerunning_after_a_crash_completes_the_brand():
    fake = FakeSheets()
    with pytest.raises(RuntimeError):
        _run(fake, fail_on="replace:GL — new")     # died between the two tabs
    assert _summary(fake)[0][SUMMARY_HEADER.index("status")] == "running", \
        "a crashed brand must be visibly unfinished, not silently absent"
    _run(fake)                                     # re-run
    assert "GL — open" in fake.tabs and "GL — new" in fake.tabs
    assert len(_summary(fake)) == 1, "the retry must update the running row, not append a second"
    assert _summary(fake)[0][SUMMARY_HEADER.index("status")] == "ok"


def test_a_crash_leaves_the_run_marked_running_not_ok():
    fake = FakeSheets()
    with pytest.raises(RuntimeError):
        _run(fake, fail_on="replace:GL — open")
    assert _summary(fake)[0][SUMMARY_HEADER.index("status")] == "running"


# --- the failure must be per-brand, not half a brand ---

def test_summary_is_marked_ok_only_after_both_tabs_are_written():
    fake = FakeSheets()
    order = []
    real = fake.replace_tab

    def spy(tab, header, rows):
        order.append(tab)
        return real(tab, header, rows)
    fake.replace_tab = spy
    _run(fake)
    assert order == ["GL — open", "GL — new"]
    ok_at = fake.calls.index("update:Summary:0")
    assert ok_at > fake.calls.index("replace:GL — new"), \
        "Summary said ok before the tabs were written"


# --- dry run: renders everything, touches nothing ---

def test_dry_run_writes_nothing_to_the_sheet():
    import io
    from auditor.sheets import DryRunSheets
    real = FakeSheets()
    buf = io.StringIO()
    dry = DryRunSheets(buf, reader=real)
    publish_brand(dry, brand="GL", run_id="r1", findings=[_f("a")],
                  delta={"new": 1, "resolved": 0, "open": 1}, changed_checks=set(),
                  first_seen={}, run_date="2026-08-04", started="2026-08-04T02:00:00", pages=1)
    assert real.tabs == {}, "a dry run touched the sheet"
    text = buf.getvalue()
    assert "would REPLACE 'GL — open'" in text and "would APPEND" in text
    assert len(dry.writes) >= 3


def test_dry_run_still_shows_the_real_triage_merge():
    # reading is allowed in a dry run, so the preview reflects what the client actually wrote
    import io
    from auditor.publish import SUMMARY_HEADER_WITH_RUN
    from auditor.sheets import OPEN_HEADER, DryRunSheets
    real = FakeSheets()
    prior = [[""] * len(OPEN_HEADER)]
    prior[0][OPEN_HEADER.index("fingerprint")] = "a"
    prior[0][OPEN_HEADER.index("status")] = "wontfix"
    real.tabs["GL — open"] = (OPEN_HEADER, prior)
    buf = io.StringIO()
    publish_brand(DryRunSheets(buf, reader=real), brand="GL", run_id="r1", findings=[_f("a")],
                  delta={"new": 0, "resolved": 0, "open": 1}, changed_checks=set(),
                  first_seen={}, run_date="2026-08-04", started="2026-08-04T02:00:00", pages=1)
    assert "wontfix" in buf.getvalue(), "the preview lost the client's existing triage"
