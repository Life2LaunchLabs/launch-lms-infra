#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
compose=(docker compose -f deploy/control-plane/compose.yaml)

case "${1:-}" in
  backup)
    destination="${2:?Provide an absolute protected backup path}"
    [[ "$destination" = /* && ! -e "$destination" ]] || { echo 'Backup destination must be a new absolute path' >&2; exit 1; }
    install -d -m 0700 "$(dirname "$destination")"
    temporary="${destination}.partial.$$"
    trap 'rm -f -- "$temporary"' EXIT
    "${compose[@]}" exec -T postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$temporary"
    test -s "$temporary"
    chmod 0600 "$temporary"
    mv -- "$temporary" "$destination"
    trap - EXIT
    echo "Backup created: $destination"
    ;;
  restore-test)
    source_file="${2:?Provide a backup file}"
    test -s "$source_file"
    database="operations_restore_$(date +%s)_$$"
    "${compose[@]}" exec -T postgres sh -c 'createdb -U "$POSTGRES_USER" "$1"' sh "$database"
    trap '"${compose[@]}" exec -T postgres sh -c '\''dropdb -U "$POSTGRES_USER" --if-exists "$1"'\'' sh "$database"' EXIT
    "${compose[@]}" exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$1" --no-owner --exit-on-error' sh "$database" < "$source_file"
    "${compose[@]}" exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$1" -Atqc "SELECT count(*) FROM schema_migrations"' sh "$database"
    echo 'Disconnected restore completed and migration ledger is readable.'
    ;;
  *) echo 'Usage: operations-db.sh backup ABSOLUTE_PATH | restore-test BACKUP_FILE' >&2; exit 2 ;;
esac
