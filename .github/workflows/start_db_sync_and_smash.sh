#!/bin/bash

db_sync_logfile="logs/db_sync_logfile.log"
smash_logfile="logs/smash_logfile.log"

function usage() {
    cat << HEREDOC

    arguments:
    -e          environment (network) - possible options: mainnet, shelley_qa
    -p          pr number that contains desired version of db-sync and SMASH that is not available on latest master
    -t          how many epochs db-sync should sync

    optional arguments:
    -h          show this help message and exit

	Example:

	./start_db_sync.sh -e shelley_qa -t 100

	USE UNDERSCORES IN NETWORK NAMES !!!
HEREDOC
}

while getopts ":h:e:p:t:" o; do
    case "${o}" in
        h)
            usage
            ;;
        e)
            environment=${OPTARG}
            ;;
        p)
            pr_no=${OPTARG}
            ;;
        t)
            expected_epochs_to_sync=${OPTARG}
            ;;
        *)
            echo "NO SUCH ARGUMENT: ${OPTARG}"
            usage
            ;;
    esac
done
if [ $? != 0 ] || [ $# == 0 ] ; then
    echo "ERROR: Error in command line arguments." >&2 ; usage; exit 1 ;
fi
shift $((OPTIND-1))

function get_latest_db_synced_epoch() {

	local log_filepath=$1
	IN=$(tail -n 1 $log_filepath)
	preformated_string=$(echo "$IN" | sed  's/^.*[Ee]poch/epoch/') # this will return: "Starting epoch 1038"

	IFS=' ' read -ra ADDR <<< "$preformated_string"
	for i in "${!ADDR[@]}"; do
		if [[ "${ADDR[$i]}" == *"epoch"* ]]; then # This is index $i for epoch keyword - we know that epoch number has ${i+1) position
	    	epoch_number=$(echo "${ADDR[$((i+1))]}" | sed 's/,[[:blank:]]*$//g') # use sed to remove comma at the end of slot number
            echo "$epoch_number"
	    fi
	done
}

echo "We are here: ${PWD}, script name is $0"
echo ""
echo "Entering cardano-db-sync directory"

cd cardano-db-sync
mkdir logs

DB_PR=""

if [[ ! -z "$pr_no" ]]
then
      DB_PR="-pr-${pr_no}"
fi


echo ""
echo "Downloading cardano-db-sync & SMASH archive:"

wget -q --content-disposition "https://hydra.iohk.io/job/Cardano/cardano-db-sync${DB_PR}/cardano-db-sync-linux/latest-finished/download/1/"

downloaded_archive=$(ls | grep tar)

echo ""
echo "Unpacking and removing archive ..."

mkdir extracted_files
tar -xzf $downloaded_archive -C extracted_files
mv extracted_files/cardano-db-sync _cardano-db-sync
mv extracted_files/cardano-smash-server _cardano-smash-server


echo ""
echo "db-sync version: "
echo ""
./_cardano-db-sync --version

echo ""
echo "SMASH version: "
echo ""
./_cardano-smash-server --version


if [ "$environment" = "shelley_qa" ]; then
    chmod 600 config/pgpass-shelley-qa-testnet
    PGPASSFILE=config/pgpass-shelley-qa-testnet scripts/postgresql-setup.sh --createdb
    PGPASSFILE=config/pgpass-shelley-qa-testnet ./_cardano-db-sync --config config/shelley-qa-config.json --socket-path ../cardano-node/shelley_qa/node.socket --schema-dir schema/ --state-dir ledger-state/shelley_qa >> $db_sync_logfile &
fi

if [ "$environment" = "testnet" ];then
    chmod 600 config/pgpass-testnet
    PGPASSFILE=config/pgpass-testnet scripts/postgresql-setup.sh --createdb
    cp ../cardano-node/testnet/testnet-db-sync-config.json config/
    sed -i "s/NodeConfigFile.*/NodeConfigFile\": \"..\/..\/cardano-node\/testnet\/testnet-config.json\",/g" config/testnet-db-sync-config.json
    PGPASSFILE=config/pgpass-testnet ./_cardano-db-sync --config config/testnet-db-sync-config.json --socket-path ../cardano-node/testnet/node.socket --schema-dir schema/ --state-dir ledger-state/testnet >> $db_sync_logfile &
fi

if [ "$environment" = "mainnet" ];then
    chmod 600 config/pgpass-mainnet
    PGPASSFILE=config/pgpass-mainnet scripts/postgresql-setup.sh --createdb
    PGPASSFILE=config/pgpass-mainnet ./_cardano-db-sync --config config/mainnet-config.yaml --socket-path ../cardano-node/mainnet/node.socket --schema-dir schema/ --state-dir ledger-state/mainnet >> $db_sync_logfile &
fi

sleep 3
cat $db_sync_logfile

echo ""
echo "Starting SMASH..."
echo "runner, password" >> admins.txt

if [ "$environment" = "shelley_qa" ]; then
PGPASSFILE=config/pgpass-shelley-qa-testnet ./_cardano-smash-server \
     --config config/shelley-qa-config.json \
     --port 3100 \
     --admins admins.txt >> $smash_logfile &
fi

if [ "$environment" = "testnet" ]; then
PGPASSFILE=config/pgpass-testnet ./_cardano-smash-server \
     --config config/testnet-db-sync-config.json \
     --port 3100 \
     --admins admins.txt >> $smash_logfile &
fi


if [ "$environment" = "mainnet" ]; then
PGPASSFILE=config/pgpass-mainnet ./_cardano-smash-server \
     --config config/mainnet-config.yaml \
     --port 3100 \
     --admins admins.txt >> $smash_logfile &
fi

sleep 3
cat  $smash_logfile

echo ""
echo "Waiting for db-sync to sync $expected_epochs_to_sync epochs ..."

latest_db_synced_epoch=$(get_latest_db_synced_epoch $db_sync_logfile)

re='^[0-9]+$'
while ! [[ $latest_db_synced_epoch =~ $re ]] ; do
   echo "Not a epoch number, waiting for proper log line that contains epoch number..."
   sleep 1
   latest_db_synced_epoch=$(get_latest_db_synced_epoch $db_sync_logfile)
done

tmp_latest_db_synced_epoch=$latest_db_synced_epoch

while [ "$latest_db_synced_epoch" -lt "$expected_epochs_to_sync" ]
do
	sleep 60
    echo "Latest epoch: $latest_db_synced_epoch"
	latest_db_synced_epoch=$(get_latest_db_synced_epoch $db_sync_logfile)

	if ! [[ $latest_db_synced_epoch =~ $re ]] ; then
		latest_db_synced_epoch=$tmp_latest_db_synced_epoch
    	continue
	fi
done

echo "Latest synced epoch before starting SMASH tests: $latest_db_synced_epoch"

#Uncomment for db contents diagnostics:
#psql -P pager=off -U runner -d "testnet" -c "select * from pool_offline_fetch_error LIMIT 5;"
#psql -P pager=off -U runner -d "testnet" -c "select * from pool_retire LIMIT 5;"
#psql -P pager=off -U runner -d "testnet" -c "select * from pool_hash LIMIT 20;"
