"""A --limit sample must not become the diff baseline."""
import json
from pathlib import Path

from auditor.audit import PageProjection, write_run
from auditor.config import load_brand
from auditor.report import Finding, Severity


def _f(u):
    return Finding(url=u, check="phone", severity=Severity.ERROR, fingerprint=f"phone:x:{u}",
                   issue="i", location="l", snippet="s", suggestion="g", details={})


def _run(tmp, persist):
    cfg = load_brand("gl")
    h = tmp / "history.json"
    write_run([_f("https://x/a/")], [PageProjection(url="https://x/a/")], brand="gl",
              base_url="https://x", now="2026-01-01T00:00:00", config=cfg, live=None,
              out_dir=tmp / "out", history_path=h, persist_history=persist)
    return h


def test_a_sampled_run_leaves_the_baseline_untouched(tmp_path):
    h = _run(tmp_path, persist=False)
    assert not h.exists(), "a -n sample wrote the diff baseline"
    meta = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert meta["history_written"] is False       # the report says so on its face


def test_a_full_run_still_writes_the_baseline(tmp_path):
    h = _run(tmp_path, persist=True)
    assert h.exists() and json.loads(h.read_text())["findings"]
    meta = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert meta["history_written"] is True
