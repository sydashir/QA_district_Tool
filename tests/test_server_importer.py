"""Importer tests — the loader that turns a report on disk into rows a human reads.

This is the same function the WORKER uses (`load_report_into_run`), deliberately: a run made
through the product has to end up byte-for-byte the same shape as a backfilled one. So every
bug fixed here is fixed on both paths at once, and every bug NOT caught here ships to both.

Runs against throwaway SQLite and a tmp_path report tree. `importer.REPO`/`REPORTS` are
monkeypatched so nothing reads the real `reports/` directory — a nine-brand crawl is writing
into it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from server import importer
from server.models import Base, Brand, Finding, Page, Run, fp_hash


@pytest.fixture()
def session(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(importer, "REPO", tmp_path)
    monkeypatch.setattr(importer, "REPORTS", tmp_path / "reports")
    with sessionmaker(bind=engine, expire_on_commit=False, future=True)() as s:
        yield s
    engine.dispose()


@pytest.fixture()
def brand(session):
    b = Brand(code="GL", name="Gratitude Lodge", base_url="https://www.gratitudelodge.com")
    session.add(b)
    session.flush()
    return b


def write_report(root: Path, code: str, stamp: str, *, findings: list[dict],
                 **summary) -> Path:
    """A report directory in the real on-disk shape: summary.json + findings.jsonl."""
    d = root / "reports" / code / stamp
    d.mkdir(parents=True, exist_ok=True)
    summary.setdefault("run_at", "2026-08-14T02:00:00")
    summary.setdefault("pages_audited", 161)
    (d / "summary.json").write_text(json.dumps(summary))
    (d / "findings.jsonl").write_text(
        "".join(json.dumps(f) + "\n" for f in findings), encoding="utf-8")
    return d


def finding(fp: str, **kw) -> dict:
    base = {"fingerprint": fp, "url": "https://www.gratitudelodge.com/detox",
            "check": "phone", "severity": "error", "issue": "tel: dials another brand",
            "status": "new", "details": {}}
    base.update(kw)
    return base


# --------------------------------------------------------------------------- statuses
def test_rule_changed_is_stored_verbatim_and_never_folded_into_resolved(session, brand,
                                                                       tmp_path):
    """INCIDENT: `rule_changed` and `resolved` both live in the diff's vanished tail, but only
    `resolved` means a defect went away. Conflating them is how a sheet came to claim "171
    fixed" when nothing had been fixed.

    The importer's job is to store the status verbatim and let queries decide — the moment it
    starts interpreting, the interpretation is baked into history and cannot be corrected.
    """
    d = write_report(tmp_path, "gl", "2026-08-14T020000", findings=[
        finding("fp-new", status="new"),
        finding("fp-persisting", status="persisting"),
        finding("fp-resolved", status="resolved"),
        finding("fp-rule-changed", status="rule_changed"),
        finding("fp-unsitemapped", status="page_unsitemapped"),
        finding("fp-removed", status="page_removed"),
    ])
    run, n = importer.import_run(session, brand, d)
    session.commit()

    assert n == 6
    stored = {f.fingerprint: f.status for f in session.scalars(select(Finding))}
    assert stored == {"fp-new": "new", "fp-persisting": "persisting",
                      "fp-resolved": "resolved", "fp-rule-changed": "rule_changed",
                      "fp-unsitemapped": "page_unsitemapped", "fp-removed": "page_removed"}


def test_the_resolved_tail_is_loaded_not_dropped(session, brand, tmp_path):
    """The worker used to build its run from `run_audit`'s return value, which has no summary
    of the diff at all — so "fixed since last run" was permanently empty for every run made
    through the product. Both paths now go through this loader; this is what holds that shut.
    """
    d = write_report(tmp_path, "gl", "2026-08-14T020000", findings=[
        finding("fp-open", status="new"),
        finding("fp-gone", status="resolved"),
    ])
    importer.import_run(session, brand, d)
    session.commit()
    assert session.scalar(select(Finding).where(Finding.status == "resolved")) is not None


# --------------------------------------------------------------------------- run ordering
def test_runs_are_ordered_by_their_own_timestamp_not_the_directory_name(session, brand,
                                                                       tmp_path):
    """INCIDENT: a ~21-hour clock skew on 2026-08-10 produced directories whose timestamp
    NAMES sort before older runs. Sorting by name would import them in the wrong order, and
    every diff downstream ("what changed since last run") inherits that ordering.

    Here the two directories' names sort the opposite way from their real `run_at`.
    """
    write_report(tmp_path, "gl", "2026-08-10T010000", findings=[],   # the skewed one
                 run_at="2026-08-09T04:00:00")
    write_report(tmp_path, "gl", "2026-08-09T230000", findings=[],
                 run_at="2026-08-10T05:00:00")

    ordered = [d.name for d in importer._run_dirs("gl")]
    assert ordered == ["2026-08-10T010000", "2026-08-09T230000"]
    assert ordered != sorted(ordered), "name order and true order really do disagree here"


def test_a_directory_missing_either_file_is_not_a_run(session, tmp_path):
    """A crawl that is still running has a half-written directory. Importing it would record a
    truncated audit as a complete one — a false all-clear built out of an incomplete file."""
    (tmp_path / "reports" / "gl" / "half-written").mkdir(parents=True)
    (tmp_path / "reports" / "gl" / "half-written" / "findings.jsonl").write_text("")
    write_report(tmp_path, "gl", "complete", findings=[finding("fp-1")])
    assert [d.name for d in importer._run_dirs("gl")] == ["complete"]


def test_run_at_falls_back_to_mtime_when_the_summary_cannot_be_parsed(tmp_path):
    """Never guess and never crash: an unparseable timestamp becomes the file's mtime, which
    is wrong by minutes rather than absent entirely."""
    d = write_report(tmp_path, "gl", "x", findings=[], run_at="not-a-timestamp")
    got = importer._parse_run_at(json.loads((d / "summary.json").read_text()), d)
    assert abs((got - datetime.now(timezone.utc)).total_seconds()) < 120
    assert importer._parse_run_at({}, d).tzinfo is not None


# --------------------------------------------------------------------------- idempotency
def test_reimporting_the_same_report_does_not_duplicate_it(session, brand, tmp_path):
    """The backfill is re-run by hand whenever new reports land. If it were not idempotent,
    every re-run would double the finding counts the dashboard reports."""
    d = write_report(tmp_path, "gl", "2026-08-14T020000",
                     findings=[finding("fp-1"), finding("fp-2")])
    run, n = importer.import_run(session, brand, d)
    session.commit()
    assert run is not None and n == 2

    again, n2 = importer.import_run(session, brand, d)
    session.commit()
    assert (again, n2) == (None, 0), "already imported"
    assert session.scalar(select(func.count(Finding.id))) == 2
    assert len(session.scalars(select(Run)).all()) == 1


def test_a_fingerprint_repeated_within_one_report_is_stored_once(session, brand, tmp_path):
    """UNIQUE(run_id, fingerprint_hash) is the schema's guarantee that a run cannot report the
    same defect twice. A report CAN repeat one, so the loader must collapse it rather than
    dying on an IntegrityError halfway through a 30,000-line file."""
    d = write_report(tmp_path, "gl", "2026-08-14T020000", findings=[
        finding("fp-dup", issue="first"),
        finding("fp-dup", issue="second"),
        finding("fp-other"),
    ])
    run, n = importer.import_run(session, brand, d)
    session.commit()
    assert n == 2
    rows = session.scalars(select(Finding).where(Finding.fingerprint == "fp-dup")).all()
    assert len(rows) == 1 and rows[0].issue == "first", "first wins, deterministically"


def test_a_malformed_line_is_skipped_rather_than_aborting_the_import(session, brand,
                                                                    tmp_path):
    """One bad line must not cost the other 29,999. Reports are appended to during a crawl."""
    d = write_report(tmp_path, "gl", "2026-08-14T020000", findings=[finding("fp-1")])
    with (d / "findings.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{not json\n\n")
        fh.write(json.dumps(finding("fp-2")) + "\n")
        fh.write(json.dumps({"url": "no fingerprint here"}) + "\n")
    run, n = importer.import_run(session, brand, d)
    session.commit()
    assert n == 2, "blank, unparseable and fingerprint-less lines are all skipped"


# --------------------------------------------------------------------------- shape
def test_page_count_prefers_details_then_sources_then_one(session, brand, tmp_path):
    """A finding that says "on 524 pages" is the unit the team acts on, and it drives the sort
    order of the whole queue — so its fallback chain has to be explicit, not incidental."""
    d = write_report(tmp_path, "gl", "2026-08-14T020000", findings=[
        finding("fp-explicit", details={"page_count": 524}),
        finding("fp-sources", details={"sources": ["/a", "/b", "/c"]}),
        finding("fp-bare"),
    ])
    importer.import_run(session, brand, d)
    session.commit()
    got = {f.fingerprint: (f.page_count, f.sources) for f in session.scalars(select(Finding))}
    assert got["fp-explicit"][0] == 524
    assert got["fp-sources"] == (3, ["/a", "/b", "/c"])
    assert got["fp-bare"] == (1, None)


def test_report_dir_is_recorded_so_the_sheet_export_can_find_it(session, brand, tmp_path):
    """Sheet export refuses a run with no `report_dir`. The worker used to leave it unset, so
    every run made through the product was un-exportable — stored RELATIVE to the repo so the
    path survives the repo being moved."""
    d = write_report(tmp_path, "gl", "2026-08-14T020000", findings=[finding("fp-1")])
    run, _ = importer.import_run(session, brand, d)
    session.commit()
    assert run.report_dir == "reports/gl/2026-08-14T020000"
    assert not Path(run.report_dir).is_absolute()


def test_a_urls_file_run_is_labelled_a_partial_sample(session, brand, tmp_path):
    """DBH is enumerated from a static list since its 2026-08-08 replatform removed the
    sitemap — a static list CANNOT discover pages added later. Defaulting this to a full
    sitemap run would present an incomplete audit as a census."""
    d = write_report(tmp_path, "gl", "2026-08-14T020000", findings=[finding("fp-1")],
                     urls_file="config/urls/dbh.txt")
    run, _ = importer.import_run(session, brand, d)
    session.commit()
    assert run.enumeration_method == "urls-file"
    assert run.partial_sample is True


def test_a_sampled_run_is_flagged_because_it_did_not_move_the_baseline(session, brand,
                                                                      tmp_path):
    """A `-n` sample writes a report but deliberately does not move the diff baseline. The
    flag is what makes the resulting small delta explainable instead of mysterious — a sampled
    run once left a baseline that made the next full run report 3,362 "new" findings that were
    not new at all."""
    d = write_report(tmp_path, "gl", "2026-08-14T020000", findings=[finding("fp-1")],
                     history_written=False)
    run, _ = importer.import_run(session, brand, d)
    session.commit()
    assert run.history_written is False
    assert run.partial_sample is True


def test_a_full_sitemap_run_is_not_flagged(session, brand, tmp_path):
    """The negative case, so the two flags above are not simply always true."""
    d = write_report(tmp_path, "gl", "2026-08-14T020000", findings=[finding("fp-1")],
                     pages_audited=161, pages_enumerated=161)
    run, _ = importer.import_run(session, brand, d)
    session.commit()
    assert (run.enumeration_method, run.partial_sample, run.history_written) == \
           ("sitemap", False, True)
    assert (run.pages_audited, run.pages_enumerated) == (161, 161)


def test_fingerprint_hash_is_what_links_a_finding_to_its_triage(session, brand, tmp_path):
    """Fingerprints reach 522 chars on real data (they embed the URL plus heading text), which
    is why the INDEX is on a hash. If the importer computed it differently from the API, every
    triage decision would fail to join and silently read as untriaged."""
    long_fp = "GL|heading_structure|" + "x" * 600
    d = write_report(tmp_path, "gl", "2026-08-14T020000", findings=[finding(long_fp)])
    importer.import_run(session, brand, d)
    session.commit()
    f = session.scalar(select(Finding))
    assert f.fingerprint == long_fp, "the full fingerprint is stored, never truncated"
    assert f.fingerprint_hash == fp_hash(long_fp)


# --------------------------------------------------------------------------- pages
def test_pages_record_the_run_that_first_saw_them(session, brand, tmp_path):
    """New-page detection: a brand-new page carrying a broken CTA is worse than an old one, so
    "first seen in the latest run" has to be a query rather than a guess."""
    d1 = write_report(tmp_path, "gl", "2026-08-13T020000", run_at="2026-08-13T02:00:00",
                      findings=[finding("fp-1", url="https://www.gratitudelodge.com/old")])
    run1, _ = importer.import_run(session, brand, d1)
    session.commit()
    importer.upsert_pages(session, brand)
    session.commit()

    d2 = write_report(tmp_path, "gl", "2026-08-14T020000", run_at="2026-08-14T02:00:00",
                      findings=[finding("fp-1", url="https://www.gratitudelodge.com/old"),
                                finding("fp-2", url="https://www.gratitudelodge.com/brand-new")])
    run2, _ = importer.import_run(session, brand, d2)
    session.commit()
    importer.upsert_pages(session, brand)
    session.commit()

    pages = {p.url: p for p in session.scalars(select(Page))}
    assert len(pages) == 2
    old = pages["https://www.gratitudelodge.com/old"]
    assert old.first_seen_run_id == run1.id, "an old page does not become new again"
    assert old.last_seen_run_id == run2.id
    new = pages["https://www.gratitudelodge.com/brand-new"]
    assert new.first_seen_run_id == run2.id


def test_ensure_brands_never_claims_a_schedule(session):
    """A brand row must not come out of an import claiming to be audited automatically.

    `brands.schedule_cron` is the only thing the dashboard reads to decide whether to print "audits
    run by themselves overnight". The importer used to stamp every non-MHD brand with `0 2 * * *`
    on every import, so that sentence appeared on installs where no timer existed. A false one is
    worse than silence: a QA reader who believes it does not start a run, thinking one already
    happened, and the sites go unaudited while the screen says they are covered.
    """
    brands = importer.ensure_brands(session)
    assert brands, "no brand config was loaded"
    claimed = {code for code, b in brands.items() if b.schedule_cron}
    assert not claimed, f"import invented a schedule for {sorted(claimed)}"


def test_ensure_brands_does_not_wipe_a_real_schedule(session):
    """...and once a timer genuinely exists, a later import must leave its cron alone."""
    first = importer.ensure_brands(session)
    session.commit()
    code = next(iter(first))
    session.scalar(select(Brand).where(Brand.code == code)).schedule_cron = "0 2 * * *"
    session.commit()

    importer.ensure_brands(session)
    session.commit()
    assert session.scalar(select(Brand).where(Brand.code == code)).schedule_cron == "0 2 * * *"
