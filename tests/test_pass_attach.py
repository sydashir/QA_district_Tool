"""Findings attached OUTSIDE the crawl still have to answer "is this new?".

The accessibility and render passes run after the crawl, so the run-diff has already finished by the
time they write. Both stamped every finding `status="new"`, on every run, forever — 579 accessibility
and 88 contrast rows in the database, not one of them anything else.

That was invisible while the sheet was rebuilt from `findings.jsonl`, because these findings never
reached it. The moment the export started reading the database (2026-09-23) it became visible and
wrong: TDRC would have published "14 new" when 1 was new and 13 had been there since run 131, and
AH "32 new" against 4. The sheet's `new` column is the QA team's work queue — the same class of
wrongness as the "AH: 171 fixed" incident, in the other direction.

So a pass compares its fingerprints against the most recent EARLIER run that carried that check,
exactly as the crawl's diff does, and carries `first_seen` forward.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auditor.report import Finding as AuditFinding
from auditor.report import Severity
from server.models import Base, Brand, Finding, Run, fp_hash
from server.passes import attach

GL = "https://www.gratitudelodge.com"


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False, future=True)() as s:
        yield s
    engine.dispose()


@pytest.fixture()
def brand(session):
    b = Brand(code="GL", name="Gratitude Lodge", enabled=True, base_url=GL)
    session.add(b); session.flush()
    return b


def _run(session, brand, n) -> Run:
    from datetime import datetime, timedelta
    r = Run(brand_id=brand.id, status="ok", pages_audited=10,
            started_at=datetime(2026, 9, 1) + timedelta(days=n))
    session.add(r); session.flush()
    return r


def _stored(session, run, fp, *, check="accessibility", status="new", first_seen="2026-09-01"):
    session.add(Finding(brand_id=run.brand_id, run_id=run.id, fingerprint=fp,
                        fingerprint_hash=fp_hash(fp), url=f"{GL}/p", check=check,
                        severity="error", issue=fp, details={}, status=status,
                        page_count=1, first_seen=first_seen))
    session.flush()


def _incoming(fp, check="accessibility") -> AuditFinding:
    return AuditFinding(url=f"{GL}/p", check=check, fingerprint=fp, severity=Severity.ERROR,
                        issue=fp, details={})


def _by_fp(session, run):
    return {f.fingerprint: f for f in session.scalars(
        select(Finding).where(Finding.run_id == run.id))}


def test_a_finding_seen_on_the_previous_run_is_persisting_not_new(session, brand):
    old = _run(session, brand, 1)
    _stored(session, old, "acc|link-name", first_seen="2026-08-02")
    new = _run(session, brand, 2)

    attach(session, brand, new, [_incoming("acc|link-name")])
    got = _by_fp(session, new)["acc|link-name"]
    assert got.status == "persisting"
    assert got.first_seen == "2026-08-02", "the date it was FIRST seen, not today"


def test_a_fingerprint_never_seen_before_is_new(session, brand):
    old = _run(session, brand, 1)
    _stored(session, old, "acc|link-name")
    new = _run(session, brand, 2)

    attach(session, brand, new, [_incoming("acc|label")])
    assert _by_fp(session, new)["acc|label"].status == "new"


def test_the_first_time_a_pass_ever_runs_everything_is_new(session, brand):
    """No earlier run carries this check, so there is nothing to have persisted from."""
    old = _run(session, brand, 1)
    _stored(session, old, "phone|x", check="phone")      # a crawl finding, different check
    new = _run(session, brand, 2)

    attach(session, brand, new, [_incoming("acc|link-name")])
    assert _by_fp(session, new)["acc|link-name"].status == "new"


def test_each_check_is_judged_against_its_own_history(session, brand):
    """A run where only the accessibility pass ran must not make every contrast finding look new,
    and vice versa — the two passes run on different cadences."""
    r1 = _run(session, brand, 1)
    _stored(session, r1, "con|button", check="contrast")
    r2 = _run(session, brand, 2)
    _stored(session, r2, "acc|label", check="accessibility")      # only accessibility ran here
    r3 = _run(session, brand, 3)

    attach(session, brand, r3, [_incoming("con|button", "contrast"),
                                _incoming("acc|label", "accessibility")])
    got = _by_fp(session, r3)
    assert got["con|button"].status == "persisting", "contrast's history is r1, not r2"
    assert got["acc|label"].status == "persisting"


def test_the_most_recent_earlier_run_wins_not_the_oldest(session, brand):
    """A finding fixed and then reintroduced is NEW again. Reading the oldest run would call it
    persisting and hide a regression."""
    r1 = _run(session, brand, 1)
    _stored(session, r1, "acc|gone")
    r2 = _run(session, brand, 2)
    _stored(session, r2, "acc|other")            # 'acc|gone' absent here: it was fixed
    r3 = _run(session, brand, 3)

    attach(session, brand, r3, [_incoming("acc|gone")])
    assert _by_fp(session, r3)["acc|gone"].status == "new"


def test_a_fingerprint_already_on_this_run_is_not_written_twice(session, brand):
    """Re-running a pass against the same run must be idempotent — the behaviour the original
    `store` had, and the UNIQUE(run_id, fingerprint) constraint depends on it."""
    run = _run(session, brand, 1)
    _stored(session, run, "acc|link-name", status="new")
    written = attach(session, brand, run, [_incoming("acc|link-name")])
    assert written == 0
    assert len(_by_fp(session, run)) == 1


def test_it_returns_how_many_it_wrote(session, brand):
    run = _run(session, brand, 1)
    assert attach(session, brand, run, [_incoming("a"), _incoming("b")]) == 2


def test_a_later_run_is_never_treated_as_history(session, brand):
    """History means EARLIER. `store` attaches to the latest ok run, but nothing in `attach`
    guarantees that, and a newer run's fingerprints would otherwise decide whether an older run's
    findings are new — reading the future to date the past. Caught by mutation: removing the
    `started_at <` bound left every test passing.
    """
    target = _run(session, brand, 1)
    later = _run(session, brand, 2)
    _stored(session, later, "acc|only-in-the-future")

    attach(session, brand, target, [_incoming("acc|only-in-the-future")])
    assert _by_fp(session, target)["acc|only-in-the-future"].status == "new"
