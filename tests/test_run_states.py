"""Run states must tell the truth about what happened.

Both cases here were found by the nine-brand acceptance run through the product, not by unit
tests — which is exactly what an acceptance test is for.
"""
from __future__ import annotations

import pytest

from server import jobs


def test_a_brand_that_could_not_be_enumerated_is_REFUSED_not_failed():
    """MHD's degraded origin returned zero pages. Recorded as `failed`, it reads like our bug;
    recorded as `refused`, it reads like their outage — and, critically, it tells the reader the
    brand was NOT given a clean bill of health."""
    src = (jobs.__file__)
    text = open(src, encoding="utf-8").read()
    # the branch exists and chooses refused on zero pages
    assert 'r.status = "refused"' in text
    assert "clean bill of health" in text
    # and `failed` is reserved for a genuine fault, not an empty enumeration
    assert 'r.status, r.error_text = "failed", "the audit ran but wrote no report directory"' in text


def test_orphaned_runs_are_reconciled_on_startup():
    """A worker killed mid-crawl left RR in `running` forever, indistinguishable from a healthy
    six-hour crawl."""
    assert hasattr(jobs, "reconcile_orphaned_runs")
    text = open(jobs.__file__, encoding="utf-8").read()
    # Keys on the WORKER HEARTBEAT, not elapsed time. RR crawls for six hours legitimately, so
    # duration can never separate "still working" from "died" — but a dead worker stops beating.
    # (An earlier version keyed on "no active queue job" and did nothing, because a killed worker
    # leaves its job sitting in `doing` — precisely the state needing detection.)
    assert "procrastinate_workers" in text and "last_heartbeat" in text
    assert "j.status = 'doing'" in text
    from server import worker
    assert "reconcile_orphaned_runs" in open(worker.__file__, encoding="utf-8").read()
