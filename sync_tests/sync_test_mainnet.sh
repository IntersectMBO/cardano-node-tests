#!/bin/sh

# Script steps:
#	0. create a new directory for the current test 
#	1. download and extract the pre-build cardano-node archive
#	2. get the required node files (genesis, config, topology)
#	3. start the node 
#	4. wait for the node to be fully synced
#	5. calculate and return the sync speed (no of blocks per second = total no of blocks synced / time in seconds / 60)

SCRIPT_LOCATION=$(pwd)
TEST_LOCATION=$SCRIPT_LOCATION/env
CLI=$TEST_LOCATION/cardano-cli
NODE=$TEST_LOCATION/cardano-node

wait_node_to_sync() {
	timeout_seconds=90
	echo "Waiting for the node to be synced = 90 seconds without downloading any new chunk"
	previous_chunk=$(ls -t | head -n1)
	sleep 60
	newest_chunk=$(ls -t | head -n1)
	echo "  - previous_chunk: $previous_chunk"
	echo "  - newest_chunk: $newest_chunk"
	while [ $previous_chunk != $newest_chunk ]; do
		sleep $timeout_seconds
		previous_chunk=$newest_chunk
		newest_chunk=$(ls -t | head -n1)
		echo "  - newest_chunk: $newest_chunk"
	done
	echo "Sync done!"
}

#	0. create a new directory for the current test 
mkdir $TEST_LOCATION
cd $TEST_LOCATION

#	1. download and extract the pre-build cardano-node archieve
wget https://hydra.iohk.io/job/Cardano/cardano-node-pr-1983/cardano-node-linux/latest-finished/download/1/cardano-node-1.21.2-linux.tar.gz
tar -xzvf cardano-node-1.21.2-linux.tar.gz

#	2. get the required node files
wget https://hydra.iohk.io/job/Cardano/iohk-nix/cardano-deployment/latest-finished/download/1/mainnet-config.json
wget https://hydra.iohk.io/job/Cardano/iohk-nix/cardano-deployment/latest-finished/download/1/mainnet-byron-genesis.json
wget https://hydra.iohk.io/job/Cardano/iohk-nix/cardano-deployment/latest-finished/download/1/mainnet-shelley-genesis.json
wget https://hydra.iohk.io/job/Cardano/iohk-nix/cardano-deployment/latest-finished/download/1/mainnet-topology.json

#	3. start the node 
tmux new -d -s mySession

# Execute a command in the detached session
tmux send-keys -t mySession.0 "$NODE run \
	--topology mainnet-topology.json \
	--database-path db \
	--socket-path db/node.socket \
	--host-addr 0.0.0.0 \
	--port 3000 \
	--config mainnet-config.json" Enter

sleep 20

tmux detach -s mySession

#	4. wait for the node to be fully synced
$CLI --version

if [ ! -d $TEST_LOCATION/db ]; then
	echo "ERROR: db directory was not created"
fi

export CARDANO_NODE_SOCKET_PATH=$TEST_LOCATION/db/node.socket
cd $TEST_LOCATION/db/immutable

wait_node_to_sync

#	5. calculate and return the sync speed (no of blocks per second = total no of blocks synced / time in seconds)
oldest_file=$(ls -t | tail -n1)
oldest_file_seconds=$(date +%s -r $oldest_file)
current_seconds=$(date +%s)

echo "oldest_file: $oldest_file"

sync_time_seconds=$(( $current_seconds - $oldest_file_seconds))
echo "sync_time_seconds: $sync_time_seconds"

current_block=$($CLI shelley query tip --mainnet | jq -r '.blockNo')
echo "current_block: $current_block"

sync_speed_bps=$(( current_block / sync_time_seconds ))
echo "sync_speed_bps: $sync_speed_bps"

##	6. clean up
#echo "Deleting the test folder - $SCRIPT_LOCATION"
#cd $SCRIPT_LOCATION
#rm -rf $TEST_LOCATION
