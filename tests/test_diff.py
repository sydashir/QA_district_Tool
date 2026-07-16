"""In-stream run-diff: annotate new/persisting; classify resolved as genuine resolved vs
stale_ruleset (a check/global source changed) vs page_removed (url no longer live). Time and
components injected for reproducibility.
"""
from __future__ import annotations

import json

from auditor import diff
from auditor.report import Finding, Severity

COMP = {"src:meta.py": "m1", "src:parse.py": "p1", "src:report.py": "r1", "canonical_phones": []}
URL = "https://x/p/"


def _f(fp, **kw):
    base = dict(url=URL, check="meta", fingerprint=fp, severity=Severity.WARNING, issue="i")
    base.update(kw)
    return Finding(**base)


def _snap(**kw):
    base = dict(first_seen="2026-01-01", last_seen="2026-06-01", url=URL, check="meta",
                issue="i", severity="warning", location=None)
    base.update(kw)
    return base


def _prior(findings, comps=COMP):
    return {"components": comps, "findings": findings}


def test_new_finding():
    d = diff.RunDiff({}, now="2026-07-17", components=COMP, enumerated={URL})
    f = d.annotate(_f("a"))
    assert f.status == "new" and f.first_seen == "2026-07-17" == f.last_seen


def test_persisting_carries_first_seen():
    d = diff.RunDiff(_prior({"a": _snap()}), now="2026-07-17", components=COMP, enumerated={URL})
    f = d.annotate(_f("a"))
    assert f.status == "persisting" and f.first_seen == "2026-01-01" and f.last_seen == "2026-07-17"


def test_resolved_genuine():
    # components unchanged, page still live -> a real fix
    d = diff.RunDiff(_prior({"gone": _snap(issue="HTTP 404", check="broken_links",
                                            severity="error")}),
                     now="2026-07-17", components=COMP, enumerated={URL})
    d.annotate(_f("a"))
    r = d.resolved_findings()[0]
    assert r.status == "resolved" and r.last_seen == "2026-06-01"


def test_stale_ruleset_when_that_check_changed():
    # meta.py source moved -> a vanished meta finding is stale ruleset, NOT resolved
    d = diff.RunDiff(_prior({"gone": _snap(check="meta")}, comps={**COMP, "src:meta.py": "OLD"}),
                     now="2026-07-17", components=COMP, enumerated={URL})
    d.annotate(_f("a"))
    assert d.resolved_findings()[0].status == "stale_ruleset"


def test_global_source_change_is_stale_for_all():
    # parse.py changed -> even a broken_links resolved finding is stale (global input moved)
    d = diff.RunDiff(_prior({"gone": _snap(check="broken_links")},
                            comps={**COMP, "src:parse.py": "OLD"}),
                     now="2026-07-17", components=COMP, enumerated={URL})
    d.annotate(_f("a"))
    assert d.resolved_findings()[0].status == "stale_ruleset"


def test_page_removed_when_url_gone():
    d = diff.RunDiff(_prior({"gone": _snap(url="https://x/deleted/")}),
                     now="2026-07-17", components=COMP, enumerated={URL})  # deleted not enumerated
    d.annotate(_f("a"))
    assert d.resolved_findings()[0].status == "page_removed"


def test_persist_keeps_components_and_only_seen(tmp_path):
    d = diff.RunDiff(_prior({"gone": _snap()}), now="2026-07-17", components=COMP, enumerated={URL})
    d.annotate(_f("a"))
    p = tmp_path / "history.json"
    d.persist(p)
    h = json.loads(p.read_text())
    assert h["components"] == COMP
    assert "a" in h["findings"] and "gone" not in h["findings"]
