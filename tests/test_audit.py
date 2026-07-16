"""M2 assembly (``audit.write_run``): annotate findings in-stream (new/persisting), emit the
resolved tail, write JSONL+CSV+summary, persist run history for the next diff. Network-free —
projections and findings are built by hand; RunDiff classification nuance lives in test_diff.
"""
from __future__ import annotations

import csv
import json
from types import SimpleNamespace

from auditor import audit
from auditor.report import Finding, Severity

CFG = SimpleNamespace(canonical_phones=[])
PROJ = [audit.PageProjection(url="https://x/a/", title="Page A")]


def _f(fp, url="https://x/a/", check="meta", issue="i"):
    return Finding(url=url, check=check, fingerprint=fp, severity=Severity.WARNING, issue=issue)


def _phone(cls, url, e164, sev=Severity.ERROR):
    return Finding(url=url, check="phone", severity=sev,
                   fingerprint=f"phone:{cls}:{url}:{e164}", issue="retired phone number",
                   location="page", snippet=e164, details={"number": e164, "class": cls})


def test_collapse_phone_site_wide_number_is_one_finding():
    # same retired number on 3 pages -> ONE finding carrying all 3 sources (broken_links shape)
    fs = [_phone("retired", f"https://x/p{i}/", "+18006929850") for i in range(3)]
    fs.append(_f("meta:x", check="meta"))  # non-phone passes through untouched
    out = audit._collapse_phone(fs)
    phone = [f for f in out if f.check == "phone"]
    assert len(phone) == 1
    f = phone[0]
    assert f.fingerprint == "phone:retired:+18006929850"  # identity = number, no url
    assert f.details["sources"] == ["https://x/p0/", "https://x/p1/", "https://x/p2/"]
    assert f.details["page_count"] == 3
    assert any(f.check == "meta" for f in out)


def test_collapse_phone_distinct_numbers_stay_separate():
    fs = [_phone("retired", "https://x/a/", "+18006929850"),
          _phone("unknown", "https://x/a/", "+12125551234", sev=Severity.WARNING)]
    out = audit._collapse_phone(fs)
    assert {f.fingerprint for f in out} == {
        "phone:retired:+18006929850", "phone:unknown:+12125551234"}


def test_collapse_leaves_mismatch_per_page():
    # mismatch is element-specific -> NOT collapsed
    fs = [Finding(url="https://x/a/", check="phone", severity=Severity.ERROR,
                  fingerprint="phone:mismatch:https://x/a/:+18006929850", issue="mismatch")]
    out = audit._collapse_phone(fs)
    assert out[0].fingerprint == "phone:mismatch:https://x/a/:+18006929850"


def _run(findings, tmp_path, now, hist, sub, live={"https://x/a/"}):
    return audit.write_run(
        findings, PROJ, brand="GL", base_url="https://x", now=now, config=CFG,
        live=live, out_dir=tmp_path / sub, history_path=hist)


def test_first_run_all_new_writes_files(tmp_path):
    hist = tmp_path / "history.json"
    fs = [_f("fp1")]
    out = _run(fs, tmp_path, "2026-07-16T01:00:00", hist, "r1")

    assert fs[0].status == "new" and fs[0].first_seen == "2026-07-16T01:00:00"
    assert out["rollup"].by_status["new"] == 1
    assert out["changed"] == []  # first run has no baseline -> nothing "changed" (not all-of-it)
    assert (tmp_path / "r1" / "findings.jsonl").exists()
    assert (tmp_path / "r1" / "summary.json").exists()
    assert hist.exists()

    # CSV first_seen is filled by the annotate step (writers test proved it's blank otherwise)
    row = next(csv.DictReader((tmp_path / "r1" / "findings.csv").open()))
    assert row["status"] == "new" and row["first_seen"] == "2026-07-16T01:00:00"


def test_second_run_persists_and_resolves(tmp_path):
    hist = tmp_path / "history.json"
    _run([_f("fp1"), _f("fp2")], tmp_path, "2026-01-01T00:00:00", hist, "r1")

    # run 2: fp1 still there (page audited + live), fp2 gone -> a genuine resolve
    fs2 = [_f("fp1")]
    out = _run(fs2, tmp_path, "2026-02-02T00:00:00", hist, "r2")

    assert fs2[0].status == "persisting" and fs2[0].first_seen == "2026-01-01T00:00:00"
    resolved = out["resolved"]
    assert [r.fingerprint for r in resolved] == ["fp2"]
    assert resolved[0].status == "resolved"
    assert out["rollup"].by_status.get("resolved") == 1

    # resolved finding lands in the same report stream (tail), carrying its old last_seen
    rows = [json.loads(l) for l in (tmp_path / "r2" / "findings.jsonl").read_text().splitlines()]
    statuses = {r["fingerprint"]: r["status"] for r in rows}
    assert statuses == {"fp1": "persisting", "fp2": "resolved"}


def test_write_is_atomic_no_partial_residue(tmp_path):
    # on success the report is promoted from <stamp>.partial to <stamp>, leaving no partial dir
    # or .tmp history behind (a crash mid-write would leave the .partial, never a real report).
    hist = tmp_path / "history.json"
    out = tmp_path / "20260101-000000"
    audit.write_run([_f("fp1")], PROJ, brand="GL", base_url="https://x",
                    now="2026-07-16T01:00:00", config=CFG, live=None, out_dir=out,
                    history_path=hist)
    assert out.exists() and (out / "findings.jsonl").exists()
    assert not out.with_name(out.name + ".partial").exists()
    assert not hist.with_suffix(".json.tmp").exists()


def test_extra_meta_reaches_summary(tmp_path):
    # the phone-scope caveat (and any run meta) must land in summary.json for the client
    out = audit.write_run(
        [_f("fp1")], PROJ, brand="GL", base_url="https://x", now="2026-07-16T01:00:00",
        config=CFG, live=None, out_dir=tmp_path / "r1", history_path=tmp_path / "h.json",
        extra_meta={"phone_scope_caveat": "validated brand-wide, not per-page"})
    s = json.loads((tmp_path / "r1" / "summary.json").read_text())
    assert s["phone_scope_caveat"] == "validated brand-wide, not per-page"


def test_history_only_keeps_seen_fingerprints(tmp_path):
    hist = tmp_path / "history.json"
    _run([_f("fp1"), _f("fp2")], tmp_path, "2026-01-01T00:00:00", hist, "r1")
    _run([_f("fp1")], tmp_path, "2026-02-02T00:00:00", hist, "r2")
    h = json.loads(hist.read_text())
    # fp2 resolved this run -> drops from history (reappearing later would be 'new' again)
    assert set(h["findings"]) == {"fp1"}
    assert "components" in h
