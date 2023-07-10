#!/bin/bash

db_sync_logfile="logs/db_sync_logfile.log"
smash_logfile="logs/smash_logfile.log"

function usage() {
    cat << HEREDOC

    arguments:
    -e          environment - possible options: mainnet, preprod, preview, shelley-qa
    -v          version - desired version number of db-sync and SMASH
    -t          time as epochs - how many epochs db-sync should sync

    optional arguments:
    -h          show this help message and exit

	Example:

	./start_db_sync.sh -e shelley-qa -v 13.1.1.3 -t 100

HEREDOC
}

while getopts ":h:e:v:t:" o; do
    case "${o}" in
        h)
            usage
            ;;
        e)
            environment=${OPTARG}
            ;;
        v)
            version=${OPTARG}
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


echo ""
echo "Downloading cardano-db-sync & SMASH archive:"


wget -q "https://update-cardano-mainnet.iohk.io/cardano-db-sync/cardano-db-sync-${version}-linux.tar.gz"
downloaded_archive=$(ls | grep tar)
mkdir extracted_files


echo ""
echo "Unpacking and removing archive ..."

tar -xzf $downloaded_archive -C extracted_files
mv extracted_files/cardano-db-sync _cardano-db-sync
mv extracted_files/cardano-smash-server _cardano-smash-server
rm $downloaded_archive


echo ""
echo "db-sync version: "
echo ""
./_cardano-db-sync --version

echo ""
echo "SMASH version: "
echo ""
./_cardano-smash-server --version


if [ "$environment" = "shelley-qa" ]; then
    chmod 600 config/pgpass-shelley-qa
    PGPASSFILE=config/pgpass-shelley-qa scripts/postgresql-setup.sh --createdb
    PGPASSFILE=config/pgpass-shelley-qa ./_cardano-db-sync --config config/shelley-qa-config.json --socket-path ../cardano-node/shelley-qa/node.socket --schema-dir schema/ --state-dir ledger-state/shelley-qa >> $db_sync_logfile &
fi

if [ "$environment" = "preview" ];then
    touch config/pgpass-preview
    echo "/var/run/postgresql:5432:preview:*:*" > config/pgpass-preview
    chmod 600 config/pgpass-preview
    PGPASSFILE=config/pgpass-preview scripts/postgresql-setup.sh --createdb
    cp config/shelley-qa-config.json config/preview-config.json
    sed -i "s/NodeConfigFile.*/NodeConfigFile\": \"..\/..\/cardano-node\/preview\/config.json\",/g" config/preview-config.json
    PGPASSFILE=config/pgpass-preview ./_cardano-db-sync --config config/preview-config.json --socket-path ../cardano-node/preview/node.socket --schema-dir schema/ --state-dir ledger-state/preview >> $db_sync_logfile &
fi

if [ "$environment" = "preprod" ];then
    touch config/pgpass-preprod
    echo "/var/run/postgresql:5432:preprod:*:*" > config/pgpass-preprod
    chmod 600 config/pgpass-preprod
    PGPASSFILE=config/pgpass-preprod scripts/postgresql-setup.sh --createdb
    cp config/shelley-qa-config.json config/preprod-config.json
    sed -i "s/NetworkName.*/NetworkName\": \"preprod\",/g" config/preprod-config.json
    sed -i "s/NodeConfigFile.*/NodeConfigFile\": \"..\/..\/cardano-node\/preprod\/config.json\",/g" config/preprod-config.json
    PGPASSFILE=config/pgpass-preprod ./_cardano-db-sync --config config/preprod-config.json --socket-path ../cardano-node/preprod/node.socket --schema-dir schema/ --state-dir ledger-state/preprod >> $db_sync_logfile &
fi

if [ "$environment" = "mainnet" ];then
    chmod 600 config/pgpass-mainnet
    PGPASSFILE=config/pgpass-mainnet scripts/postgresql-setup.sh --createdb
    PGPASSFILE=config/pgpass-mainnet ./_cardano-db-sync --config config/mainnet-config.yaml --socket-path ../cardano-node/mainnet/node.socket --schema-dir schema/ --state-dir ledger-state/mainnet >> $db_sync_logfile &
fi

sleep 3
cat $db_sync_logfile

echo ""
echo ""
echo "Starting SMASH..."
echo "runner, password" >> admins.txt

if [ "$environment" = "shelley-qa" ]; then
PGPASSFILE=config/pgpass-shelley-qa ./_cardano-smash-server \
     --config config/shelley-qa-config.json \
     --port 3100 \
     --admins admins.txt >> $smash_logfile &
fi

if [ "$environment" = "preview" ]; then
PGPASSFILE=config/pgpass-preview ./_cardano-smash-server \
     --config config/preview-config.json \
     --port 3100 \
     --admins admins.txt >> $smash_logfile &
fi

if [ "$environment" = "preprod" ]; then
PGPASSFILE=config/pgpass-preprod ./_cardano-smash-server \
     --config config/preprod-config.json \
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
echo ""
echo "Waiting for db-sync to sync $expected_epochs_to_sync epochs ..."
echo ""

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
	latest_db_synced_epoch=$(get_latest_db_synced_epoch $db_sync_logfile)

	if ! [[ $latest_db_synced_epoch =~ $re ]] ; then
		latest_db_synced_epoch=$tmp_latest_db_synced_epoch
    	continue
    else
        echo "Latest epoch: $latest_db_synced_epoch"
	fi
done

echo "Latest synced epoch before starting SMASH tests: $latest_db_synced_epoch"

#Uncomment for db contents diagnostics:
#psql -P pager=off -U runner -d "testnet" -c "select * from pool_offline_fetch_error LIMIT 5;"
#psql -P pager=off -U runner -d "testnet" -c "select * from pool_retire LIMIT 5;"
#psql -P pager=off -U runner -d "testnet" -c "select * from pool_hash LIMIT 20;"
