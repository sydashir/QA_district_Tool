"""Locator measurement — the instrument that decides whether element pictures ship.

Every test here exists because the tool got the answer wrong once. It is the same rule CLAUDE.md
states for tests: verify the instrument before trusting what it tells you. Two of the three defects
below were reported as results before anyone checked them.
"""
from __future__ import annotations

import json

import pytest

from scripts import locator_measure as lm


def _report(tmp_path, rows: list[dict]):
    d = tmp_path / "reports" / "gl" / "20260903-000000"
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps({"run_at": "2026-09-03T00:00:00"}))
    (d / "findings.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return d


def _f(fp: str, url: str, sel: str = "a", status: str = "persisting") -> dict:
    return {"fingerprint": fp, "url": url, "status": status, "details": {"selector": sel}}


def test_occurrence_suffixed_classes_are_counted_not_dropped(tmp_path):
    """`phone.py` emits display_dial_mismatch#1/#2/#3 for repeats on one page and `actions.py` does
    the same for dead_cta. Splitting on ':' alone left those failing the membership test and they
    were dropped with NO message — 22 of 70 measured, and the 22 was quoted as a result."""
    d = _report(tmp_path, [
        _f("phone:display_dial_mismatch:u1", "https://x.invalid/a", "a:nth-of-type(1)"),
        _f("phone:display_dial_mismatch#1:u1", "https://x.invalid/a", "a:nth-of-type(2)"),
        _f("phone:display_dial_mismatch#2:u1", "https://x.invalid/a", "a:nth-of-type(3)"),
        _f("actions:dead_cta#7:u2", "https://x.invalid/b", "a:nth-of-type(1)"),
    ])
    got = lm.load_targets(d, ("display_dial_mismatch", "dead_cta"))
    assert sum(len(v) for v in got.values()) == 4, f"suffixed classes were dropped: {got}"
    classes = [c for v in got.values() for c, _ in v]
    assert classes.count("display_dial_mismatch") == 3
    assert classes.count("dead_cta") == 1


def test_a_resolved_finding_is_not_measured(tmp_path):
    """Its element is GONE by definition; failing to find it is not a locator miss."""
    d = _report(tmp_path, [
        _f("phone:display_dial_mismatch:u1", "https://x.invalid/a", status="resolved"),
        _f("phone:display_dial_mismatch:u2", "https://x.invalid/b", status="new"),
    ])
    got = lm.load_targets(d, ("display_dial_mismatch",))
    assert sum(len(v) for v in got.values()) == 1


def test_a_sample_too_small_gets_no_verdict(capsys):
    """RR really produced `cross_brand_dial n=1 -> 0% -> BELOW FLOOR`, which reads as a finding about
    the locator and is one element's layout. 'We do not know' must never render as an answer."""
    from render.shots import ShotTally

    tally = ShotTally()
    tally.record("cross_brand_dial", "many")
    rc = lm.report_tally(tally, {"cross_brand_dial": 1})
    out = capsys.readouterr().out
    assert "NO VERDICT" in out
    assert "BELOW FLOOR, do not ship" not in out, "a verdict was issued on n=1"
    assert rc != 0, "'unknown' is not a pass"


def test_a_real_sample_still_gets_a_verdict(capsys):
    """The guard must not swallow genuine results — n>=10 is judged as before."""
    from render.shots import ShotTally

    tally = ShotTally()
    for _ in range(9):
        tally.record("dead_cta", "captured")
    tally.record("dead_cta", "none")
    rc = lm.report_tally(tally, {"dead_cta": 10})
    out = capsys.readouterr().out
    assert "SHIPS" in out and "NO VERDICT" not in out
    assert rc == 0


def test_a_class_that_was_never_attempted_fails_loudly(capsys):
    """Every page carrying it failed to load. Silence would read as a clean pass."""
    from render.shots import ShotTally

    tally = ShotTally()
    for _ in range(12):
        tally.record("dead_cta", "captured")
    rc = lm.report_tally(tally, {"dead_cta": 12, "display_dial_mismatch": 14})
    out = capsys.readouterr().out
    assert "NOT MEASURED" in out
    assert rc != 0
