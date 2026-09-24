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

    def ensure_tab(self, tab, header=None):
        self._maybe_fail(f"ensure:{tab}")
        # A REAL new tab is empty. The first version of this fake invented a header here, which
        # hid a live bug: without a header row the first append lands in A1 and is then read AS
        # the header, so (run_id, brand) can never be found and every re-run appends a duplicate.
        if tab not in self.tabs:
            self.tabs[tab] = (list(header) if header else [], [])
        elif header and not self.tabs[tab][0]:
            self.tabs[tab] = (list(header), self.tabs[tab][1])
        elif header and list(self.tabs[tab][0]) != list(header) \
                and list(header[:len(self.tabs[tab][0])]) == list(self.tabs[tab][0]):
            # Mirrors the real client: EXTEND a header written before a column existed, and only
            # when the live one is a strict prefix (columns appended, nothing moved). A fake that
            # skipped this would let the live sheet keep an 18-column header while the code wrote 19.
            self.tabs[tab] = (list(header), self.tabs[tab][1])

    def replace_tab(self, tab, header, rows):
        self._maybe_fail(f"replace:{tab}")
        self.tabs[tab] = (header, [list(r) for r in rows])

    def append_row(self, tab, row):
        self._maybe_fail(f"append:{tab}")
        hdr, rows = self.tabs.setdefault(tab, ([], []))
        rows.append(list(row))

    def read_tab_raw(self, tab):
        return self.tabs.get(tab)

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


def test_the_summary_tab_gets_a_header_so_reruns_can_find_their_row():
    """Without a header row the first append lands in A1 and is read back AS the header, so
    (run_id, brand) is never found and every re-run appends a duplicate. Found on the first live
    publish; the original fake invented a header and hid it."""
    fake = FakeSheets()
    _run(fake)
    hdr, rows = fake.tabs["Summary"]
    assert hdr and hdr[0] == "run_id", f"Summary has no header row: {hdr!r}"
    assert len(rows) == 1
    _run(fake)
    assert len(fake.tabs["Summary"][1]) == 1, "a re-run duplicated the row"


def test_a_zero_page_audit_is_never_published_as_ok():
    """MHD hit this live: enumeration returned nothing, run_audit correctly refused to write a
    report — and publish still wrote a Summary row saying `ok` with 0 findings. An empty audit
    presented as a successful brand is the same lie as a partial sitemap yielding confident
    coverage findings."""
    import pytest as _pytest
    from auditor.publish import EmptyAuditRefused, publish_brand_from_result
    fake = FakeSheets()
    with _pytest.raises(EmptyAuditRefused):
        publish_brand_from_result("mhd", {"pages_audited": 0, "findings": []},
                                  run_id="r1", dry_run=False, client=fake)
    assert fake.tabs == {}, "an empty audit was published"


# --------------------------------------------------------------------------- what counts as OPEN
# A finding on a page that now 404s is not work anybody can do — the page is gone. The sheet used to
# count those in its open tab while `scripts/client_report.py` (which filters `status IN
# ('new','persisting')`) did not, so the two client-facing surfaces disagreed. Measured on GL run 142,
# 2026-09-24: the sheet said 9,173 open / 800 errors, the report 8,077 / 567 — a 1,096 gap that was
# invisible until GL deleted 224 pages in one go. The removals are real information, so they are
# SAID in Summary rather than dropped.

def _f_status(fp, status, url="https://x/a/", sev=Severity.ERROR):
    f = _f(fp, sev)
    f.url = url
    f.status = status
    return f


def _publish(findings, **kw):
    c = FakeSheets()
    delta = {"new": 0, "resolved": 0, "rule_changed": 0, "open": 0, "new_fingerprints": set()}
    delta.update(kw.pop("delta", {}))
    publish_brand(c, brand="GL", run_id="r1", findings=findings, delta=delta,
                  changed_checks=set(), first_seen={}, run_date="2026-09-24",
                  started="2026-09-24T01:00:00", finished="2026-09-24T02:00:00",
                  pages=10, counts={"ERROR": len(findings)}, **kw)
    return c


def test_a_finding_on_a_removed_page_never_reaches_the_open_tab():
    """publish_brand is handed only open work; the filtering happens above it. This pins the tab
    itself: whatever arrives is what the QA team sees, so a removed page must not arrive."""
    c = _publish([_f_status("keep", "persisting")])
    _, rows = c.tabs["GL — open"]
    assert len(rows) == 1


def test_the_summary_says_how_many_pages_were_removed():
    """Not dropped silently. 224 pages vanishing from GL is the story of that audit."""
    c = _publish([_f_status("keep", "persisting")], delta={"pages_removed": 224})
    hdr, rows = c.tabs["Summary"]
    assert "pages_removed" in hdr
    assert rows[0][hdr.index("pages_removed")] == "224"


def test_a_run_that_removed_nothing_says_nothing_about_removals():
    """A '0 pages removed' note on every brand every run is noise that trains people to skip the
    detail column — which is where PARTIAL SAMPLE also lives."""
    c = _publish([_f_status("keep", "persisting")])
    hdr, rows = c.tabs["Summary"]
    assert rows[0][hdr.index("pages_removed")] == "0"
    assert "removed" not in rows[0][hdr.index("detail")].lower()


def test_the_new_column_goes_after_the_existing_ones():
    """Summary is append-only history. Inserting a column mid-header would re-point every row
    already written — `detail` on an old row would be read as the new column."""
    assert SUMMARY_HEADER[-1] == "pages_removed"
    assert SUMMARY_HEADER.index("detail") < SUMMARY_HEADER.index("pages_removed")


def test_a_sheet_written_before_the_column_existed_gets_it_added():
    """The live Summary tab already has the old header, and `ensure_tab` only seeds a header when
    the tab has NONE — so without this the value lands in a column with no name and every reader
    that uses header.index() fails to find it."""
    c = FakeSheets()
    old = SUMMARY_HEADER[:-1]
    c.tabs["Summary"] = (list(old), [["r0", "s", "f", "GL"] + [""] * (len(old) - 4)])
    c.ensure_tab("Summary", header=SUMMARY_HEADER)
    hdr, rows = c.tabs["Summary"]
    assert hdr == SUMMARY_HEADER
    assert rows[0][3] == "GL", "an existing row must not shift"


def test_only_new_and_persisting_findings_are_open_work():
    """THE change, tested where it actually lives. The filter is in `publish_brand_from_result`,
    not in `publish_brand` — the tests above drive the lower layer and would all still pass with
    this reverted. Statuses here are one of each carried state plus the two that are real work."""
    from auditor.publish import publish_brand_from_result

    class _Rollup:
        by_status = {"new": 1, "persisting": 1, "rule_changed": 1,
                     "page_removed": 2, "page_unsitemapped": 1}

    findings = [
        _f_status("open-new", "new", url="https://x/live-a/"),
        _f_status("open-old", "persisting", url="https://x/live-b/"),
        _f_status("ruler", "rule_changed", url="https://x/live-c/"),
        _f_status("gone-1", "page_removed", url="https://x/gone-1/"),
        _f_status("gone-2", "page_removed", url="https://x/gone-1/"),   # SAME page, 2 findings
        _f_status("gone-3", "page_removed", url="https://x/gone-2/"),
        _f_status("out-of-scope", "page_unsitemapped", url="https://x/live-d/"),
    ]
    c = FakeSheets()
    publish_brand_from_result("GL", {
        "pages_audited": 10, "findings": findings,
        "run": {"rollup": _Rollup(), "resolved": [], "changed": []},
        "css_status": "ok", "sitemap_partial": False, "partial_sample": False,
        "scope_total": 10}, run_id="r1", client=c)

    _, open_rows = c.tabs["GL — open"]
    assert len(open_rows) == 2, "only the new and persisting findings are work anybody can do"

    hdr, rows = c.tabs["Summary"]
    assert rows[0][hdr.index("pages_removed")] == "2", "2 PAGES gone, not 3 findings"
    detail = rows[0][hdr.index("detail")]
    assert "2 pages covered by the previous audit are no longer listed" in detail
    assert "removed or redirected" in detail, "must not claim deletion it never checked"
    assert "1 page left the audit scope" in detail


def test_a_run_with_no_removals_adds_no_removal_sentence():
    """Tested through `publish_brand_from_result`, which is what builds `detail` — the earlier
    zero-removal test drives `publish_brand` and never reaches that code, so a mutation making the
    note unconditional survived it."""
    from auditor.publish import publish_brand_from_result

    class _Rollup:
        by_status = {"persisting": 1}

    c = FakeSheets()
    publish_brand_from_result("GL", {
        "pages_audited": 10, "findings": [_f_status("a", "persisting")],
        "run": {"rollup": _Rollup(), "resolved": [], "changed": []},
        "css_status": "ok", "sitemap_partial": False, "partial_sample": False,
        "scope_total": 10}, run_id="r1", client=c)
    hdr, rows = c.tabs["Summary"]
    assert "removed" not in rows[0][hdr.index("detail")].lower()
    assert rows[0][hdr.index("pages_removed")] == "0"


def test_a_sampled_brand_still_carries_its_partial_sample_warning():
    """The most dangerous sentence on the sheet to lose: without it a 621-of-10,727-page MHD sample
    reads as a complete audit of a clean site. It had no test until `_detail` was extracted, and a
    mutation deleting it survived every other test here."""
    from auditor.publish import publish_brand_from_result

    class _Rollup:
        by_status = {"persisting": 1}

    c = FakeSheets()
    publish_brand_from_result("MHD", {
        "pages_audited": 621, "findings": [_f_status("a", "persisting")],
        "run": {"rollup": _Rollup(), "resolved": [], "changed": []},
        "css_status": "ok", "sitemap_partial": False, "partial_sample": True,
        "scope_total": 10727}, run_id="r1", client=c)
    hdr, rows = c.tabs["Summary"]
    detail = rows[0][hdr.index("detail")]
    assert "PARTIAL SAMPLE" in detail
    assert "621 pages audited out of 10727" in detail
