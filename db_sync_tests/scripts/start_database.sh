#! /usr/bin/env nix-shell
#! nix-shell -i bash --keep LOG_FILEPATH --keep ENVIRONMENT --keep DB_SYNC_START_ARGS --keep POSTGRES_DIR --keep PGUSER -p glibcLocales postgresql lsof procps
# shellcheck shell=bash


cd cardano-db-sync

#test fix

cd config
wget -O ${ENVIRONMENT}-db-config.json https://book.world.dev.cardano.org/environments/${ENVIRONMENT}/db-sync-config.json

sed -i "s/NodeConfigFile.*/NodeConfigFile\": \"..\/..\/cardano-node\/${ENVIRONMENT}-config.json\",/g" "${ENVIRONMENT}-db-config.json"

cd ..

#end test fix

export PGPASSFILE=config/pgpass-$ENVIRONMENT
echo "${POSTGRES_DIR}:5432:${ENVIRONMENT}:${PGUSER}:*" > $PGPASSFILE
chmod 600 $PGPASSFILE

PGPASSFILE=$PGPASSFILE scripts/postgresql-setup.sh --createdb

nix-build -A cardano-db-sync -o db-sync-node

export DbSyncAbortOnPanic=1

PGPASSFILE=$PGPASSFILE db-sync-node/bin/cardano-db-sync --config config/${ENVIRONMENT}-db-config.json --socket-path ../cardano-node/db/node.socket --schema-dir schema/ --state-dir ledger-state/${ENVIRONMENT} ${DB_SYNC_START_ARGS} >> ${LOG_FILEPATH} &

#if [ "$ENVIRONMENT" = "shelley_qa" ];
#then
    #PGPASSFILE=$PGPASSFILE db-sync-node/bin/cardano-db-sync --config config/shelley-qa-config.json --socket-path ../cardano-node/db/node.socket --schema-dir schema/ --state-dir ledger-state/${ENVIRONMENT} ${DB_SYNC_START_ARGS} >> ${LOG_FILEPATH} &
#else
    #PGPASSFILE=$PGPASSFILE db-sync-node/bin/cardano-db-sync --config config/${ENVIRONMENT}-config.yaml --socket-path ../cardano-node/db/node.socket --schema-dir schema/ --state-dir ledger-state/${ENVIRONMENT} ${DB_SYNC_START_ARGS} >> ${LOG_FILEPATH} &
#fi
