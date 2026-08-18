#!/usr/bin/env bash
# Nightly Postgres backup. We own backups entirely — this is a plain VM, there is no managed
# service taking snapshots for us.
#
# What is in the database and what it costs to lose: ~263,000 findings across ~98 runs (244 MB
# today), and — the part that is genuinely unrecoverable — the `triage` table. Findings can be
# re-crawled. A human's judgement about a defect cannot.
#
# Usage:  deploy/backup.sh
#         BACKUP_DIR=/mnt/backups RETENTION_DAYS=30 deploy/backup.sh
#
# Schedule it as its own systemd timer or a root crontab line (see deploy/README.md). Run it at a
# time that does NOT collide with the 02:00 audit trigger — pg_dump only takes an ACCESS SHARE lock
# so it is safe during a crawl, but there is no reason to make the box do both at once.

set -euo pipefail

# --- configuration -------------------------------------------------------------------------------
BACKUP_DIR="${BACKUP_DIR:-/var/backups/auditor}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
# Never let retention empty the directory. If backups have been failing for three weeks, the old
# ones are the only ones left and deleting them is the worst possible move.
MIN_KEEP="${MIN_KEEP:-7}"
# A dump smaller than this is treated as a failure, not a backup. A truncated or empty file that
# passes silently is worse than no file at all, because it looks like protection.
MIN_BYTES="${MIN_BYTES:-1000000}"   # 1 MB; the real dump is ~40-60 MB compressed

# Set when Postgres runs in the compose stack and the host has no postgres-client installed.
# e.g. BACKUP_DOCKER_SERVICE=db  ->  runs pg_dump inside that container.
BACKUP_DOCKER_SERVICE="${BACKUP_DOCKER_SERVICE:-}"
COMPOSE_DIR="${COMPOSE_DIR:-/opt/auditor}"
# For a plain `docker run` container (what local dev uses): dump inside it by name.
BACKUP_DOCKER_CONTAINER="${BACKUP_DOCKER_CONTAINER:-}"
# Inside the container the server is local, whatever host/port the app uses to reach it.
DSN_IN_CONTAINER="${DSN_IN_CONTAINER:-postgresql://district:district@127.0.0.1:5432/district}"

# Optional off-box copy. Local-only backups do not survive the event most likely to need them.
BACKUP_RSYNC_TARGET="${BACKUP_RSYNC_TARGET:-}"

# --- connection ----------------------------------------------------------------------------------
# Reuse the app's own DATABASE_URL so there is one definition of where the database is. It carries
# SQLAlchemy's `+psycopg` driver suffix, which libpq does not understand, so strip it.
: "${DATABASE_URL:=postgresql+psycopg://district:district@127.0.0.1:55432/district}"
DSN="${BACKUP_DSN:-${DATABASE_URL/+psycopg/}}"

STAMP="$(date -u +%Y%m%d-%H%M%S)"
OUT="${BACKUP_DIR}/district-${STAMP}.dump"

log() { printf '[backup] %s\n' "$*"; }
die() { printf '[backup] ERROR: %s\n' "$*" >&2; exit 1; }

mkdir -p "$BACKUP_DIR"

# Only one backup at a time. Two overlapping pg_dumps would double the I/O for no benefit and could
# interleave their retention passes.
# Locking. `flock` is standard on Linux (the deploy target) but ABSENT on macOS, where a developer
# will run this by hand. The first version treated a missing `flock` as "lock is held" and printed
# "another backup is already running" — a lie that sends you hunting a job that does not exist.
# Distinguish the two, and fall back to an atomic mkdir lock so the guarantee survives either way.
LOCKDIR="${BACKUP_DIR}/.backup.lock.d"
if command -v flock >/dev/null 2>&1; then
  exec 9>"${BACKUP_DIR}/.backup.lock"
  flock -n 9 || die "another backup is already running (lock held on ${BACKUP_DIR}/.backup.lock)"
else
  # mkdir is atomic on every POSIX filesystem, which is the property a lock needs.
  if ! mkdir "$LOCKDIR" 2>/dev/null; then
    die "another backup is already running (lock dir ${LOCKDIR} exists — remove it if stale)"
  fi
  trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT
fi

log "dumping to ${OUT}"

# -Fc: custom format. Compressed, and restorable table-by-table with pg_restore, which is what you
# want at 3am when only `triage` needs recovering. Plain SQL would be a 1 GB file you can only
# replay whole.
#
# pg_dump must be the same major version as the server (16) or newer — an older client refuses.
if [[ -n "$BACKUP_DOCKER_CONTAINER" ]]; then
  # A plain `docker run` container (not compose-managed). Dumping INSIDE it guarantees the client
  # matches the server major version, which pg_dump refuses to work without.
  docker exec -i "$BACKUP_DOCKER_CONTAINER" \
      pg_dump --format=custom --compress=6 --no-owner --no-privileges "$DSN_IN_CONTAINER" > "$OUT"
elif [[ -n "$BACKUP_DOCKER_SERVICE" ]]; then
  # Dump inside the container (guaranteed matching client version), stream to the host.
  ( cd "$COMPOSE_DIR" && docker compose exec -T "$BACKUP_DOCKER_SERVICE" \
      pg_dump --format=custom --compress=6 --no-owner --no-privileges "$DSN" ) > "$OUT"
else
  command -v pg_dump >/dev/null 2>&1 || die "pg_dump not found — install postgresql-client-16, or set BACKUP_DOCKER_SERVICE"
  pg_dump --format=custom --compress=6 --no-owner --no-privileges --file="$OUT" "$DSN"
fi

# --- verify BEFORE rotating ----------------------------------------------------------------------
# Order matters. Deleting old backups before confirming the new one is good is how a bad night
# turns into no backups at all.
size=$(wc -c < "$OUT" | tr -d ' ')
[[ "$size" -ge "$MIN_BYTES" ]] || die "dump is only ${size} bytes (< ${MIN_BYTES}) — keeping old backups, not rotating"

# pg_restore --list reads the archive's table of contents. It is the cheapest proof that the file
# is a structurally valid dump rather than a well-sized pile of bytes.
if command -v pg_restore >/dev/null 2>&1; then
  pg_restore --list "$OUT" >/dev/null || die "dump failed its table-of-contents check — keeping old backups"
elif [[ -n "$BACKUP_DOCKER_CONTAINER" ]]; then
  docker exec -i "$BACKUP_DOCKER_CONTAINER" pg_restore --list < "$OUT" >/dev/null \
    || die "dump failed its table-of-contents check — keeping old backups"
elif [[ -n "$BACKUP_DOCKER_SERVICE" ]]; then
  ( cd "$COMPOSE_DIR" && docker compose exec -T "$BACKUP_DOCKER_SERVICE" pg_restore --list ) < "$OUT" >/dev/null \
    || die "dump failed its table-of-contents check — keeping old backups"
else
  log "WARNING: pg_restore not available, skipped the archive check"
fi

chmod 600 "$OUT"
log "ok — $(du -h "$OUT" | cut -f1)"

# --- off-box copy --------------------------------------------------------------------------------
if [[ -n "$BACKUP_RSYNC_TARGET" ]]; then
  log "copying to ${BACKUP_RSYNC_TARGET}"
  rsync -a --partial "$OUT" "$BACKUP_RSYNC_TARGET" \
    || log "WARNING: off-box copy failed — the local backup is still good"
fi

# --- retention -----------------------------------------------------------------------------------
# Age-based, floored by count: anything older than RETENTION_DAYS goes, EXCEPT that the newest
# MIN_KEEP files are never candidates. That floor is what makes a long run of failed backups
# survivable.
# Portable newest-first list. `mapfile` is bash 4+; macOS ships bash 3.2, where it does not exist
# and retention would abort — leaving dumps to accumulate until the disk fills.
all=()
while IFS= read -r line; do
  [[ -n "$line" ]] && all+=("$line")
done < <(ls -1t "${BACKUP_DIR}"/district-*.dump 2>/dev/null || true)
if (( ${#all[@]} > MIN_KEEP )); then
  for f in "${all[@]:MIN_KEEP}"; do
    if [[ -n "$(find "$f" -mtime "+${RETENTION_DAYS}" -print -quit 2>/dev/null)" ]]; then
      log "pruning $(basename "$f")"
      rm -f -- "$f"
    fi
  done
fi

log "done — $(ls -1 "${BACKUP_DIR}"/district-*.dump 2>/dev/null | wc -l | tr -d ' ') backup(s) retained"
