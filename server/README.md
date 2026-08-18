# server — the product layer

Wraps the finished audit engine. Nothing in `auditor/` was changed to build this.

## Local bring-up

```bash
docker run -d --name district-pg -e POSTGRES_PASSWORD=district -e POSTGRES_USER=district \
  -e POSTGRES_DB=district -p 55432:5432 postgres:16
python3 -m alembic upgrade head     # creates the schema (run from the repo root)
python3 -m server.importer          # backfills reports/ -> postgres (85 runs, ~247k findings)
python3 -m uvicorn server.api:app --port 8099
```

`DATABASE_URL` overrides the connection; it defaults to the container above. Alembic reads the
same variable, and falls back to the same default, via `server.db.DATABASE_URL`.

## Migrations

Alembic owns the schema. `db.create_all()` still exists for the test suite, which builds a
throwaway database per run and has no use for a migration history — but it must not be used on any
database Alembic manages.

Invoke it as `python3 -m alembic`, not bare `alembic` — the console script on PATH belongs to a
Python without `psycopg` and fails inside `env.py`.

```bash
# fresh database: create everything
python3 -m alembic upgrade head
# EXISTING database: record revision 0001, change nothing else
python3 -m alembic stamp head
# after editing models.py — then READ the generated file before applying it
python3 -m alembic revision --autogenerate -m "add x"
# what revision is this database on
python3 -m alembic current
```

**The database running today needs `stamp`, not `upgrade`.** It was built by `create_all()` before
Alembic existed, so it already has every table in revision `0001` plus ~263k findings; `upgrade`
would fail on the first `CREATE TABLE`. This is a one-time step.

Schema changes take an ACCESS EXCLUSIVE lock, and a crawl holds sessions open for hours (RR ~6h),
so migrate only when no run is `queued` or `running`.

Full detail — autogenerate caveats, why `procrastinate_*` tables are excluded, offline SQL — is in
[`../migrations/README.md`](../migrations/README.md).

## What is stubbed, and why

* **Auth** — `api.get_current_user()` returns a fixed local user. One seam; deploy-time it verifies
  the Cloudflare Access JWT (signature + `iss` + `aud`). No endpoint signature changes.
* **Email** — `mail.send()` writes to `reports/_outbox/` unless `SMTP_HOST` is set. The *logic*
  (who gets told, when, what it says) is finished and testable without a sending domain.
* **Deploy** — deferred by decision. See `docs/plans/2026-08-14-product-design.md`.
