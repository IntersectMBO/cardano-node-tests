#! /usr/bin/env nix-shell
#! nix-shell -i bash --keep LOG_FILEPATH --keep ENVIRONMENT --keep DB_SYNC_START_ARGS --keep POSTGRES_DIR --keep PGUSER --keep PGPORT --keep FIRST_START -p glibcLocales postgresql lsof procps
# shellcheck shell=bash


cd cardano-db-sync
export PGPASSFILE=config/pgpass-$ENVIRONMENT

if [[ $FIRST_START == "True" ]]; then
    cd config
    wget -O $ENVIRONMENT-db-config.json https://book.world.dev.cardano.org/environments/$ENVIRONMENT/db-sync-config.json
    sed -i "s/NodeConfigFile.*/NodeConfigFile\": \"..\/..\/cardano-node\/$ENVIRONMENT-config.json\",/g" "$ENVIRONMENT-db-config.json"
    cd ..
fi

# clear log file
cat /dev/null > $LOG_FILEPATH

# set abort on first error flag and start db-sync
export DbSyncAbortOnPanic=1
PGPASSFILE=$PGPASSFILE db-sync-node/bin/cardano-db-sync --config config/$ENVIRONMENT-db-config.json --socket-path ../cardano-node/db/node.socket --schema-dir schema/ --state-dir ledger-state/$ENVIRONMENT $DB_SYNC_START_ARGS >> $LOG_FILEPATH &
