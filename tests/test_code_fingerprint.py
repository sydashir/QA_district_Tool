"""The worker must not silently run code older than the repo.

The incident: the api/worker image was built at 13:48Z; the batch landed at 17:26Z; the nine-brand
run was queued at 17:27Z against the stale image. A whole night's crawl ran code committed hours
earlier, and **nothing warned**. Proved afterwards from the data — 0 `redirected_internal`,
0 `collision_slug`, 0 selectors across every run.

The fingerprint hashes SOURCE FILE CONTENT ONLY. It deliberately excludes the config and environment
that `checks_version` also folds in, because those legitimately differ between host and container —
`geodata_gfv_path` is `/Users/...` on the host and `/opt/geodata-services` inside. Comparing full
check-versions across that boundary always reports a difference and so tells you nothing.
"""
from __future__ import annotations

from auditor.checks_version import code_fingerprint


def test_a_fingerprint_is_produced():
    fp = code_fingerprint()
    assert isinstance(fp, str) and len(fp) >= 8


def test_it_is_stable_across_calls():
    assert code_fingerprint() == code_fingerprint()


def test_it_does_not_depend_on_the_geodata_path(monkeypatch):
    """The whole point. Host and container resolve that path differently, and a fingerprint that
    moved with it could never be compared across the boundary it exists to check."""
    before = code_fingerprint()
    monkeypatch.setenv("GEODATA_SERVICES_DIR", "/somewhere/else/entirely")
    assert code_fingerprint() == before


def test_it_takes_no_config_at_all():
    """`checks_version.version()` folds in canonical phones, title bounds and the geodata path —
    all per-brand or per-machine. "Is this the code the repo has?" is none of those, so the function
    cannot accept a config even by accident. Checked on the SIGNATURE, not by grepping the source:
    an earlier version of this test searched the text and failed on the word appearing in a
    docstring, which tests the prose rather than the behaviour."""
    import inspect

    from auditor.checks_version import code_fingerprint
    assert "config" not in inspect.signature(code_fingerprint).parameters


def test_it_changes_when_a_checked_source_file_changes(tmp_path):
    """A fingerprint that did not move on a code edit would be worse than none — it would certify
    a stale image as current."""
    from pathlib import Path

    from auditor import checks_version
    real = Path(checks_version.__file__).resolve().parent / "checks"
    fake = tmp_path / "checks"
    fake.mkdir()
    for f in real.glob("*.py"):
        (fake / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    a = code_fingerprint(checks_dir=fake)
    (fake / "phone.py").write_text("# changed\n" + (fake / "phone.py").read_text(), encoding="utf-8")
    assert code_fingerprint(checks_dir=fake) != a
