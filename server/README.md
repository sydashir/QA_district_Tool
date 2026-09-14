# server — the product layer

Wraps the finished audit engine. Nothing in `auditor/` was changed to build this.

## Local bring-up

**No Docker on the Mac.** Since 2026-09-14 the database is a native Homebrew Postgres, because
Docker Desktop disrupted the machine it runs on. Postgres listens on **55432**, the port every
default in this repo already expects, so nothing else needs configuring.

```bash
brew install postgresql@16                 # once; the cluster lives in /usr/local/var/postgresql@16
# set `port = 55432` in /usr/local/var/postgresql@16/postgresql.conf
brew services start postgresql@16          # starts now and at every login
PG=/usr/local/opt/postgresql@16/bin        # keg-only: psql / pg_restore are not on PATH
$PG/psql -h 127.0.0.1 -p 55432 -d postgres -c "create role district login password 'district'"
$PG/psql -h 127.0.0.1 -p 55432 -d postgres -c "create database district owner district \
  template template0 encoding 'UTF8' lc_collate 'en_US.UTF-8' lc_ctype 'en_US.UTF-8'"

# either restore real data (the newest dump in backups/ — check its date first) ...
$PG/pg_restore -h 127.0.0.1 -p 55432 -U district -d district --no-owner --role=district \
  --exit-on-error -j 4 backups/district-YYYYMMDD-HHMMSS.dump
# ... or start empty
python3 -m alembic upgrade head            # creates the schema (run from the repo root)

python3 -m uvicorn server.api:app --port 8099   # the API the web app proxies to
python3 -m server.worker                        # only needed to run audits from the web app
```

### Starting at login — REQUIRED on the Mac

Postgres starts at login (`brew services`). The API and the web app start at login **only if the
login agents are installed**. Without them a reboot leaves the product dead with no warning: the page
does not load, or loads and shows nothing.

```bash
deploy/macos/login_agents.sh install     # once: writes two launchd agents, starts both, waits for answers
deploy/macos/login_agents.sh status      # are both running, and do both answer?
deploy/macos/login_agents.sh uninstall
```

* `local.district-auditor.api` runs uvicorn on 127.0.0.1:8099; `local.district-auditor.web` runs the
  Vite dev server on localhost:5173. Both start at login and are restarted by launchd if they exit.
* Logs: `~/Library/Logs/district-auditor/{api,web}.log`.
* **The API does not reload itself.** After changing anything under `server/`, restart it:
  `launchctl kickstart -k gui/$(id -u)/local.district-auditor.api`. The web agent picks up changes
  on its own.

`DATABASE_URL` overrides the connection; it defaults to `127.0.0.1:55432`. Alembic reads the same
variable, and falls back to the same default, via `server.db.DATABASE_URL`. `docker-compose.yml`
still describes the server deployment in `deploy/README.md`; it is simply not how this runs locally.

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
