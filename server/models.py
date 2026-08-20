"""Database schema for the product layer.

Design notes that matter (full reasoning in docs/plans/2026-08-14-product-design.md §3):

* **`findings` is per-run; `triage` is per-fingerprint.** A finding row is an OBSERVATION belonging
  to one run. A triage decision is a human JUDGEMENT about a defect, and it must outlive every run.
  Conflating the two is exactly how triage gets lost on re-run — the failure the Sheets code has to
  defend against by hand today.
* **`details` stays JSONB.** Each check puts different things in it (`page_count`, `sources`,
  `intended`/`actual`, `matched`). Normalising it would couple the schema to the checks and force a
  migration every time a check gains a field.
* **`fingerprint` is the product's real identity**, not the row id. It is stable across runs by
  construction (`auditor/report.py::make_fingerprint`), which is what makes history, triage and
  "what changed" possible at all.
* An `org` column is deliberately absent — single-tenant by decision. Adding one later is a
  migration, not a rewrite, because every table already carries `brand_id`.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import hashlib


def fp_hash(fingerprint: str) -> str:
    """Stable index key for a fingerprint. The fingerprint itself stays authoritative."""
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()

# JSONB on Postgres, plain JSON elsewhere so the test-suite can run on SQLite if it ever needs to.
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(8), unique=True, index=True)   # RR, GL, ...
    name: Mapped[str] = mapped_column(String(120))
    base_url: Mapped[str] = mapped_column(String(255))
    sitemap_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 'sitemap' | 'urls_file' — DBH is urls_file since its 2026-08-08 replatform removed the index.
    enumeration_mode: Mapped[str] = mapped_column(String(20), default="sitemap")
    # None = not scheduled. MHD stays unscheduled: it is a labelled partial sample by design.
    schedule_cron: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # None = this brand's origin can take a full census. An integer means it CANNOT, and every run
    # caps here unless a human passes an explicit max_pages. Only MHD sets it today: its origin 503s
    # under concurrency, so max_concurrency is locked at 2 and throughput is ~2.1 pages/min, making a
    # full 11,439-page census ~54h of wall clock. The CLI has always been able to say `audit -n 900`;
    # the product could not, so one MHD run queued all 11,439 pages and blocked every other brand
    # behind it. This column is what closes that gap.
    default_sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    runs: Mapped[list["Run"]] = relationship(back_populates="brand")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # queued | running | ok | failed | refused | cancelled
    # 'refused' is its own state ON PURPOSE: EmptyAuditRefused means the brand could not be audited
    # (host down, no enumeration), and that must never render as "audited, found nothing".
    # 'cancelled' is separate from 'failed' for the same reason, in the other direction: a human
    # deliberately stopped the run, so it is not our bug to chase — but the brand was NOT fully
    # audited either, and it must never read as a clean result.
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)

    pages_audited: Mapped[int] = mapped_column(Integer, default=0)
    pages_enumerated: Mapped[int] = mapped_column(Integer, default=0)
    # The cap this run actually STARTED with; None = full census. Recorded per-run rather than read
    # back off the brand because the brand's default can change later, and then a historical run
    # would silently claim a scope it never had. pages_enumerated stays the honest denominator: a
    # run with max_pages=900 against 11,439 enumerated pages checked 8% of the site, and the row has
    # to be able to say so months from now.
    max_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checks_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    changed_components: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # False when a --limit sample ran: the run wrote a report but deliberately did NOT move the
    # diff baseline. Surfaced so a small delta is explainable rather than mysterious.
    history_written: Mapped[bool] = mapped_column(Boolean, default=True)
    enumeration_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    partial_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    # A human asked for this run to stop. It is a REQUEST, not the stop itself: the crawl does not
    # abort mid-flight, so a run stays 'running' with this flag set until the worker next stops and
    # reconcile_orphaned_runs settles it as 'cancelled' instead of 'failed'. Kept as its own column
    # so that distinction survives a worker death — the flag is on the row, not in the process.
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_dir: Mapped[str | None] = mapped_column(String(255), nullable=True)

    brand: Mapped[Brand] = relationship(back_populates="runs")
    findings: Mapped[list["Finding"]] = relationship(back_populates="run")

    __table_args__ = (Index("ix_runs_brand_started", "brand_id", "started_at"),)


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)

    # FULL fingerprint, unbounded. Measured on real data: they reach 522 chars because they embed
    # the page URL plus heading text, and there is no ceiling we could safely pick. The INDEX is on
    # a hash instead — Postgres' btree entry limit (~2704 bytes) makes indexing raw text a latent
    # failure waiting for one long URL.
    fingerprint: Mapped[str] = mapped_column(Text)
    fingerprint_hash: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(Text)
    check: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(10), index=True)      # error | warning | info
    issue: Mapped[str] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)

    # From the run-diff, not the checks: new | persisting | resolved | rule_changed |
    # page_unsitemapped | page_removed.
    status: Mapped[str | None] = mapped_column(String(24), index=True, nullable=True)
    first_seen: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_seen: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Promoted out of `details` because the shape-collapse made them first-class: a finding that
    # says "on 524 pages" is the unit the team acts on, and it has to be sortable.
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    sources: Mapped[list | None] = mapped_column(JSONType, nullable=True)

    run: Mapped[Run] = relationship(back_populates="findings")

    __table_args__ = (
        UniqueConstraint("run_id", "fingerprint_hash", name="uq_findings_run_fingerprint"),
        Index("ix_findings_triage_query", "brand_id", "severity", "status"),
        Index("ix_findings_brand_check", "brand_id", "check"),
    )


class Triage(Base):
    """A human decision about a DEFECT. Keyed on fingerprint so it survives every run.

    States mirror what the team already uses in the sheet (`auditor/triage.py`), plus `fixed`:
    open | acknowledged | wontfix | fixed.
    """
    __tablename__ = "triage"

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(Text)
    fingerprint_hash: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(16), default="open")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("brand_id", "fingerprint_hash", name="uq_triage_brand_fingerprint"),
    )


class Page(Base):
    """Every URL we have ever audited, with when we first saw it.

    This is what turns "new page detection" from a feature into a query: the diff engine already
    knows the enumerated set each run, so recording first_seen_run_id per URL is enough.
    """
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    first_seen_run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    last_seen_run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (UniqueConstraint("brand_id", "url", name="uq_pages_brand_url"),)


class User(Base):
    """Stubbed while auth is deferred. `email` is the identity the API will get from
    Cloudflare Access later; nothing else about the model changes when real auth lands."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="member")     # admin | member
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
