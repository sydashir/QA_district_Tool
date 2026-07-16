"""M2 writers: JSONL (complete, one finding/line), CSV (one row/finding, human triage),
summary.json (in-stream rollup counters). All consume an iterable of findings so run_audit
can stream into them without materializing the full list.
"""
from __future__ import annotations

import csv
import json

from auditor import writers
from auditor.report import Finding, Severity


def _f(**kw):
    base = dict(url="https://x/p/", check="meta", fingerprint="fp",
                severity=Severity.WARNING, issue="i")
    base.update(kw)
    return Finding(**base)


def test_rollup_counts():
    r = writers.Rollup()
    r.add(_f(check="meta", severity=Severity.ERROR, fingerprint="a"))
    r.add(_f(check="meta", severity=Severity.WARNING, fingerprint="b"))
    r.add(_f(check="phone", severity=Severity.WARNING, fingerprint="c"))
    d = r.to_dict()
    assert d["total"] == 3
    assert d["by_severity"] == {"error": 1, "warning": 2}
    assert d["by_check"]["meta"] == 2 and d["by_check"]["phone"] == 1


def test_jsonl_roundtrip_keeps_full_sources(tmp_path):
    fs = [_f(fingerprint="a"),
          _f(check="broken_links", fingerprint="broken_links:https://t/",
             details={"sources": ["https://x/1/", "https://x/2/"]})]
    p = tmp_path / "f.jsonl"
    writers.write_jsonl(fs, p)
    lines = p.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["details"]["sources"] == ["https://x/1/", "https://x/2/"]


def test_csv_flattens_broken_link_sources(tmp_path):
    fs = [_f(check="broken_links", url="https://x/src1/", fingerprint="broken_links:https://t/",
             location="https://t/",
             details={"sources": ["https://x/src1/", "https://x/src2/", "https://x/src3/"]})]
    p = tmp_path / "f.csv"
    writers.write_csv(fs, p, brand="GL", titles={"https://x/src1/": "Src One"})
    row = next(csv.DictReader(p.open()))
    assert row["brand"] == "GL"
    assert row["page_url"] == "https://x/src1/"     # representative source
    assert row["page_title"] == "Src One"
    assert row["source_count"] == "3"               # flattened count, full array is in JSONL
    assert row["first_seen"] == "" and row["last_seen"] == ""  # populated by the diff step


def test_summary_has_meta_and_rollup(tmp_path):
    r = writers.Rollup()
    r.add(_f(fingerprint="a"))
    p = tmp_path / "s.json"
    writers.write_summary(r, {"brand": "GL", "pages_fetched": 10}, p)
    d = json.loads(p.read_text())
    assert d["brand"] == "GL" and d["pages_fetched"] == 10 and d["rollup"]["total"] == 1
