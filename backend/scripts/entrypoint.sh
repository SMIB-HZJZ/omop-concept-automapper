#!/bin/sh
# Container entrypoint for the API: prepare the database, then run the given command.
set -e
if [ "${SKIP_DB_INIT:-0}" != "1" ]; then
  python -m app.cli init
fi
exec "$@"
