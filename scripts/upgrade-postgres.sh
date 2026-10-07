#!/usr/bin/env bash
# One-off upgrade of the Streamlit-era PostgreSQL 15 data (volume `pgdata`) to the new
# PostgreSQL 18 + pgvectorscale service (volume `pgdata18`), by dump and restore.
#
#   scripts/upgrade-postgres.sh            # uses docker-compose.yaml and .env
#   COMPOSE_ARGS="-f docker-compose.dev.yaml" scripts/upgrade-postgres.sh
#
# The old volume is never modified, so rolling back only means pointing compose at it again.
# After a successful run, start the stack (`docker compose up -d`); the backend adopts the
# restored schema (alembic stamp 0001) and applies the new migrations.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose ${COMPOSE_ARGS:-}"
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

if [[ -f .env ]]; then
  set -a; source .env; set +a
fi
PGUSER="${POSTGRES_USER:-postgres}"
PGDB="${POSTGRES_DB:-auto_mapper}"
TABLES="concept concept_relationship concept_ancestor concept_atc7 source_concepts source_standard_map auto_mapping_audit app_config vocabulary_imports"

wait_healthy() {
  # A fresh volume is initialised by a temporary server that is restarted afterwards, so
  # require several consecutive successful queries before trusting the server.
  local service=$1 ok=0
  for _ in $(seq 1 120); do
    if $COMPOSE exec -T "$service" psql -U "$PGUSER" -d "$PGDB" -tAc "SELECT 1" >/dev/null 2>&1; then
      ok=$((ok + 1))
      [[ $ok -ge 3 ]] && return 0
    else
      ok=0
    fi
    sleep 2
  done
  echo "Service $service did not become ready" >&2
  exit 1
}

count_rows() {
  # Prints the row count, or -1 if the table does not exist; fails if the server is unreachable
  local service=$1 table=$2 exists
  exists=$($COMPOSE exec -T "$service" psql -U "$PGUSER" -d "$PGDB" -tAc \
    "SELECT to_regclass('public.$table') IS NOT NULL") || return 1
  if [[ "$exists" != "t" ]]; then echo -1; return 0; fi
  $COMPOSE exec -T "$service" psql -U "$PGUSER" -d "$PGDB" -tAc "SELECT COUNT(*) FROM $table"
}

echo "==> Stopping application services"
$COMPOSE stop backend worker frontend >/dev/null 2>&1 || true

echo "==> Starting PostgreSQL 18 (new volume)"
$COMPOSE up -d postgres-db
wait_healthy postgres-db
new_concepts=$(count_rows postgres-db concept)
if [[ "$new_concepts" != "-1" && $FORCE -eq 0 ]]; then
  echo "The PostgreSQL 18 database already contains the schema. Re-run with --force to drop and restore it." >&2
  exit 1
fi

echo "==> Starting the old PostgreSQL 15 container on volume 'pgdata'"
$COMPOSE --profile pg-upgrade up -d postgres-15-old
wait_healthy postgres-15-old
old_concepts=$(count_rows postgres-15-old concept)
if [[ "$old_concepts" == "-1" ]]; then
  echo "The old database has no 'concept' table - nothing to upgrade." >&2
  $COMPOSE --profile pg-upgrade stop postgres-15-old
  exit 1
fi

if [[ $FORCE -eq 1 ]]; then
  echo "==> Dropping and recreating the PostgreSQL 18 database"
  $COMPOSE exec -T postgres-db psql -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS \"$PGDB\" WITH (FORCE)" -c "CREATE DATABASE \"$PGDB\""
fi

echo "==> Copying data (pg_dump 15 | pg_restore 18). This can take a while for full OMOP vocabularies."
$COMPOSE exec -T postgres-15-old pg_dump -U "$PGUSER" -d "$PGDB" --format=custom --no-owner --no-privileges \
  | $COMPOSE exec -T postgres-db pg_restore -U "$PGUSER" -d "$PGDB" --no-owner --no-privileges --exit-on-error

echo "==> Verifying row counts"
status=0
for table in $TABLES; do
  old=$(count_rows postgres-15-old "$table")
  new=$(count_rows postgres-db "$table")
  printf "  %-24s old=%-12s new=%-12s %s\n" "$table" "$old" "$new" "$([[ "$old" == "$new" ]] && echo ok || echo MISMATCH)"
  [[ "$old" == "$new" ]] || status=1
done
$COMPOSE exec -T postgres-db psql -U "$PGUSER" -d "$PGDB" -qc "ANALYZE" >/dev/null

echo "==> Stopping the old PostgreSQL 15 container"
$COMPOSE --profile pg-upgrade stop postgres-15-old >/dev/null

if [[ $status -ne 0 ]]; then
  echo "Row counts differ - check the output above. The old volume 'pgdata' is untouched." >&2
  exit 1
fi
cat <<MSG

Upgrade complete. Next:
  docker compose up -d          # the backend stamps the baseline and applies the new migrations
When everything works, the old volume can be removed manually:
  docker volume rm \$(docker volume ls -q | grep '_pgdata$')
MSG
