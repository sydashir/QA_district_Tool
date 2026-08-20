"""run sample cap + cancellation

Closes the gap between the CLI and the product. The CLI has always been able to say `audit -n 900`;
`POST /api/brands/{code}/runs` took no cap at all, so the one MHD run it accepted queued all 11,439
enumerated pages and blocked every other brand behind it for the duration. MHD's origin 503s under
concurrent requests, so max_concurrency is a locked ceiling of 2 and throughput is ~2.1 pages/min —
a full census is ~54h of wall clock, which is why MHD is documented to run as a labelled PARTIAL
SAMPLE and never as a census.

Three columns:

* `brands.default_sample_size` — the per-brand cap a run falls back to. Only MHD sets it (900).
* `runs.max_pages` — the cap the run actually started with, recorded per-run so a historical row
  cannot later claim a scope it never had when the brand default changes.
* `runs.cancel_requested` — a human asked for this run to stop. The crawl does not abort mid-flight,
  so this is a request on the ROW, which is what lets a cancelled run be settled as `cancelled`
  rather than `failed` even if the worker dies before it can say so.

Additive only: three nullable-or-defaulted columns, no backfill, no data touched. The existing ~98
runs get `max_pages` NULL, which reads correctly as "full census" — none of them were capped through
the product, because the product could not cap them.

`ALTER TABLE` takes an ACCESS EXCLUSIVE lock. `runs` and `brands` are tiny (tens of rows), so the
lock is held for milliseconds, but a crawl holds sessions open for hours: land this when no run is
`running`. See migrations/README.md.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-19

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NULL = this brand's origin can take a full census. An integer means it cannot.
    op.add_column(
        "brands",
        sa.Column("default_sample_size", sa.Integer(), nullable=True),
    )

    # NULL = full census. The value the run STARTED with, not the brand's current default.
    op.add_column(
        "runs",
        sa.Column("max_pages", sa.Integer(), nullable=True),
    )

    # NOT NULL with a DDL default, unlike the Python-side defaults in revision 0001. It has to be a
    # real server_default: the existing rows need a value, and the API sets this flag with a bare
    # UPDATE that never goes through the model's Python default.
    op.add_column(
        "runs",
        sa.Column("cancel_requested", sa.Boolean(),
                  server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("runs", "cancel_requested")
    op.drop_column("runs", "max_pages")
    op.drop_column("brands", "default_sample_size")
