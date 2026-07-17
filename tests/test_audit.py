"""M2 assembly (``audit.write_run``): annotate findings in-stream (new/persisting), emit the
resolved tail, write JSONL+CSV+summary, persist run history for the next diff. Network-free —
projections and findings are built by hand; RunDiff classification nuance lives in test_diff.
"""
from __future__ import annotations

import csv
import json
from types import SimpleNamespace

from auditor import audit
from auditor.report import Finding, Severity

CFG = SimpleNamespace(canonical_phones=[])
PROJ = [audit.PageProjection(url="https://x/a/", title="Page A")]


def _f(fp, url="https://x/a/", check="meta", issue="i"):
    return Finding(url=url, check=check, fingerprint=fp, severity=Severity.WARNING, issue=issue)


def test_select_sample_seeded_random_reproducible_and_representative():
    urls = [f"https://x/{i}" for i in range(1000)]
    a = audit.select_sample(urls, 50)
    b = audit.select_sample(urls, 50)
    assert a == b                      # reproducible across runs (fixed seed) -> diffable
    assert a != urls[:50]              # NOT first-N -> kills the structural skew
    # representative: spread across the whole range, not clustered at the head
    idxs = [int(u.rsplit("/", 1)[1]) for u in a]
    assert max(idxs) > 800 and min(idxs) < 200


def test_select_sample_head_is_first_n():
    urls = [f"https://x/{i}" for i in range(1000)]
    assert audit.select_sample(urls, 10, head=True) == urls[:10]


def test_select_sample_no_limit_returns_all():
    urls = [f"https://x/{i}" for i in range(5)]
    assert audit.select_sample(urls, None) == urls
    assert audit.select_sample(urls, 99) == urls


def test_canonical_url_strips_trailing_slash():
    assert audit.canonical_url("https://x/page/") == "https://x/page"
    assert audit.canonical_url("https://x/page") == "https://x/page"


def test_identity_is_requested_url_links_resolve_against_final():
    # a page requested at /old/ that redirects to /new/: identity must be the REQUESTED url
    # (so the diff, keyed on requested, stays stable), but relative links resolve against FINAL.
    from auditor.parse import parse_html
    html = '<html><body><a href="child">c</a></body></html>'
    p = parse_html(html, page_url="https://x/old", base_url="https://x/new/")
    assert p.url == "https://x/old"                       # identity = requested
    assert p.links[0].url == "https://x/new/child"        # links resolve against final


def _phone(cls, url, e164, sev=Severity.ERROR):
    return Finding(url=url, check="phone", severity=sev,
                   fingerprint=f"phone:{cls}:{url}:{e164}", issue="retired phone number",
                   location="page", snippet=e164, details={"number": e164, "class": cls})


def test_collapse_phone_site_wide_number_is_one_finding():
    # same retired number on 3 pages -> ONE finding carrying all 3 sources (broken_links shape)
    fs = [_phone("retired", f"https://x/p{i}/", "+18006929850") for i in range(3)]
    fs.append(_f("meta:x", check="meta"))  # non-phone passes through untouched
    out = audit._collapse_phone(fs)
    phone = [f for f in out if f.check == "phone"]
    assert len(phone) == 1
    f = phone[0]
    assert f.fingerprint == "phone:retired:+18006929850"  # identity = number, no url
    assert f.details["sources"] == ["https://x/p0/", "https://x/p1/", "https://x/p2/"]
    assert f.details["page_count"] == 3
    assert any(f.check == "meta" for f in out)


def test_collapse_phone_distinct_numbers_stay_separate():
    fs = [_phone("retired", "https://x/a/", "+18006929850"),
          _phone("unknown", "https://x/a/", "+12125551234", sev=Severity.WARNING)]
    out = audit._collapse_phone(fs)
    assert {f.fingerprint for f in out} == {
        "phone:retired:+18006929850", "phone:unknown:+12125551234"}


def _mh1(url, snippet="A | B"):
    return Finding(url=url, check="heading_structure", severity=Severity.ERROR,
                   fingerprint=f"heading_structure:multi_h1:{url}", issue="multiple <h1>",
                   snippet=snippet, details={"h1_count": 3})


def test_collapse_headings_by_template():
    # 3 pages of the same section+depth template -> ONE finding carrying all sources + a
    # representative H1; a lone page of another template stays per-page.
    fs = [_mh1(f"https://x/drug-rehab/a/b/c/city{i}", f"Rehab near City{i} | Rehab near City{i}")
          for i in range(3)]
    fs.append(_mh1("https://x/contact-us"))  # different template, single page -> stays
    fs.append(_f("meta:x", check="meta"))     # non-heading passes through
    out = audit._collapse_headings(fs)
    collapsed = [f for f in out if f.details.get("class") == "multi_h1" and "template" in f.details]
    assert len(collapsed) == 1
    assert collapsed[0].details["page_count"] == 3
    assert "Rehab near City0" in collapsed[0].snippet          # representative H1 carried
    assert "template fix" in collapsed[0].suggestion.lower()
    # the lone /contact-us multi_h1 kept per-page; meta passed through
    assert any(f.fingerprint == "heading_structure:multi_h1:https://x/contact-us" for f in out)
    assert any(f.check == "meta" for f in out)


def test_collapse_leaves_mismatch_per_page():
    # mismatch is element-specific -> NOT collapsed
    fs = [Finding(url="https://x/a/", check="phone", severity=Severity.ERROR,
                  fingerprint="phone:mismatch:https://x/a/:+18006929850", issue="mismatch")]
    out = audit._collapse_phone(fs)
    assert out[0].fingerprint == "phone:mismatch:https://x/a/:+18006929850"


def _run(findings, tmp_path, now, hist, sub, live={"https://x/a/"}):
    return audit.write_run(
        findings, PROJ, brand="GL", base_url="https://x", now=now, config=CFG,
        live=live, out_dir=tmp_path / sub, history_path=hist)


def test_first_run_all_new_writes_files(tmp_path):
    hist = tmp_path / "history.json"
    fs = [_f("fp1")]
    out = _run(fs, tmp_path, "2026-07-16T01:00:00", hist, "r1")

    assert fs[0].status == "new" and fs[0].first_seen == "2026-07-16T01:00:00"
    assert out["rollup"].by_status["new"] == 1
    assert out["changed"] == []  # first run has no baseline -> nothing "changed" (not all-of-it)
    assert (tmp_path / "r1" / "findings.jsonl").exists()
    assert (tmp_path / "r1" / "summary.json").exists()
    assert hist.exists()

    # CSV first_seen is filled by the annotate step (writers test proved it's blank otherwise)
    row = next(csv.DictReader((tmp_path / "r1" / "findings.csv").open()))
    assert row["status"] == "new" and row["first_seen"] == "2026-07-16T01:00:00"


def test_second_run_persists_and_resolves(tmp_path):
    hist = tmp_path / "history.json"
    _run([_f("fp1"), _f("fp2")], tmp_path, "2026-01-01T00:00:00", hist, "r1")

    # run 2: fp1 still there (page audited + live), fp2 gone -> a genuine resolve
    fs2 = [_f("fp1")]
    out = _run(fs2, tmp_path, "2026-02-02T00:00:00", hist, "r2")

    assert fs2[0].status == "persisting" and fs2[0].first_seen == "2026-01-01T00:00:00"
    resolved = out["resolved"]
    assert [r.fingerprint for r in resolved] == ["fp2"]
    assert resolved[0].status == "resolved"
    assert out["rollup"].by_status.get("resolved") == 1

    # resolved finding lands in the same report stream (tail), carrying its old last_seen
    rows = [json.loads(l) for l in (tmp_path / "r2" / "findings.jsonl").read_text().splitlines()]
    statuses = {r["fingerprint"]: r["status"] for r in rows}
    assert statuses == {"fp1": "persisting", "fp2": "resolved"}


def test_projection_cache_persists_light_schema_no_findings(tmp_path, monkeypatch):
    # B5: cache stores change-detection keys + barrier inputs, never intrinsic_findings.
    from auditor import crawl as C
    monkeypatch.setattr(C, "CACHE_DIR", tmp_path)
    projs = [audit.PageProjection(
        url="https://x/a", content_hash="h1", last_modified="Fri, 17 Jul 2026 00:00:00 GMT",
        status=200, final_url="https://x/a/", link_urls=["https://t/1"], title="A",
        meta_description="d", h1_text="H",
        intrinsic_findings=[_f("should-not-be-cached")])]
    path, n = audit.write_projection_cache("GL", projs)
    entry = json.loads(path.read_text())["https://x/a"]
    assert entry["last_modified"] == "Fri, 17 Jul 2026 00:00:00 GMT"
    assert entry["link_urls"] == ["https://t/1"] and entry["h1_text"] == "H"
    assert "intrinsic_findings" not in entry and "findings" not in entry  # the trap avoided


def test_fetchresult_carries_last_modified():
    from auditor.crawl import FetchResult
    r = FetchResult("u", 200, "u", "html", None, last_modified="Fri, 17 Jul 2026 00:00:00 GMT")
    assert r.last_modified == "Fri, 17 Jul 2026 00:00:00 GMT" and r.ok


def test_write_is_atomic_no_partial_residue(tmp_path):
    # on success the report is promoted from <stamp>.partial to <stamp>, leaving no partial dir
    # or .tmp history behind (a crash mid-write would leave the .partial, never a real report).
    hist = tmp_path / "history.json"
    out = tmp_path / "20260101-000000"
    audit.write_run([_f("fp1")], PROJ, brand="GL", base_url="https://x",
                    now="2026-07-16T01:00:00", config=CFG, live=None, out_dir=out,
                    history_path=hist)
    assert out.exists() and (out / "findings.jsonl").exists()
    assert not out.with_name(out.name + ".partial").exists()
    assert not hist.with_suffix(".json.tmp").exists()


def test_extra_meta_reaches_summary(tmp_path):
    # the phone-scope caveat (and any run meta) must land in summary.json for the client
    out = audit.write_run(
        [_f("fp1")], PROJ, brand="GL", base_url="https://x", now="2026-07-16T01:00:00",
        config=CFG, live=None, out_dir=tmp_path / "r1", history_path=tmp_path / "h.json",
        extra_meta={"phone_scope_caveat": "validated brand-wide, not per-page"})
    s = json.loads((tmp_path / "r1" / "summary.json").read_text())
    assert s["phone_scope_caveat"] == "validated brand-wide, not per-page"


def test_history_only_keeps_seen_fingerprints(tmp_path):
    hist = tmp_path / "history.json"
    _run([_f("fp1"), _f("fp2")], tmp_path, "2026-01-01T00:00:00", hist, "r1")
    _run([_f("fp1")], tmp_path, "2026-02-02T00:00:00", hist, "r2")
    h = json.loads(hist.read_text())
    # fp2 resolved this run -> drops from history (reappearing later would be 'new' again)
    assert set(h["findings"]) == {"fp1"}
    assert "components" in h
