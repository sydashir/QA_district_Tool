"""page_redirects — audited URLs that land on a different page of the same site

Google reports search traffic under the page a visitor lands on. The audit keys a finding on the URL
it requested. When the two differ, the finding reads "not in the traffic data" while its destination
is in it. Measured 2026-09-15 from every brand's crawl cache: 313 same-site redirects carrying 1,047
findings, 534 of them unmatched although 513 could be weighted (RR 356, GL 98, COC 22, AR 19, CAD 18).

A separate table rather than a column on `pages`: the map is rebuilt from the latest crawl at every
traffic import, and `pages` does not store where a URL landed.

LOCKING: a CREATE TABLE. No lock on `runs` or `findings`.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "page_redirects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("brand_id", sa.Integer(), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("url_key", sa.Text(), nullable=False),
        sa.Column("final_key", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("brand_id", "url_key", name="uq_page_redirect"),
    )


def downgrade() -> None:
    op.drop_table("page_redirects")
