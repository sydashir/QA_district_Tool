"""page_traffic — per-URL search traffic, so findings can be ranked by who is actually affected

Today every finding reads as equally important. A dead button on a page with 40,000 monthly visits
and one on a page nobody visits are the same row. This table is what turns a defect list into a
priority list.

DESIGN NOTES THAT MATTER MORE THAN THE SCHEMA:

* **Separate table, not a column on `findings`.** A finding is an observation belonging to one run
  and never changes; traffic for the same URL changes weekly. Joining at read time means an old
  run's report re-renders with current traffic — correct, because "which should I fix first" is
  asked now, not at crawl time.

* **`url_key`, not `url`.** Lowercased host + path, no scheme, no trailing slash. The live sites
  serve the SLASHED form (96.7-100% of `final_url` per brand) while `canonical_url()` strips it, so
  raw equality between Google's URLs and ours matches ~0.2%. One normalisation on both sides fixes
  essentially all of it. `www` is deliberately NOT stripped: GL and RR are 100% `www` and the other
  seven are 100% bare, so collapsing it would merge two brands' notions of a host.

* **`value_state`.** Google's CSV export writes MISSING values as zeros — its own documentation says
  values shown as `~` or `-` in the report "will be zeros in the downloaded data". A zero that means
  "we do not know" must never rank a finding as unimportant, so every CSV row lands `unknown` and
  only the API can produce `measured`. This column is the difference between a priority list and a
  confidently wrong one.

LOCKING: this is a CREATE TABLE. It takes no lock on `runs` or `findings`, so unlike migration 0002
it is safe to land while a crawl is running — which it was.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "page_traffic",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("brand_id", sa.Integer(), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("url_key", sa.Text(), nullable=False),
        sa.Column("period", sa.String(32), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=True),
        sa.Column("clicks", sa.Integer(), nullable=True),
        sa.Column("position", sa.Float(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("value_state", sa.String(12), nullable=False, server_default="measured"),
        sa.Column("fetched_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("brand_id", "url_key", "period", "source",
                            name="uq_traffic_page_period"),
    )
    op.create_index("ix_page_traffic_brand_id", "page_traffic", ["brand_id"])
    # The join this table exists for is (brand, url_key) -> traffic, done once per finding at report
    # time. Indexed together rather than separately because neither half is selective alone.
    op.create_index("ix_page_traffic_lookup", "page_traffic", ["brand_id", "url_key"])


def downgrade() -> None:
    op.drop_index("ix_page_traffic_lookup", table_name="page_traffic")
    op.drop_index("ix_page_traffic_brand_id", table_name="page_traffic")
    op.drop_table("page_traffic")
