"""URL sampling for the accessibility pass — where the pages come from, and what happens when the
crawl artefact they normally come from is not there.

This exists because of a real loss: GL's `cache/gl/pages.json` and `resume.done.jsonl` both vanished
between 04:09 and 04:43 on 2026-09-03 — cause never established — while its run 128 report sat
complete on disk. The pass then printed "no cached URLs — run an audit first" at a brand that HAD
just been audited, stored nothing, and left GL silently STALE while the eight other brands went
current. A missing crawl artefact must not be able to make a brand quietly skip its checks.
"""
from __future__ import annotations

import json

import pytest

from scripts import accessibility_pass as ap


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "ROOT", tmp_path)
    return tmp_path


def _report(root, brand: str, stamp: str, urls: list[str], run_at: str):
    d = root / "reports" / brand / stamp
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps({"run_at": run_at}))
    (d / "findings.jsonl").write_text(
        "".join(json.dumps({"url": u, "check": "meta"}) + "\n" for u in urls))
    return d


def test_the_report_is_used_when_the_resume_cache_is_gone(repo):
    urls = [f"https://gl.test/drug-rehab/city-{i}" for i in range(20)]
    _report(repo, "gl", "20260903-032946", urls, "2026-09-03T03:29:46")
    got = ap.sample_urls("gl", 10)
    assert len(got) == 10, "a brand with a complete report must not sample zero pages"
    assert set(got) <= set(urls)


def test_the_resume_cache_still_wins_when_it_exists(repo):
    cache = repo / "cache" / "gl"
    cache.mkdir(parents=True)
    (cache / "resume.done.jsonl").write_text(
        "".join(json.dumps({"url": f"https://gl.test/from-cache-{i}", "status": 200}) + "\n"
                for i in range(20)))
    _report(repo, "gl", "20260903-032946", ["https://gl.test/from-report"], "2026-09-03T03:29:46")
    got = ap.sample_urls("gl", 5)
    assert all("from-cache" in u for u in got), "the fallback must not shadow the real cache"


def test_the_newest_report_is_chosen_by_run_at_not_directory_name(repo):
    """A ~21-hour clock skew on 2026-08-10 produced directory names that sort BEFORE older runs."""
    _report(repo, "gl", "20260810-999999", ["https://gl.test/stale"], "2026-08-10T00:00:00")
    _report(repo, "gl", "20260101-000000", ["https://gl.test/fresh"], "2026-09-03T03:29:46")
    got = ap.sample_urls("gl", 5)
    assert got == ["https://gl.test/fresh"], f"picked by name, not by run_at: {got}"


def test_no_report_and_no_cache_still_returns_nothing(repo):
    """The genuine 'never audited' case must stay distinguishable from a lost cache."""
    assert ap.sample_urls("gl", 5) == []


def test_duplicate_urls_across_findings_are_sampled_once(repo):
    """A report has many findings per page; the sample is of PAGES."""
    _report(repo, "gl", "20260903-032946",
            ["https://gl.test/a"] * 5 + ["https://gl.test/b"] * 3, "2026-09-03T03:29:46")
    got = ap.sample_urls("gl", 10)
    assert sorted(got) == ["https://gl.test/a", "https://gl.test/b"]
