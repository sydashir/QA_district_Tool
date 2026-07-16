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


def test_history_only_keeps_seen_fingerprints(tmp_path):
    hist = tmp_path / "history.json"
    _run([_f("fp1"), _f("fp2")], tmp_path, "2026-01-01T00:00:00", hist, "r1")
    _run([_f("fp1")], tmp_path, "2026-02-02T00:00:00", hist, "r2")
    h = json.loads(hist.read_text())
    # fp2 resolved this run -> drops from history (reappearing later would be 'new' again)
    assert set(h["findings"]) == {"fp1"}
    assert "components" in h
