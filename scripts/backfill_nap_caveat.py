"""Remove a caveat that is no longer true from findings already stored in the database.

The phone check stamped every NAP-derived finding with "NAP 2026-07-02 snapshot; sheet ID
unverified". The sheet WAS verified live on 2026-08-03 — it reads as "NAP Phone numbers / UTM Codes
/ DBAs" with a "NAP (Current)" tab (CLAUDE.md). So the caveat is wrong, and it sits on the phone
findings, which are the most important thing a client reads.

**Why a backfill rather than waiting for the next run.** `load_report_into_run` uses
`bulk_save_objects` — every run INSERTS new finding rows and never updates existing ones. So fixing
the wording in the check only changes findings produced AFTER the next full crawl, which is about
twelve hours of crawling for a change of wording. Meanwhile the sheet and the client report both
read the LATEST run, which is this one, and it carries the stale text.

**Why it is safe.** The fingerprint is built from (check, class, url, number) and does not include
the suggestion, so rewriting this text cannot change a finding's identity, cannot move it between
new/persisting/resolved, and cannot affect the diff. Only the words a human reads change.

Idempotent: running it twice is a no-op, because the second run matches nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text as sql

from server.db import SessionLocal

OLD_LONG = "(NAP 2026-07-02 snapshot; sheet ID unverified)"
NEW_LONG = "(from your NAP sheet)"
OLD_BARE = "NAP 2026-07-02 snapshot; sheet ID unverified"
NEW_BARE = "NAP sheet, verified 2026-08-03"


def main(apply: bool) -> None:
    with SessionLocal() as s:
        before_sugg = s.execute(sql(
            "SELECT count(*) FROM findings WHERE suggestion LIKE '%sheet ID unverified%'")).scalar()
        before_det = s.execute(sql(
            "SELECT count(*) FROM findings WHERE details->>'source' LIKE '%unverified%'")).scalar()
        print(f"  suggestion column   : {before_sugg:,} row(s) carry the stale caveat")
        print(f"  details->>'source'  : {before_det:,} row(s)")

        if not apply:
            print("\n  DRY RUN — nothing written. Re-run with --apply to make the change.")
            return

        # Longest form first, so the bare form does not eat the parenthesised one.
        s.execute(sql("""UPDATE findings SET suggestion = replace(suggestion, :o, :n)
                         WHERE suggestion LIKE '%sheet ID unverified%'"""),
                  {"o": OLD_LONG, "n": NEW_LONG})
        s.execute(sql("""UPDATE findings SET suggestion = replace(suggestion, :o, :n)
                         WHERE suggestion LIKE '%sheet ID unverified%'"""),
                  {"o": OLD_BARE, "n": NEW_BARE})
        # `details` is already jsonb, and a bound parameter cannot be cast inside to_jsonb() —
        # Postgres rejects `to_jsonb(:n::text)`. Build the JSON string value instead.
        s.execute(sql("""UPDATE findings
                         SET details = jsonb_set(details, '{source}', to_jsonb(CAST(:n AS text)))
                         WHERE details->>'source' LIKE '%unverified%'"""), {"n": NEW_BARE})
        s.commit()

        after_sugg = s.execute(sql(
            "SELECT count(*) FROM findings WHERE suggestion LIKE '%unverified%'")).scalar()
        after_det = s.execute(sql(
            "SELECT count(*) FROM findings WHERE details->>'source' LIKE '%unverified%'")).scalar()
        print(f"\n  after: suggestion {after_sugg:,} remaining, details {after_det:,} remaining")
        if after_sugg or after_det:
            raise SystemExit("  STALE TEXT REMAINS — investigate before trusting the report")
        print("  done — no stale caveat left in the database")


if __name__ == "__main__":
    main("--apply" in sys.argv)
