"""Backfill Postgres from the report directories already on disk.

Verified before writing this: **85 historical runs, 247,595 finding rows, all 9 brands** sit in
`reports/<brand>/<stamp>/` with `findings.jsonl` + `summary.json`. Importing them is what makes the
product open with months of real trend history instead of an empty database — the dashboard
sparkline and the "what changed" view are useful on day one rather than after a month of runs.

Two things this importer must get right, both learned the hard way this session:

1. **Report directory names are NOT reliably ordered.** A ~21-hour clock skew on 2026-08-10 produced
   directories whose timestamp names sort BEFORE older runs. Runs are ordered by the `run_at` field
   inside `summary.json`, falling back to file mtime — never by directory name.
2. **`rule_changed` is not `resolved`.** Both live in the diff's vanished tail, but only `resolved`
   means a defect went away. Conflating them is how a sheet came to claim "171 fixed" when nothing
   had been fixed. The importer stores `status` verbatim and lets queries decide.

Idempotent: re-importing the same report directory updates rather than duplicates, keyed on
(brand, report_dir).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from auditor.config import load_brand
from .models import Brand, Finding, Page, Run, fp_hash

REPO = Path(__file__).resolve().parent.parent
REPORTS = REPO / "reports"
BRAND_CODES = ["rr", "gl", "ah", "coc", "cad", "ar", "tdrc", "dbh", "mhd"]

# Statuses that are diff bookkeeping, not open defects. Kept in the DB but excluded from
# "open" counts by every query — see api.py::_open_filter.
CARRIED = {"resolved", "rule_changed", "page_unsitemapped", "page_removed"}


def _parse_run_at(summary: dict, fallback: Path) -> datetime:
    raw = summary.get("run_at")
    if raw:
        try:
            return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.fromtimestamp(fallback.stat().st_mtime, tz=timezone.utc)


def ensure_brands(session: Session) -> dict[str, Brand]:
    """Create/refresh brand rows from the real config files — never invented."""
    out: dict[str, Brand] = {}
    for code in BRAND_CODES:
        try:
            cfg = load_brand(code)
        except Exception:
            continue
        brand = session.scalar(select(Brand).where(Brand.code == cfg.brand.upper()))
        if brand is None:
            brand = Brand(code=cfg.brand.upper(), name=cfg.name, base_url=cfg.base_url)
            session.add(brand)
        brand.name = cfg.name
        brand.base_url = cfg.base_url
        brand.sitemap_url = getattr(cfg, "sitemap_url", None)
        brand.enumeration_mode = "urls_file" if getattr(cfg, "urls_file", None) else "sitemap"
        # MHD is deliberately unscheduled: a full census is ~85h, so it is a labelled sample.
        brand.schedule_cron = None if cfg.brand.upper() == "MHD" else "0 2 * * *"
        # ...and for the same reason it caps every run at 900 pages. MHD's origin 503s under
        # concurrent requests, so max_concurrency is a locked ceiling of 2 and throughput is ~2.1
        # pages/min; the full 10,727-page census is ~85h of wall clock, which is why MHD has always
        # been published as a labelled PARTIAL SAMPLE and never as a census. 900 is not a guess: the
        # only MHD runs ever actually published sampled 901 and 353 pages, so this is the largest
        # sample the brand has ever survived. Every other brand stays None (full census).
        brand.default_sample_size = 900 if cfg.brand.upper() == "MHD" else None
        out[cfg.brand.upper()] = brand
    session.flush()
    return out


def _run_dirs(code: str) -> list[Path]:
    base = REPORTS / code
    if not base.is_dir():
        return []
    dirs = [d for d in base.iterdir()
            if d.is_dir() and (d / "findings.jsonl").is_file() and (d / "summary.json").is_file()]
    # ordered by the run's OWN timestamp, not by directory name — see module docstring.
    def key(d: Path):
        try:
            s = json.loads((d / "summary.json").read_text())
        except (OSError, ValueError):
            s = {}
        return _parse_run_at(s, d)
    return sorted(dirs, key=key)


def load_report_into_run(session: Session, brand: Brand, run: Run, d: Path) -> int:
    """Populate an EXISTING run row + its findings from a report directory on disk.

    Shared by the backfill importer AND the worker, deliberately. The worker used to build its own
    row from `run_audit`'s return value and got three things wrong, because that dict has no
    `summary` key at all: it dropped the RESOLVED tail (so "fixed since last run" was always empty
    for runs made through the product), left `report_dir` unset (so sheet export refused), and
    defaulted enumeration_method/partial_sample — which would have mislabelled DBH's static-list
    runs as full sitemap runs. One loader means the two paths cannot drift again.
    """
    summary = json.loads((d / "summary.json").read_text())
    scope = summary.get("audit_scope") or {}
    run.pages_audited = int(summary.get("pages_audited") or 0)
    run.pages_enumerated = int(summary.get("pages_enumerated") or scope.get("union") or 0)
    run.changed_components = summary.get("changed_components")
    run.history_written = bool(summary.get("history_written", True))
    run.enumeration_method = "urls-file" if summary.get("urls_file") else "sitemap"
    run.partial_sample = (bool(summary.get("urls_file"))
                          or not summary.get("history_written", True))
    run.report_dir = str(d.relative_to(REPO)) if str(d).startswith(str(REPO)) else str(d)
    session.flush()

    n = 0
    seen_fp: set[str] = set()
    rows: list[Finding] = []
    with (d / "findings.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            fp = r.get("fingerprint")
            if not fp or fp in seen_fp:
                continue          # UNIQUE(run_id, fingerprint); a report may repeat one
            seen_fp.add(fp)
            det = r.get("details") or {}
            srcs = det.get("sources")
            rows.append(Finding(
                brand_id=brand.id, run_id=run.id, fingerprint=fp, fingerprint_hash=fp_hash(fp),
                url=r.get("url") or "", check=r.get("check") or "",
                severity=str(r.get("severity") or "info"),
                issue=r.get("issue") or "", location=r.get("location"),
                snippet=r.get("snippet"), suggestion=r.get("suggestion"),
                details=det, status=r.get("status"),
                first_seen=r.get("first_seen"), last_seen=r.get("last_seen"),
                page_count=int(det.get("page_count") or (len(srcs) if isinstance(srcs, list) and srcs else 1)),
                sources=srcs if isinstance(srcs, list) else None,
            ))
            n += 1
    session.bulk_save_objects(rows)
    return n


def import_run(session: Session, brand: Brand, d: Path) -> tuple[Run | None, int]:
    """Backfill one report directory as a NEW run. Skips one already imported."""
    rel = str(d.relative_to(REPO))
    if session.scalar(select(Run).where(Run.brand_id == brand.id, Run.report_dir == rel)):
        return None, 0
    try:
        summary = json.loads((d / "summary.json").read_text())
    except (OSError, ValueError):
        return None, 0
    started = _parse_run_at(summary, d)
    run = Run(brand_id=brand.id, started_at=started, finished_at=started, status="ok")
    session.add(run)
    session.flush()
    n = load_report_into_run(session, brand, run, d)
    return run, n


def upsert_pages(session: Session, brand: Brand) -> int:
    """Record every URL we have findings for, with the run that first saw it.

    Derived from findings rather than a separate crawl artefact, because a URL we produced a
    finding for is definitionally a page we audited. Good enough for new-page detection and it
    costs one query.
    """
    from sqlalchemy import func as sqlfunc
    rows = session.execute(
        select(Finding.url, sqlfunc.min(Finding.run_id), sqlfunc.max(Finding.run_id))
        .where(Finding.brand_id == brand.id)
        .group_by(Finding.url)
    ).all()
    existing = {p.url: p for p in session.scalars(select(Page).where(Page.brand_id == brand.id))}
    n = 0
    for url, first_run, last_run in rows:
        p = existing.get(url)
        if p is None:
            session.add(Page(brand_id=brand.id, url=url,
                             first_seen_run_id=first_run, last_seen_run_id=last_run))
            n += 1
        else:
            p.last_seen_run_id = max(p.last_seen_run_id or 0, last_run or 0)
    return n


def import_all(session: Session, echo=print) -> dict:
    brands = ensure_brands(session)
    session.commit()
    stats = {"brands": len(brands), "runs": 0, "findings": 0, "pages": 0, "skipped": 0}
    for code, brand in brands.items():
        dirs = _run_dirs(code.lower())
        got_runs = got_find = 0
        for d in dirs:
            run, n = import_run(session, brand, d)
            if run is None:
                stats["skipped"] += 1
                continue
            got_runs += 1
            got_find += n
            session.commit()
        pages = upsert_pages(session, brand)
        session.commit()
        stats["runs"] += got_runs
        stats["findings"] += got_find
        stats["pages"] += pages
        echo(f"  {code:<5} {got_runs:>3} runs  {got_find:>7,} findings  {pages:>6,} pages")
    return stats


if __name__ == "__main__":
    from .db import SessionLocal, create_all
    create_all()
    print("importing reports/ into postgres ...")
    with SessionLocal() as s:
        st = import_all(s)
    print(f"\ndone: {st['runs']} runs, {st['findings']:,} findings, {st['pages']:,} pages "
          f"({st['skipped']} already present)")
