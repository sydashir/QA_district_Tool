#!/usr/bin/env python3
"""Re-date the findings the out-of-crawl passes already wrote as `new`.

WHY THIS EXISTS. `scripts/accessibility_pass.py` and `scripts/render_pass.py` stamped every finding
`status="new"` on every run, because the run-diff has already finished by the time they write and
nothing else was deciding it. 667 rows in the database — 579 accessibility, 88 contrast — and not
one of them anything else. `server/passes.py` fixes that going forward; this fixes what is already
stored, because until it runs the Google Sheet's `new` column (the QA team's work queue) counts
every accessibility and contrast finding as new on every export, forever.

It applies exactly the rule `server/passes.py::attach` applies, so the two cannot drift: a finding
is NEW only when its fingerprint is absent from the most recent EARLIER run carrying that check.
`first_seen` is carried forward from that run for the same reason — re-dating an old defect to
today would make it look like it appeared in this run.

CRAWL FINDINGS ARE NEVER TOUCHED. Their status came from the real run-diff, which is the authority
on them; only the checks the passes write are considered.

    python3 scripts/backfill_pass_status.py            # dry run, prints what would change
    python3 scripts/backfill_pass_status.py --apply
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select                                          # noqa: E402

from server.db import SessionLocal                                     # noqa: E402
from server.models import Brand, Finding, Run                          # noqa: E402
from server.passes import PASS_CHECKS, _previous_run_with              # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = ap.parse_args()

    changed = Counter()
    with SessionLocal() as s:
        for brand in s.scalars(select(Brand).order_by(Brand.code)):
            runs = s.scalars(select(Run).where(Run.brand_id == brand.id)
                             .order_by(Run.started_at)).all()
            for run in runs:
                for check in sorted(PASS_CHECKS):
                    rows = s.scalars(select(Finding).where(
                        Finding.run_id == run.id, Finding.check == check)).all()
                    if not rows:
                        continue
                    prior_id = _previous_run_with(s, brand.id, run, check)
                    history = {} if prior_id is None else {
                        fp: first for fp, first in s.execute(
                            select(Finding.fingerprint, Finding.first_seen)
                            .where(Finding.run_id == prior_id,
                                   Finding.check == check)).all()}
                    for f in rows:
                        want = "persisting" if f.fingerprint in history else "new"
                        first = history.get(f.fingerprint) or f.first_seen
                        if f.status == want and f.first_seen == first:
                            continue
                        changed[f"{brand.code}:{check}"] += 1
                        if args.apply:
                            f.status = want
                            f.first_seen = first
        if args.apply:
            s.commit()

    total = sum(changed.values())
    for key, n in sorted(changed.items()):
        print(f"  {key:28} {n:5} row(s) re-dated")
    verb = "re-dated" if args.apply else "WOULD be re-dated (dry run — pass --apply)"
    print(f"\n  {total} row(s) {verb}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
