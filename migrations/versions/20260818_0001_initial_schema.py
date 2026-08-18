"""initial schema

Reproduces exactly what `Base.metadata.create_all()` had already built, verified column-by-column
and index-by-index against the live database on 2026-08-18 (pg_indexes + pg_constraint), not
inferred from the models alone.

**The existing database must be STAMPED, not upgraded** — it already has every one of these tables
and ~263k findings in them. See `migrations/README.md`.

Revision ID: 0001
Revises:
Create Date: 2026-08-18

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Restated rather than imported from server.models on purpose: a migration is a frozen snapshot of
# the schema at a point in time, and importing a live app definition would make old revisions change
# meaning whenever the models do. Same variant as models.JSONType — JSONB on Postgres, JSON
# elsewhere so the file still applies against SQLite.
JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

# `enabled`, `status`, `page_count` and friends are NOT NULL with no DDL default: their defaults are
# Python-side (`default=`, not `server_default=`), so SQLAlchemy supplies them on INSERT and the
# column itself carries none. Reproduced as-is — adding server defaults here would make the
# migrated schema quietly different from the one in production.


def upgrade() -> None:
    op.create_table(
        "brands",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=8), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("base_url", sa.String(length=255), nullable=False),
        sa.Column("sitemap_url", sa.String(length=255), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        # 'sitemap' | 'urls_file' — DBH is urls_file since its replatform removed the page index.
        sa.Column("enumeration_mode", sa.String(length=20), nullable=False),
        sa.Column("schedule_cron", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_brands_code", "brands", ["code"], unique=True)

    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("brand_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        # queued | running | ok | failed | refused. `refused` means the brand COULD NOT be audited
        # and must never render as "audited, found nothing" — see server/models.py.
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("pages_audited", sa.Integer(), nullable=False),
        sa.Column("pages_enumerated", sa.Integer(), nullable=False),
        sa.Column("checks_version", sa.String(length=64), nullable=True),
        sa.Column("changed_components", JSONType, nullable=True),
        sa.Column("history_written", sa.Boolean(), nullable=False),
        sa.Column("enumeration_method", sa.String(length=32), nullable=True),
        sa.Column("partial_sample", sa.Boolean(), nullable=False),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("report_dir", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["brand_id"], ["brands.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runs_brand_id", "runs", ["brand_id"], unique=False)
    op.create_index("ix_runs_status", "runs", ["status"], unique=False)
    # Serves the dashboard's "latest run per brand" and the per-brand history list.
    op.create_index("ix_runs_brand_started", "runs", ["brand_id", "started_at"], unique=False)

    op.create_table(
        "findings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("brand_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        # Unbounded Text: real fingerprints reach 522 chars because they embed the page URL plus
        # heading text. The INDEX is on the sha256 instead — Postgres' ~2704-byte btree entry limit
        # makes indexing the raw text a latent failure waiting for one long URL.
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("fingerprint_hash", sa.String(length=64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        # "check" is a reserved SQL keyword; SQLAlchemy quotes it on emit.
        sa.Column("check", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("issue", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("suggestion", sa.Text(), nullable=True),
        sa.Column("details", JSONType, nullable=False),
        # From the run-diff: new | persisting | resolved | rule_changed | page_unsitemapped |
        # page_removed. OPEN is only (new, persisting).
        sa.Column("status", sa.String(length=24), nullable=True),
        sa.Column("first_seen", sa.String(length=32), nullable=True),
        sa.Column("last_seen", sa.String(length=32), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("sources", JSONType, nullable=True),
        sa.ForeignKeyConstraint(["brand_id"], ["brands.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        # The importer's idempotency guarantee: re-importing a run cannot duplicate its findings.
        sa.UniqueConstraint("run_id", "fingerprint_hash", name="uq_findings_run_fingerprint"),
    )
    op.create_index("ix_findings_brand_id", "findings", ["brand_id"], unique=False)
    op.create_index("ix_findings_run_id", "findings", ["run_id"], unique=False)
    op.create_index("ix_findings_fingerprint_hash", "findings", ["fingerprint_hash"], unique=False)
    op.create_index("ix_findings_check", "findings", ["check"], unique=False)
    op.create_index("ix_findings_severity", "findings", ["severity"], unique=False)
    op.create_index("ix_findings_status", "findings", ["status"], unique=False)
    # The triage screen's actual filter: one brand, by severity, open-only.
    op.create_index("ix_findings_triage_query", "findings",
                    ["brand_id", "severity", "status"], unique=False)
    op.create_index("ix_findings_brand_check", "findings", ["brand_id", "check"], unique=False)

    op.create_table(
        "triage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("brand_id", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("fingerprint_hash", sa.String(length=64), nullable=False),
        # open | acknowledged | wontfix | fixed
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        # `onupdate` is Python-side, so the DDL carries only the insert default.
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["brand_id"], ["brands.id"]),
        sa.PrimaryKeyConstraint("id"),
        # One decision per defect per brand — this is what makes triage survive a re-run.
        sa.UniqueConstraint("brand_id", "fingerprint_hash", name="uq_triage_brand_fingerprint"),
    )
    op.create_index("ix_triage_brand_id", "triage", ["brand_id"], unique=False)
    op.create_index("ix_triage_fingerprint_hash", "triage", ["fingerprint_hash"], unique=False)

    op.create_table(
        "pages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("brand_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("first_seen_run_id", sa.Integer(), nullable=True),
        sa.Column("last_seen_run_id", sa.Integer(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["brand_id"], ["brands.id"]),
        sa.ForeignKeyConstraint(["first_seen_run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["last_seen_run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("brand_id", "url", name="uq_pages_brand_url"),
    )
    op.create_index("ix_pages_brand_id", "pages", ["brand_id"], unique=False)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),   # admin | member
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)


def downgrade() -> None:
    # Reverse dependency order: pages and findings reference runs, everything references brands.
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    op.drop_index("ix_pages_brand_id", table_name="pages")
    op.drop_table("pages")

    op.drop_index("ix_triage_fingerprint_hash", table_name="triage")
    op.drop_index("ix_triage_brand_id", table_name="triage")
    op.drop_table("triage")

    op.drop_index("ix_findings_brand_check", table_name="findings")
    op.drop_index("ix_findings_triage_query", table_name="findings")
    op.drop_index("ix_findings_status", table_name="findings")
    op.drop_index("ix_findings_severity", table_name="findings")
    op.drop_index("ix_findings_check", table_name="findings")
    op.drop_index("ix_findings_fingerprint_hash", table_name="findings")
    op.drop_index("ix_findings_run_id", table_name="findings")
    op.drop_index("ix_findings_brand_id", table_name="findings")
    op.drop_table("findings")

    op.drop_index("ix_runs_brand_started", table_name="runs")
    op.drop_index("ix_runs_status", table_name="runs")
    op.drop_index("ix_runs_brand_id", table_name="runs")
    op.drop_table("runs")

    op.drop_index("ix_brands_code", table_name="brands")
    op.drop_table("brands")
