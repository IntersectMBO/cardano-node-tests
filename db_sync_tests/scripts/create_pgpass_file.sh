#! /usr/bin/env nix-shell
#! nix-shell -i bash --keep LOG_FILEPATH --keep ENVIRONMENT --keep POSTGRES_DIR --keep PGUSER -p glibcLocales postgresql lsof procps
# shellcheck shell=bash

cd cardano-db-sync

echo "Inside create pgpass file"

export PGPASSFILE=config/pgpass-$ENVIRONMENT
echo "${POSTGRES_DIR}:5432:${ENVIRONMENT}:${PGUSER}:*" > $PGPASSFILE
chmod 600 $PGPASSFILE

cat $PGPASSFILE
