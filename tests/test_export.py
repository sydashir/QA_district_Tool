"""The Google Sheet export, built from the DATABASE rather than the crawl's report file.

WHY THIS CHANGED. The sheet used to be rebuilt from `reports/<brand>/<stamp>/findings.jsonl`, which
holds only what the CRAWL wrote. The accessibility pass and the render pass attach their findings to
the run row in Postgres and never touch that file, so the sheet and the client HTML report — which
reads the database — disagreed about the same run. Measured 2026-09-23: TDRC run 141 exported as
"80 open, 7 ERRORs" while the database held 93 and 20, and across the nine brands **265**
accessibility/contrast findings could not reach the sheet at all. Not a sampling gap but a whole
CATEGORY missing, which is the same false impression Section D was fixed for.

The database is already the system of record: the client report reads it, the web app reads it, the
passes write to it. So the sheet reads it too, and any future pass is visible everywhere at once.

Three run-level scalars STILL come from the crawl's summary.json, and deliberately: `css_status`,
`sitemap_partial` and the audit-set denominator exist nowhere else, and no pass ever changes them.
`pages_enumerated` on the run row is NOT that denominator — it is the sitemap count, which on AR is
4 against an audit set of 474. Using it would have understated coverage by 99%.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from server.export import result_from_run
from server.models import Base, Brand, Finding, Run, fp_hash

GL = "https://www.gratitudelodge.com"


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False, future=True)() as s:
        yield s
    engine.dispose()


def _report(tmp_path: Path, **summary) -> str:
    d = tmp_path / "20260923-000000"
    d.mkdir(parents=True, exist_ok=True)
    base = {"pages_audited": 10, "css_status": "ok", "sitemap_partial": False,
            "audit_scope": {"sitemap": 4, "rest_only_added": 470, "union": 474},
            "history_written": True}
    base.update(summary)
    (d / "summary.json").write_text(json.dumps(base), encoding="utf-8")
    (d / "findings.jsonl").write_text("", encoding="utf-8")   # deliberately EMPTY: never read now
    return str(d)


def _run(s, tmp_path, **kw) -> Run:
    b = Brand(code="GL", name="Gratitude Lodge", enabled=True, base_url=GL)
    s.add(b); s.flush()
    r = Run(brand_id=b.id, status="ok", pages_audited=kw.pop("pages_audited", 10),
            pages_enumerated=kw.pop("pages_enumerated", 4),      # the sitemap count, NOT the scope
            partial_sample=kw.pop("partial_sample", False),
            changed_components=kw.pop("changed_components", None),
            report_dir=_report(tmp_path, **kw.pop("summary", {})))
    s.add(r); s.flush()
    return r


def _finding(s, run, name, *, check="phone", severity="error", status="persisting"):
    fp = f"{run.id}|{name}"
    s.add(Finding(brand_id=run.brand_id, run_id=run.id, fingerprint=fp, fingerprint_hash=fp_hash(fp),
                  url=f"{GL}/{name}", check=check, severity=severity, issue=name,
                  details={}, status=status, page_count=1, first_seen="2026-08-01"))


# --------------------------------------------------------------------------- the defect
def test_the_sheet_sees_findings_no_crawl_ever_wrote(session, tmp_path):
    """THE bug. `findings.jsonl` in this fixture is EMPTY, so anything that comes back proves the
    result was built from the database. Accessibility and contrast are attached to the run by a
    pass minutes after the crawl wrote its report."""
    run = _run(session, tmp_path)
    _finding(session, run, "retired-number")
    _finding(session, run, "no-link-name", check="accessibility")
    _finding(session, run, "unreadable-button", check="contrast")
    session.commit()

    got = result_from_run(session, run)
    assert {f.check for f in got["findings"]} == {"phone", "accessibility", "contrast"}


def test_open_counts_are_the_databases_counts(session, tmp_path):
    """What the export line prints has to be what the dashboard and the HTML report print."""
    run = _run(session, tmp_path)
    for i in range(3):
        _finding(session, run, f"err{i}")
    _finding(session, run, "warn", severity="warning", status="new")
    session.commit()

    got = result_from_run(session, run)
    assert len([f for f in got["findings"] if f.status != "resolved"]) == 4
    assert got["run"]["rollup"].by_status == {"persisting": 3, "new": 1}


# --------------------------------------------------------------------------- what publish reads
def test_resolved_findings_are_carried_so_the_fixed_count_survives(session, tmp_path):
    """publish counts `run.resolved` for its "N fixed" figure and filters resolved out of the open
    tab. Dropping the tail would report every genuine fix as zero."""
    run = _run(session, tmp_path)
    _finding(session, run, "still-broken")
    _finding(session, run, "actually-fixed", status="resolved")
    _finding(session, run, "ruler-moved", status="rule_changed")
    session.commit()

    got = result_from_run(session, run)
    resolved = got["run"]["resolved"]
    assert [f.issue for f in resolved] == ["actually-fixed"], "rule_changed is NOT a fix"
    assert "actually-fixed" not in [f.issue for f in got["findings"] if f.status != "resolved"]


def test_a_sampled_run_keeps_its_partial_sample_banner(session, tmp_path):
    """DBH and MHD publish as PARTIAL SAMPLE. Losing that flag would present a sample as a complete
    audit — the single most dangerous thing this export could get wrong."""
    run = _run(session, tmp_path, partial_sample=True)
    _finding(session, run, "x")
    session.commit()
    assert result_from_run(session, run)["partial_sample"] is True


def test_the_denominator_is_the_audit_set_not_the_sitemap(session, tmp_path):
    """AR's sitemap advertises 4 URLs while 474 pages are actually audited (CLAUDE.md 6a). The
    PARTIAL SAMPLE banner says "N audited out of M live", so taking M from `pages_enumerated` would
    claim we audited 474 of 4."""
    run = _run(session, tmp_path, pages_enumerated=4)
    _finding(session, run, "x")
    session.commit()
    assert result_from_run(session, run)["scope_total"] == 474


def test_crawl_only_facts_still_come_from_the_crawls_own_summary(session, tmp_path):
    """css_status and sitemap_partial are recorded nowhere else, and no pass changes them. A
    partial CSS read means findings are less certain, and a partial sitemap withholds coverage
    claims — both must survive the move to the database."""
    run = _run(session, tmp_path, summary={"css_status": "partial", "sitemap_partial": True})
    _finding(session, run, "x")
    session.commit()
    got = result_from_run(session, run)
    assert got["css_status"] == "partial"
    assert got["sitemap_partial"] is True


def test_a_run_whose_report_is_gone_refuses_rather_than_dropping_the_caveats(session, tmp_path):
    """Degrading quietly here would publish a partial-sitemap run with no caveat, which reads as a
    complete clean audit. Refuse instead; the API turns it into a 409."""
    run = _run(session, tmp_path)
    run.report_dir = str(tmp_path / "gone")
    _finding(session, run, "x")
    session.commit()
    with pytest.raises(FileNotFoundError):
        result_from_run(session, run)


def test_pages_audited_comes_from_the_run_row(session, tmp_path):
    """publish REFUSES a result with 0 pages audited, on purpose."""
    run = _run(session, tmp_path, pages_audited=1239)
    _finding(session, run, "x")
    session.commit()
    assert result_from_run(session, run)["pages_audited"] == 1239


def test_changed_components_reach_publish_so_rule_changes_are_labelled(session, tmp_path):
    run = _run(session, tmp_path, changed_components=["src:parse.py"])
    _finding(session, run, "x")
    session.commit()
    assert result_from_run(session, run)["run"]["changed"] == ["src:parse.py"]
