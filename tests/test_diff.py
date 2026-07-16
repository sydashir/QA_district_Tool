"""In-stream run-diff: findings annotated with first_seen/last_seen/status as they stream;
resolved (prior fingerprints not seen this run) emitted as a tail from history snapshots.
Time is passed in explicitly (no wall-clock in the logic) so runs are reproducible.
"""
from __future__ import annotations

import json

from auditor import diff
from auditor.report import Finding, Severity


def _f(fp, **kw):
    base = dict(url="u", check="c", fingerprint=fp, severity=Severity.WARNING, issue="i")
    base.update(kw)
    return Finding(**base)


def _snap(**kw):
    base = dict(first_seen="2026-01-01", last_seen="2026-06-01", url="u", check="c",
                issue="i", severity="warning", location=None)
    base.update(kw)
    return base


def test_new_finding():
    d = diff.RunDiff({}, now="2026-07-17")
    f = d.annotate(_f("a"))
    assert f.status == "new"
    assert f.first_seen == "2026-07-17" and f.last_seen == "2026-07-17"


def test_persisting_carries_first_seen():
    d = diff.RunDiff({"a": _snap()}, now="2026-07-17")
    f = d.annotate(_f("a"))
    assert f.status == "persisting"
    assert f.first_seen == "2026-01-01" and f.last_seen == "2026-07-17"


def test_resolved_tail_from_snapshot():
    prior = {"gone": _snap(url="https://x/404/", check="broken_links",
                            issue="HTTP 404", severity="error", location="https://t/")}
    d = diff.RunDiff(prior, now="2026-07-17")
    d.annotate(_f("a"))  # 'gone' never seen this run
    res = d.resolved_findings()
    assert len(res) == 1
    r = res[0]
    assert r.status == "resolved" and r.fingerprint == "gone"
    assert r.issue == "HTTP 404" and r.last_seen == "2026-06-01"  # last time it was present


def test_persist_keeps_only_seen(tmp_path):
    d = diff.RunDiff({"gone": _snap()}, now="2026-07-17")
    d.annotate(_f("a"))
    p = tmp_path / "history.json"
    d.persist(p)
    h = json.loads(p.read_text())
    assert "a" in h and "gone" not in h  # resolved drops out of history
