"""traffic_imports — how many rows each traffic export actually had

`page_traffic` stores pages, not exports, so it cannot tell a report whether a brand's export was
CUT OFF. That matters for what "not in the traffic data" means:

* GL, RR, CAD, COC and DBH exported exactly 1,000 rows — Google's UI cap. A page missing from those
  exports may be busy; it is simply below the site's 1,000 pages with the most clicks.
* AR (518 rows) and AH (194) exported everything Google had. A page missing from those most likely
  had no search impressions at all.

Measured 2026-09-14: AR leaves 62% of its findings unweighted with an export that was NOT capped,
so blaming the cap for every unweighted finding would have been a false statement in AR's report.

LOCKING: a CREATE TABLE. No lock on `runs` or `findings`.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-14
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "traffic_imports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("brand_id", sa.Integer(), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("period", sa.String(32), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("rows_read", sa.Integer(), nullable=False),
        sa.Column("pages_stored", sa.Integer(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("brand_id", "period", "source", name="uq_traffic_import"),
    )


def downgrade() -> None:
    op.drop_table("traffic_imports")
