"""Demo seeder tests — the properties that make synthetic data safe and useful.

Two of these exist because the seeder got them wrong first. It seeded its RNG per RUN rather than
per BRAND, which reshuffled the catalogue every run and therefore changed every fingerprint, so
nothing persisted across runs and the trend line was noise. And its "fixed"/"appeared" events were
pinned to run indexes 3 and 4, so with the default six runs the NEWEST run — the only one "what
changed" shows — had neither.

Runs against throwaway SQLite and a tmp_path report tree; nothing touches the real reports/ or the
real database.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scripts import seed_demo
from server import importer
from server.models import Base, Finding


RUNS = 6
ANCHOR = datetime(2026, 9, 2, 2, 10)


@pytest.fixture()
def demo_root(tmp_path, monkeypatch):
    root = tmp_path / "reports" / "_demo"
    monkeypatch.setattr(seed_demo, "DEMO_ROOT", root)
    monkeypatch.setattr(seed_demo, "REPO", tmp_path)
    return root


def _write_history(code: str) -> list[list[dict]]:
    """All RUNS reports for one brand, oldest first, as parsed rows."""
    first = ANCHOR - timedelta(days=5 * (RUNS - 1))
    out = []
    for i in range(RUNS):
        when = ANCHOR - timedelta(days=5 * (RUNS - 1 - i), hours=i % 3)
        d = seed_demo.write_report(code, when, i, RUNS - 1, first)
        out.append([json.loads(l) for l in (d / "findings.jsonl").read_text().splitlines() if l])
    return out


def test_every_url_is_unresolvable(demo_root):
    """The one property that keeps a demo row from being mistaken for a client's page."""
    for code in ("gl", "rr", "tdrc"):
        for rows in _write_history(code):
            for r in rows:
                assert f"//{code}.demo.invalid/" in r["url"] + "/", r["url"]
                for src in (r["details"] or {}).get("sources") or []:
                    assert ".demo.invalid" in src, src


def test_findings_persist_across_runs(demo_root):
    """A defect present in two runs keeps ONE fingerprint.

    This is what makes the history table, the trend sparkline and triage-carries-across-runs mean
    anything. A per-run RNG seed silently broke it once — every run looked like a fresh set of
    problems and nothing was ever 'still there'.
    """
    history = _write_history("gl")
    early = {r["fingerprint"] for r in history[1] if r["status"] != "resolved"}
    late = {r["fingerprint"] for r in history[-1] if r["status"] != "resolved"}
    assert early, "no findings generated"
    overlap = early & late
    assert len(overlap) >= len(early) * 0.7, (
        f"only {len(overlap)} of {len(early)} findings survived to the newest run")


def test_newest_run_has_something_new_and_something_fixed(demo_root):
    """'What changed' is the screen somebody opens first; an empty one teaches nothing."""
    history = _write_history("gl")
    newest = history[-1]
    assert any(r["status"] == "new" for r in newest), "newest run introduced nothing"
    assert any(r["status"] == "resolved" for r in newest), "newest run fixed nothing"


def test_collapsed_findings_report_the_true_page_count(demo_root):
    """page_count is the real number of affected pages, not the length of the capped sample.

    The product's whole framing — 'one fault, N pages, one fix' — reads page_count. A collapsed
    finding whose count equalled its five stored sources would understate every template fault.
    """
    seen = False
    for rows in _write_history("rr"):
        for r in rows:
            det = r["details"] or {}
            if "page_count" in det:
                seen = True
                assert det["page_count"] >= len(det.get("sources") or [])
                if det["page_count"] > 5:
                    assert det.get("sources_truncated") is True
                    assert len(det["sources"]) == 5
    assert seen, "no collapsed findings were generated"


def test_seed_refuses_a_database_that_already_has_findings(demo_root, monkeypatch, capsys):
    """The only guard between a mistyped DATABASE_URL and real run history."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with Session() as s:
        s.add(Finding(brand_id=1, run_id=1, fingerprint="x", fingerprint_hash="x",
                      url="https://www.gratitudelodge.com/", check="phone", severity="error",
                      issue="a real finding"))
        s.commit()

    monkeypatch.setattr(seed_demo, "SessionLocal", Session)
    monkeypatch.setattr("sys.argv", ["seed_demo.py"])
    assert seed_demo.main() == 1
    err = capsys.readouterr().err
    assert "REFUSED" in err
    with Session() as s:
        assert s.query(Finding).count() == 1, "the guard let rows through"
    engine.dispose()


def test_seed_writes_through_the_real_importer(tmp_path, monkeypatch, demo_root):
    """The demo has to exercise the same loader a real run uses, not a parallel one."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(importer, "REPO", tmp_path)
    monkeypatch.setattr(importer, "REPORTS", tmp_path / "reports")
    with sessionmaker(bind=engine, expire_on_commit=False, future=True)() as s:
        stats = seed_demo.seed(s, runs=3, anchor=ANCHOR, echo=lambda *a: None)
        assert stats["findings"] > 0
        assert stats["runs"] == stats["brands"] * 3 + 2       # +2: the refused and failed runs
        rows = s.query(Finding).all()
        assert rows and all(".demo.invalid" in f.url for f in rows)
        # page_count survives the importer, which is where the UI reads it from.
        assert any(f.page_count > 1 for f in rows)
    engine.dispose()


# ------------------------------------------------------------------ found by adversarial review
# All four below were confirmed defects in 4f932a7, each verified by three independent reviewers.

def test_the_guard_also_refuses_a_database_holding_runs_but_no_findings(demo_root, monkeypatch,
                                                                        capsys):
    """Counting findings alone is not enough, and this is the case that proves it.

    `server/api.py` commits the Run row when a run is QUEUED; findings are only written when the
    crawl finishes. So during the whole of a first ~10h RR crawl the database holds real runs and
    zero findings — and the old guard waved the seeder straight through onto it.
    """
    from server.models import Base, Brand, Run

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with Session() as s:
        s.add(Brand(code="RR", name="Renaissance Recovery", base_url="https://x.invalid"))
        s.flush()
        s.add(Run(brand_id=1, status="running"))       # queued/running: no findings yet
        s.commit()
        assert s.query(Finding).count() == 0, "the premise: findings really is empty"

    monkeypatch.setattr(seed_demo, "SessionLocal", Session)
    monkeypatch.setattr("sys.argv", ["seed_demo.py"])
    assert seed_demo.main() == 1
    assert "REFUSED" in capsys.readouterr().err
    with Session() as s:
        assert s.query(Run).count() == 1, "the real run must be untouched"
    engine.dispose()


def test_a_resolved_row_never_carries_an_unsubstituted_template_token(demo_root):
    """A demo row printing a literal %(host)s would be an example of the very defect the product's
    `empty_slot` check exists to flag — and its URL would not be on *.demo.invalid either."""
    for code in ("dbh", "gl", "rr", "coc"):
        for rows in _write_history(code):
            for r in rows:
                assert "%(host)s" not in (r.get("snippet") or ""), (code, r["fingerprint"])


def test_a_short_history_never_reports_a_fix_for_something_never_seen_open(demo_root):
    """--runs is a documented flag. With runs<=2 the drop window and the resolved tail collapsed
    onto run 0, so the first-ever run claimed a repair no run had ever observed as open."""
    for runs in (1, 2, 3):
        first = ANCHOR - timedelta(days=5 * (runs - 1))
        history = []
        for i in range(runs):
            when = ANCHOR - timedelta(days=5 * (runs - 1 - i), hours=i % 3)
            d = seed_demo.write_report(code := "gl", when, i, runs - 1, first)
            history.append([json.loads(l) for l in
                            (d / "findings.jsonl").read_text().splitlines() if l])
        open_before = set()
        for i, rows in enumerate(history):
            for r in rows:
                if r["status"] == "resolved":
                    assert r["fingerprint"] in open_before, (
                        f"runs={runs}: run {i} reports a fix for a finding never seen open")
            open_before |= {r["fingerprint"] for r in rows if r["status"] != "resolved"}


def test_demo_run_timestamps_are_utc_and_never_in_the_future(tmp_path, monkeypatch, demo_root):
    """`server/jobs.py` computes the cooldown as now() - finished_at. A future timestamp makes the
    next REAL audit sleep for hours before it touches the network."""
    from datetime import timezone

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(importer, "REPO", tmp_path)
    monkeypatch.setattr(importer, "REPORTS", tmp_path / "reports")
    from server.models import Run
    now = datetime.now(timezone.utc)
    with sessionmaker(bind=engine, expire_on_commit=False, future=True)() as s:
        # Anchor at NOW rather than the default 02:10, so the overshoot is deterministic instead
        # of depending on the clock: MHD's newest run is anchor + 20min + 900/8min = anchor+2h12m,
        # which is only in the future if the anchor is late enough in the day. The original test
        # passed at 17:00 and failed at 03:00 — i.e. it only caught the bug inside the documented
        # 2a-5a crawl window, which is precisely when someone would hit it.
        seed_demo.seed(s, runs=6, anchor=datetime.now(timezone.utc).replace(tzinfo=None),
                       echo=lambda *a: None)
        for r in s.query(Run).all():
            for field in ("started_at", "finished_at"):
                v = getattr(r, field)
                if v is None:
                    continue
                v = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
                assert v <= now + timedelta(seconds=5), f"{field} is in the future: {v}"
    engine.dispose()
