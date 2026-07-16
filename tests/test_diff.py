"""In-stream run-diff: annotate new/persisting; classify resolved as genuine resolved vs
rule_changed (a check/global source changed) vs page_removed (confirmed not live) vs
page_unsitemapped (still live, but left the audited scope). Scope is the AUDITED set (pages
evaluated this run), liveness is the WP-REST LIVE set — never sitemap membership. Time and
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
    d = diff.RunDiff({}, now="2026-07-17", components=COMP, audited={URL})
    f = d.annotate(_f("a"))
    assert f.status == "new" and f.first_seen == "2026-07-17" == f.last_seen


def test_persisting_carries_first_seen():
    d = diff.RunDiff(_prior({"a": _snap()}), now="2026-07-17", components=COMP, audited={URL})
    f = d.annotate(_f("a"))
    assert f.status == "persisting" and f.first_seen == "2026-01-01" and f.last_seen == "2026-07-17"


def test_resolved_genuine():
    # components unchanged, the page WAS audited this run, finding gone -> a real fix
    d = diff.RunDiff(_prior({"gone": _snap(issue="HTTP 404", check="broken_links",
                                            severity="error")}),
                     now="2026-07-17", components=COMP, audited={URL})
    d.annotate(_f("a"))
    r = d.resolved_findings()[0]
    assert r.status == "resolved" and r.last_seen == "2026-06-01"


def test_rule_changed_when_that_check_changed():
    # meta.py source moved AND the page was audited -> vanished meta finding is rule_changed
    d = diff.RunDiff(_prior({"gone": _snap(check="meta")}, comps={**COMP, "src:meta.py": "OLD"}),
                     now="2026-07-17", components=COMP, audited={URL})
    d.annotate(_f("a"))
    assert d.resolved_findings()[0].status == "rule_changed"


def test_global_source_change_is_rule_changed_for_all():
    # parse.py changed -> even a broken_links resolved finding is rule_changed (global input moved)
    d = diff.RunDiff(_prior({"gone": _snap(check="broken_links")},
                            comps={**COMP, "src:parse.py": "OLD"}),
                     now="2026-07-17", components=COMP, audited={URL})
    d.annotate(_f("a"))
    assert d.resolved_findings()[0].status == "rule_changed"


def test_page_removed_when_confirmed_not_live():
    # not audited AND absent from the WP-REST live set -> genuinely gone
    d = diff.RunDiff(_prior({"gone": _snap(url="https://x/deleted/")}),
                     now="2026-07-17", components=COMP, audited={URL}, live={URL})
    d.annotate(_f("a"))
    assert d.resolved_findings()[0].status == "page_removed"


def test_page_unsitemapped_when_still_live_but_unaudited():
    # THE CATCH: page dropped from the sitemap (not audited) but STILL LIVE -> not a resolution.
    # A page sitting right there serving its broken link must never be reported "removed".
    dead_url = "https://x/noindexed/"
    d = diff.RunDiff(_prior({"gone": _snap(url=dead_url)}),
                     now="2026-07-17", components=COMP, audited={URL}, live={URL, dead_url})
    d.annotate(_f("a"))
    assert d.resolved_findings()[0].status == "page_unsitemapped"


def test_unaudited_without_live_set_never_claims_removal():
    # No live set -> we can't confirm removal, so we must NOT say page_removed. Under-claim.
    d = diff.RunDiff(_prior({"gone": _snap(url="https://x/unknown/")}),
                     now="2026-07-17", components=COMP, audited={URL})  # live=None
    d.annotate(_f("a"))
    assert d.resolved_findings()[0].status == "page_unsitemapped"


def test_persist_keeps_components_and_only_seen(tmp_path):
    d = diff.RunDiff(_prior({"gone": _snap()}), now="2026-07-17", components=COMP, audited={URL})
    d.annotate(_f("a"))
    p = tmp_path / "history.json"
    d.persist(p)
    h = json.loads(p.read_text())
    assert h["components"] == COMP
    assert "a" in h["findings"] and "gone" not in h["findings"]
