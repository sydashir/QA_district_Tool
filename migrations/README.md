# migrations — Alembic

Owns the Postgres schema for the product layer (`server/models.py`). Nothing in `auditor/` is
involved; the audit engine reads and writes files on disk, not this database.

Run every command from the repo root — `alembic.ini` lives there and `env.py` needs the repo root
on `sys.path` to import `server.models`.

**Invoke it as `python3 -m alembic`, not bare `alembic`.** This machine has several Pythons and the
`alembic` console script on PATH belongs to a 3.12 framework install with no `psycopg`, so the bare
command dies on `ModuleNotFoundError: No module named 'psycopg'` inside `env.py`. `python3 -m` uses
the same interpreter as `python3 -m server.worker` and `python3 -m server.importer`, which is the
one that actually has the driver.

---

## READ THIS FIRST: the existing database must be STAMPED, not upgraded

The database that is running today was created by `Base.metadata.create_all()` before Alembic
existed. It **already has** `brands`, `runs`, `findings`, `triage`, `pages` and `users`, holding
roughly 263,000 findings across ~98 runs and 9 brands.

Revision `0001` **creates** those tables. Running `upgrade head` against that database would
therefore fail on the first `CREATE TABLE brands` — and if it somehow did not, it would be because
something had already dropped the data.

So, once, on the existing database:

```bash
python3 -m alembic stamp head
```

That writes `0001` into the `alembic_version` table and touches nothing else. From then on
`upgrade head` is the normal command and it applies only revisions newer than `0001`.

Confirm it took:

```bash
python3 -m alembic current          # -> 0001 (head)
```

## Fresh database

A database with no tables — a new deploy, a colleague's laptop, CI — takes the normal path:

```bash
python3 -m alembic upgrade head
```

This replaces `server.db.create_all()`. `create_all()` stays in the codebase for the test suite,
which builds a throwaway schema per run and has no use for a migration history.

**Do not mix the two.** If you bring a database up with `create_all()`, stamp it. If you bring it
up with `upgrade head`, never call `create_all()` against it.

---

## Adding a change

```bash
# 1. edit server/models.py
# 2. generate the diff
python3 -m alembic revision --autogenerate -m "add finding.assigned_to"

# 3. READ THE GENERATED FILE. Always.
# 4. apply it
python3 -m alembic upgrade head
```

Step 3 is not optional. Autogenerate is a good first draft and a poor final answer: it does not see
server-side defaults reliably, it cannot know how to backfill a new NOT NULL column on 263k existing
rows, and it renames columns as a drop plus an add — which is data loss, silently.

To write one by hand instead:

```bash
python3 -m alembic revision -m "backfill run.enumeration_method"
```

### Migrating while a crawl is running

A crawl runs for hours (RR ~6h; MHD is throttled to ~10 pages/min) and the worker holds sessions
open the whole time. `ALTER TABLE` takes an ACCESS EXCLUSIVE lock, so a migration against a live
crawl will block behind the worker — or, worse, block the worker behind itself. Land schema changes
when no run is `running`:

```bash
psql "$DATABASE_URL" -c "select id, brand_id, status from runs where status in ('queued','running')"
```

---

## Where the connection URL comes from

`env.py`, in this order:

1. the `DATABASE_URL` environment variable;
2. `server.db.DATABASE_URL`, which itself defaults to the local docker container
   `postgresql+psycopg://district:district@127.0.0.1:55432/district`.

`sqlalchemy.url` in `alembic.ini` is intentionally left empty. A URL there would be a second
definition of "where the database is" and would eventually disagree with the one the API and
worker use.

## Procrastinate's tables are not ours

The job queue shares this database and owns `procrastinate_jobs`, `procrastinate_events`,
`procrastinate_workers` and `procrastinate_periodic_defers`. It creates them itself:

```bash
python3 -m procrastinate --app=server.jobs.app schema --apply
```

They are not in `Base.metadata`, so `env.py` filters them out of autogenerate
(`FOREIGN_TABLE_PREFIXES`). Without that filter the first `--autogenerate` would emit
`op.drop_table('procrastinate_jobs')` and take the queue — including any audit mid-crawl — with it.
If you ever see a `procrastinate_*` table in a generated revision, the filter is broken: delete the
revision, fix `env.py`, and regenerate.

## Offline / reviewable SQL

```bash
python3 -m alembic upgrade head --sql > schema.sql
```

Prints the SQL instead of executing it, for when a change needs review before it touches the
production database.
