# Deploying the District Site Auditor — first-deploy runbook

One small Linux VM runs everything: Postgres, the FastAPI API, and **one** long-running audit
worker. Auth is Cloudflare Access in front, so there is no login code in the app.

Target box: **Hetzner CX33 (4 vCPU / 8 GB / 80 GB NVMe)**. The reasoning — including why managed
platforms were rejected (Render Cron Jobs stop at 12h, Fly stops machines by default, and Render's
Postgres alone costs more than this entire box) — is in
[`docs/plans/2026-08-14-product-design.md`](../docs/plans/2026-08-14-product-design.md).

**The one number that shapes every decision below: a full audit takes about seven hours**, and RR
alone is ~8,000 pages / ~6 hours. Nothing here may assume a short-running process.

There are two install routes. **Pick one.**

* **Docker Compose** (`docker-compose.yml` at the repo root) — three containers, one command.
* **systemd** (`deploy/*.service`) — Postgres from the distro, the app in a venv. Fewer moving
  parts, easier to attach a debugger to, more steps to get wrong.

---

## 0. Before you touch the box — gather five things

The deploy stalls on each of these, so collect them first.

| # | Thing | Where it comes from | What breaks without it |
|---|---|---|---|
| 1 | `data/nap_snapshot.xlsx` | **gitignored** — copy from the dev machine | The phone check (the flagship check) has no canonical numbers |
| 2 | `geo_field_validator.py` | GeoData Fetcher repo, `services/` | The placeholder check raises `FileNotFoundError`; **and** `checks_version` flips to `MISSING`, invalidating every resume cache |
| 3 | Google service-account JSON | `app-service-account@lexical-sol-454719-s2.iam.gserviceaccount.com` | `POST /api/export/sheet` returns 503 (see §9) |
| 4 | A Postgres password | you invent it | nothing starts |
| 5 | A hostname + Cloudflare Access application + Tunnel | Cloudflare dashboard | **the admin API would be on the public internet with no auth** (see §9) |

Item 2 is one file. The check `importlib`-loads only `geo_field_validator.py`, and stubs the one
import it makes (`services.sheets_service`), so nothing else from that repo is needed.

---

## 1. Provision the box

```bash
# as root, on a fresh Debian 12 / Ubuntu 24.04
adduser --system --group --home /opt/auditor auditor
apt-get update && apt-get install -y git rsync
timedatectl set-timezone UTC          # the timer names its own timezone; keep the box on UTC

git clone <repo> /opt/auditor
chown -R auditor:auditor /opt/auditor
```

Everything below assumes the repo is at **`/opt/auditor`**. If you put it elsewhere, edit the
`WorkingDirectory=`, `ExecStart=` and `ReadWritePaths=` lines in the three unit files.

### Environment file

```bash
cp /opt/auditor/.env.example /opt/auditor/.env
$EDITOR /opt/auditor/.env                 # set POSTGRES_PASSWORD, DATABASE_URL, MAIL_*, APP_URL
chmod 600 /opt/auditor/.env
```

Read the comments in `.env.example` rather than skimming the variable names — several of them
(`GEODATA_SERVICES_DIR`, `SPELLCHECK`, and the note about the Google credentials **not** being an
env var) carry consequences that are not obvious from the name.

### The two files that are not in git

```bash
# 1. canonical phone numbers
mkdir -p /opt/auditor/data
scp dev-machine:~/Documents/workk2/QA_district_Tool/data/nap_snapshot.xlsx /opt/auditor/data/

# 2. the out-of-repo ACF ruleset — ONE file, at the path GEODATA_SERVICES_DIR points to
mkdir -p /opt/geodata-services
scp dev-machine:~/Documents/workk/district/services/geo_field_validator.py /opt/geodata-services/

chown -R auditor:auditor /opt/auditor/data /opt/geodata-services
```

> **Do not change `GEODATA_SERVICES_DIR` later.** `auditor/checks_version.py:65` hashes the *path
> string itself* as a version component — repointing the ACF ruleset **is** a rule change, by
> design. Move it and you invalidate every brand's resume cache and get one full `rule_changed`
> cycle in the diff. `/opt/geodata-services` is used by both install routes so switching between
> them is free.

---

## 2A. Bring up — Docker Compose

```bash
cd /opt/auditor

# The bind mounts are written to by a container running as uid 10001 (the image's `auditor` user).
mkdir -p cache reports
chown -R 10001:10001 cache reports data

# Tell compose where geo_field_validator.py lives ON THE HOST. The CONTAINER path is always
# /opt/geodata-services and must never change (it is hashed into checks_version).
echo 'GEODATA_HOST_DIR=/opt/geodata-services' >> .env

docker compose config -q          # validates only; starts nothing
docker compose build
docker compose up -d db
docker compose ps                 # wait for db to report (healthy)
```

Then continue to §3 (schema), running the app commands through the image:

```bash
docker compose run --rm api <command>
```

Finally:

```bash
docker compose up -d              # api + worker
docker compose logs -f worker     # should print the startup reconcile line, then go quiet
```

## 2B. Bring up — systemd

```bash
apt-get install -y python3.12 python3.12-venv postgresql-16 postgresql-client-16

sudo -u postgres createuser district --pwprompt
sudo -u postgres createdb district --owner=district

sudo -u auditor python3.12 -m venv /opt/auditor/.venv
sudo -u auditor /opt/auditor/.venv/bin/pip install -r /opt/auditor/deploy/requirements-server.txt

# the units read the environment from here, not from the repo
mkdir -p /etc/auditor && cp /opt/auditor/.env /etc/auditor/auditor.env
chown -R auditor:auditor /etc/auditor && chmod 600 /etc/auditor/auditor.env
```

Run §3 with the venv (`sudo -u auditor /opt/auditor/.venv/bin/<command>`, from `/opt/auditor`),
then install the units:

```bash
cp /opt/auditor/deploy/auditor-api.service /etc/systemd/system/
cp /opt/auditor/deploy/auditor-worker.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now auditor-api auditor-worker
```

---

## 3. Create the schema — `upgrade` or `stamp`, and they are not interchangeable

Two schemas live in this database and they are applied by different tools.

**3a. The application schema (Alembic).** Pick the line that matches your situation:

```bash
alembic upgrade head    # FRESH, EMPTY database — creates every table
alembic stamp head      # database that ALREADY has the tables — records the revision, changes nothing
```

Use `stamp` when you restored a dump (§8) or when the database was built by an older
`create_all()`. Running `upgrade` on a populated database fails on the first `CREATE TABLE`; running
`stamp` on an empty one leaves you with no tables and Alembic convinced it is up to date. Confirm
either way:

```bash
alembic current         # should print the head revision
```

**3b. The queue schema (Procrastinate owns it, Alembic deliberately does not).**

```bash
procrastinate --app=server.jobs.app schema --apply
```

`migrations/env.py` excludes `procrastinate_*` tables from autogenerate on purpose: an autogenerated
migration would otherwise emit `op.drop_table('procrastinate_jobs')` and destroy the queue —
including any audit mid-crawl.

**3c. Seed the brands.**

```bash
python3 -m server.importer
```

On an empty `reports/` this imports zero runs and seeds the nine brands from `config/*.toml` —
which is all the API and the nightly trigger need. If you copied `reports/` over from the dev
machine it also backfills that history. It is idempotent; a second run reports everything as
already present.

Verify the brands landed, and that MHD is **not** scheduled:

```bash
# docker:   docker compose exec db psql -U district -d district -c "$SQL"
# systemd:  psql "$DATABASE_URL_WITHOUT_+psycopg" -c "$SQL"
SQL="SELECT code, enabled, coalesce(schedule_cron,'(none)') FROM brands ORDER BY code;"
```

Expect 9 rows, and MHD's schedule must read `(none)` — that is what keeps the nightly trigger off
it. (`server/importer.py:68` sets it that way; if MHD ever shows a cron here, something re-seeded
it wrongly.)

---

## 4. Verify before scheduling anything

```bash
curl -s http://127.0.0.1:8099/api/health
# {"ok":true,"brands":9,"runs":...,"findings":...}

curl -s http://127.0.0.1:8099/api/brands | head -c 400
```

Then start **one small brand by hand** and watch it end to end. TDRC is the smallest.

```bash
curl -s -X POST http://127.0.0.1:8099/api/brands/TDRC/runs      # 202 + a run_id
curl -s "http://127.0.0.1:8099/api/runs?brand=TDRC&limit=1"     # queued -> running -> ok
```

What you are checking, in order:

1. the run leaves `queued` within a few seconds — if it does not, the worker is not running;
2. it reaches `ok` and `pages_audited` is in the thousands;
3. `reports/tdrc/<stamp>/` exists **and the API container can see it** — this is the shared-volume
   mistake, and it only shows up later as a 409 from the sheet export;
4. it does **not** land on `refused`.

> **`refused` is not a pass.** It means the brand *could not be audited* — the site was unreachable
> or had no page index — and the previous results are untouched. A refused brand has **not** been
> given a clean bill of health. If TDRC refuses, fix the crawl before going further; do not
> schedule anything.

Never trigger **MHD** from the API. Its origin throttles to ~10 pages/min and a full 11,439-page
census is ~131 hours; it is meant to run as a labelled partial sample via the CLI's `-n` flag, and
`POST /api/brands/{code}/runs` has no sampling parameter. `deploy/nightly_enqueue.py` refuses it
explicitly for this reason.

---

## 5. Turn on the nightly run

```bash
cp /opt/auditor/deploy/auditor-nightly.service /etc/systemd/system/
cp /opt/auditor/deploy/auditor-nightly.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now auditor-nightly.timer

systemctl list-timers auditor-nightly.timer    # NEXT should be tomorrow 02:00 Pacific
systemctl start auditor-nightly.service        # dry test: queues now, ignores the timer
journalctl -u auditor-nightly -n 30
```

The timer fires at **02:00 America/Los_Angeles** — the client's stated 2am–5am PST crawl window —
with `Persistent=true` and up to 15 minutes of jitter. The unit files explain why it is a timer and
not an in-process scheduler; the short version is that a persistent timer **replays a run missed
while the box was down**, and an in-process periodic task simply never fires.

**Under Docker, the timer still runs on the host** — it only makes HTTP calls to `127.0.0.1:8099`.
Nothing schedules itself inside a container.

**This is the single biggest operational risk in the whole product.** Until this timer is enabled
and verified, nothing is audited unless a human clicks a button.

---

## 6. Serve the dashboard

The API does **not** serve the SPA — `server/api.py` mounts no `StaticFiles`, and there is no `web`
service in the compose file. Build it and put a reverse proxy in front:

```bash
cd /opt/auditor/web && npm ci && npm run build     # -> web/dist
```

Point the Cloudflare Tunnel at a proxy that serves `web/dist` for `/` and forwards `/api` to
`127.0.0.1:8099`. Caddy is two lines:

```
auditor.example.com {
    handle /api/* { reverse_proxy 127.0.0.1:8099 }
    handle { root * /opt/auditor/web/dist; try_files {path} /index.html; file_server }
}
```

Same-origin, so no CORS is involved — which matters, because `server/api.py:37` allows only
`http://localhost:5173`. Serving the SPA from a *different* origin than the API means editing that
line as well.

---

## 7. Backups

We own backups entirely; there is no managed service taking snapshots. `deploy/backup.sh` writes a
timestamped `pg_dump` custom-format archive and prunes old ones.

**The script is not executable in a fresh clone** — git does not reliably carry the bit, and the
file is written 644. Either fix it once or always call it through bash:

```bash
chmod +x /opt/auditor/deploy/backup.sh          # do this once
# or, equivalently, run it as:  bash /opt/auditor/deploy/backup.sh
```

Schedule it away from the 02:00 audit window:

```cron
# /etc/cron.d/auditor-backup
30 4 * * *  auditor  BACKUP_DIR=/var/backups/auditor RETENTION_DAYS=14 /opt/auditor/deploy/backup.sh >> /var/log/auditor-backup.log 2>&1
```

Docker install — the host may have no `pg_dump`, so dump inside the container:

```bash
BACKUP_DOCKER_SERVICE=db COMPOSE_DIR=/opt/auditor /opt/auditor/deploy/backup.sh
```

Off-box copy (do this; a local-only backup does not survive the event most likely to need it):

```bash
BACKUP_RSYNC_TARGET=backup-host:/srv/auditor/ /opt/auditor/deploy/backup.sh
```

The script **verifies the new dump before pruning anything** — a size floor plus a
`pg_restore --list` table-of-contents check — and never prunes below `MIN_KEEP` (7) files, so a
long run of failed backups cannot leave you with nothing. Expect ~40–60 MB per dump against
today's 244 MB / 263k-finding database.

**What this does not cover.** `cache/` (the resume cache, ~600 MB) and `reports/` (~290 MB) are
plain files on disk and are not in the dump. Losing `cache/` costs a full re-crawl of everything
(~7 hours, plus MHD). Losing `reports/` breaks `POST /api/export/sheet` for historical runs, which
reads the report off disk. Back both up with `rsync` if you care about the hours.

The row that genuinely cannot be regenerated is **`triage`** — a human's judgement about a defect.
Findings can be re-crawled; that table cannot.

### Restoring

```bash
systemctl stop auditor-worker auditor-api         # or: docker compose stop api worker
```

Restore into a **new, empty** database and swap, rather than over a live one:

```bash
sudo -u postgres createdb district_restore --owner=district
pg_restore --dbname=postgresql://district:PASS@127.0.0.1:55432/district_restore \
           --no-owner --no-privileges /var/backups/auditor/district-20260818-043000.dump

# sanity-check BEFORE you cut over
psql .../district_restore -c "SELECT count(*) FROM findings;"
psql .../district_restore -c "SELECT count(*) FROM triage;"
```

Then point `DATABASE_URL`/`PROCRASTINATE_DSN` at the restored database (or rename it), restart, and
**`alembic stamp head`** — the restored database already has the tables, so `upgrade` would fail.

Recovering only the triage table, which is the usual case:

```bash
pg_restore --data-only --table=triage --dbname=<live dsn> district-<stamp>.dump
```

A restored dump also carries the `procrastinate_*` tables, so a job may come back marked `doing`
with no worker behind it. That is handled: `reconcile_orphaned_runs()` runs at worker startup,
compares against the worker heartbeat, and marks any such run `failed` rather than leaving it stuck
in `running`.

---

## 8. Migrating the existing database instead of starting fresh

There are already ~263,000 findings, ~98 runs and the team's triage on the dev machine. Carry them
over rather than re-crawling:

```bash
# on the dev machine
docker exec district-pg pg_dump -U district --format=custom district > district.dump
scp district.dump box:/tmp/

# on the box, with the app stopped
pg_restore --dbname=<dsn> --no-owner --no-privileges /tmp/district.dump
alembic stamp head          # STAMP, not upgrade — the tables are already there
```

Copy `reports/` and `cache/` across at the same time (`rsync -a`). `runs.report_dir` holds
**repo-relative** paths, so as long as the repo root is `/opt/auditor` the sheet export keeps
working.

Expect the first run after migrating to report a wave of `rule_changed` findings if
`GEODATA_SERVICES_DIR` differs from the dev machine's value (it will —
`$HOME/Documents/workk/district/services` there, `/opt/geodata-services` here). That is the version
system working correctly, not a bug: the diff labels them `rule_changed`, **not** `resolved`, so
nothing is misreported as fixed. It settles after one cycle.

---

## 9. Known gaps — say these out loud, do not paper over them

1. **The API has no auth of its own.** `api.get_current_user()` (`server/api.py:47`) returns a
   fixed local admin. Cloudflare Access in front is what makes that safe, which means the origin
   must be reachable **only** through Cloudflare — hence the tunnel, and hence every port in this
   deploy binding to `127.0.0.1`. Cloudflare's own docs are explicit that checking header presence
   alone permits identity spoofing: when auth is implemented it must verify the JWT **signature**
   plus `iss` and the per-application `aud`.
2. **The Google service-account path is hardcoded to a laptop.**
   `auditor/publish.py:102` is a literal `/Users/ashir/...` path. Sheet export 503s until you either
   edit that one line to `/etc/auditor/service-account.json` (preferred — `publish.py` is *not*
   hashed into `checks_version`, so changing it invalidates no cache) or recreate that literal path
   on the box. There is no environment variable for it; do not go looking for one.
3. **`requirements.txt` is missing two engine dependencies** — `openpyxl` (canonical phone numbers)
   and `google-auth` (sheet export). `deploy/requirements-server.txt` installs them and says so.
   They fail at runtime, not at import, which is how they went unnoticed on a dev machine.
4. **DBH is enumerated from a static list** (`config/urls/dbh.txt`) because its 2026-08-08 replatform
   to headless Next.js removed the sitemap, robots.txt and WP-REST. A static list cannot discover
   pages added later — they are silently never audited. Regenerate it by hand when DBH publishes.
5. **MHD is a labelled partial sample, permanently.** Concurrency 2 is a locked ceiling; its origin
   503s above that. Never trigger it from the API (§4). If enumeration ever returns *blocked + 0
   URLs*, that is evidence the origin is degraded: leave it alone, do not retry into it.
6. **Stopping the worker during a crawl throws away hours.** Both the systemd unit and the compose
   service give it 5 minutes to wind down, then it is killed. The resume cache means the work is
   re-usable, not lost — but always check `GET /api/runs` for a `running` row before restarting or
   deploying.
7. **No log rotation for the app itself under systemd.** Output goes to the journal; cap it with
   `SystemMaxUse=` in `journald.conf`. Under Docker both Python services cap json-file logs at
   5 × 10 MB.
