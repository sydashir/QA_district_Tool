#!/usr/bin/env bash
# Restore the newest backup into a scratch database and prove it matches the live one.
#
# WHY THIS EXISTS: a dump nobody has ever restored is not a backup, it is a file. `pg_dump` exiting 0
# means it wrote a file, not that the file can be read back. This restores for real and compares
# CONTENT, not just row counts — row counts pass happily on a dump that silently lost a column.
#
# It is read-only with respect to the real database: it only ever CREATEs and DROPs its own scratch
# database, whose name contains a timestamp so it can never collide with anything real.
#
# Usage:  deploy/backup-verify.sh
#         BACKUP_DIR=/var/backups/auditor deploy/backup-verify.sh
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/auditor}"
DB="${POSTGRES_DB:-district}"
USER="${POSTGRES_USER:-district}"
SCRATCH="verify_$(date -u +%Y%m%d%H%M%S)"

BACKUP_DOCKER_SERVICE="${BACKUP_DOCKER_SERVICE:-}"
BACKUP_DOCKER_CONTAINER="${BACKUP_DOCKER_CONTAINER:-}"
COMPOSE_DIR="${COMPOSE_DIR:-/opt/auditor}"

log() { printf '[verify] %s\n' "$*"; }
die() { printf '[verify] ERROR: %s\n' "$*" >&2; exit 1; }

# Same three ways of reaching postgres the backup script supports, so the two agree about where the
# database is instead of each having their own idea.
if [ -n "$BACKUP_DOCKER_SERVICE" ]; then
  psql_() { (cd "$COMPOSE_DIR" && docker compose exec -T "$BACKUP_DOCKER_SERVICE" psql -U "$USER" "$@"); }
  restore_() { (cd "$COMPOSE_DIR" && docker compose exec -T "$BACKUP_DOCKER_SERVICE" pg_restore -U "$USER" "$@"); }
elif [ -n "$BACKUP_DOCKER_CONTAINER" ]; then
  psql_() { docker exec -i "$BACKUP_DOCKER_CONTAINER" psql -U "$USER" "$@"; }
  restore_() { docker exec -i "$BACKUP_DOCKER_CONTAINER" pg_restore -U "$USER" "$@"; }
else
  psql_() { psql "$@"; }
  restore_() { pg_restore "$@"; }
fi

# Newest dump. Portable: no `mapfile` (bash 4+ only; macOS ships 3.2) and no `ls` parsing.
NEWEST=""
for f in "$BACKUP_DIR"/district-*.dump; do
  [ -e "$f" ] || continue
  [ -z "$NEWEST" ] && NEWEST="$f"
  [ "$f" -nt "$NEWEST" ] && NEWEST="$f"
done
[ -n "$NEWEST" ] || die "no dump found in $BACKUP_DIR"
log "verifying $NEWEST"

cleanup() { psql_ -d postgres -c "DROP DATABASE IF EXISTS $SCRATCH;" >/dev/null 2>&1 || true; }
trap cleanup EXIT

psql_ -d postgres -c "CREATE DATABASE $SCRATCH;" >/dev/null || die "could not create scratch database"
restore_ -d "$SCRATCH" --no-owner --no-privileges < "$NEWEST" >/dev/null 2>&1 \
  || die "pg_restore FAILED — this dump is not restorable"

fail=0
# Row counts first: cheap, and catches a wholesale table loss.
for t in findings runs brands triage; do
  a=$(psql_ -d "$DB" -tAc "select count(*) from $t" 2>/dev/null || echo "-")
  b=$(psql_ -d "$SCRATCH" -tAc "select count(*) from $t" 2>/dev/null || echo "-")
  if [ "$a" = "$b" ] && [ "$a" != "-" ]; then
    log "  $t: $a rows — match"
  else
    printf '[verify] MISMATCH %s: live=%s restored=%s\n' "$t" "$a" "$b" >&2; fail=1
  fi
done

# Then CONTENT. `coalesce` on every nullable column on purpose: a bare `a||b` where b is NULL yields
# NULL for the whole row, string_agg skips it, and the checksum silently compares fewer rows than it
# claims to. That exact mistake produced an empty checksum that looked like a pass.
SUM="select md5(string_agg(x,'|' order by x)) from (
       select f.id::text||':'||f.run_id::text||':'||coalesce(f.fingerprint,'')
              ||':'||coalesce(f.suggestion,'') as x from findings f) s"
a=$(psql_ -d "$DB" -tAc "$SUM"); b=$(psql_ -d "$SCRATCH" -tAc "$SUM")
if [ -n "$a" ] && [ "$a" = "$b" ]; then log "  findings content checksum matches ($a)"
else printf '[verify] CONTENT MISMATCH: live=%s restored=%s\n' "${a:-NULL}" "${b:-NULL}" >&2; fail=1; fi

# triage is the one table a re-crawl cannot rebuild — a human's judgement about a defect.
TSUM="select coalesce(md5(string_agg(t::text,'|' order by t::text)),'empty') from triage t"
a=$(psql_ -d "$DB" -tAc "$TSUM"); b=$(psql_ -d "$SCRATCH" -tAc "$TSUM")
if [ "$a" = "$b" ]; then log "  triage content checksum matches ($a)"
else printf '[verify] TRIAGE MISMATCH: live=%s restored=%s\n' "$a" "$b" >&2; fail=1; fi

[ "$fail" -eq 0 ] || die "verification FAILED — the newest backup does not match the live database"
log "ok — $NEWEST restores and matches"
