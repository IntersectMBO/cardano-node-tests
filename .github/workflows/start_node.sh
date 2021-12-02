#!/bin/bash

node_logfile="logs/node_logfile.log"

function usage() {
    cat << HEREDOC

    arguments:
    -e          environment - possible options: allegra, launchpad, mary_qa, mainnet, staging, testnet, shelley_qa
    -p          pr number that contains desired version of node that is not available on latest master

    optional arguments:
    -h        show this help message and exit

Example:

./start_node.sh -e shelley_qa -p 3410

USE UNDERSCORES IN environment NAMES !!!
HEREDOC
}

while getopts ":h:e:p:" o; do
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


echo "We are here: ${PWD}, script name is $0"
echo ""
echo "Creating cardano-node directory and entering it ..."

mkdir cardano-node
cd cardano-node
mkdir logs

NODE_PR=""

if [[ ! -z "$pr_no" ]]
then
      NODE_PR="-pr-${pr_no}"
fi

echo ""
echo "Downloading cardano-node & cli archive:"

wget -q --content-disposition "https://hydra.iohk.io/job/Cardano/cardano-node${NODE_PR}/cardano-node-linux/latest-finished/download/1/"
downloaded_archive=$(ls | grep tar)


echo ""
echo "Unpacking and removing archive ..."

tar -xf $downloaded_archive
rm $downloaded_archive

NODE_CONFIGS_URL=$(curl -Ls -o /dev/null -w %{url_effective} https://hydra.iohk.io/job/Cardano/iohk-nix/cardano-deployment/latest-finished/download/1/index.html | sed 's|\(.*\)/.*|\1|')

echo ""
echo "Downloading node configuration files from $NODE_CONFIGS_URL for environments specified in script ..."
echo ""

# Get latest configs for environment(s) you need:

for _environment in ${environment}
do
	mkdir ${_environment}
	cd ${_environment}
	echo "Node configuration files located in ${PWD}:"
	wget -q  $NODE_CONFIGS_URL/${_environment}-config.json
	wget -q  $NODE_CONFIGS_URL/${_environment}-byron-genesis.json
	wget -q  $NODE_CONFIGS_URL/${_environment}-shelley-genesis.json
	wget -q  $NODE_CONFIGS_URL/${_environment}-alonzo-genesis.json
	wget -q  $NODE_CONFIGS_URL/${_environment}-topology.json
	wget -q  $NODE_CONFIGS_URL/${_environment}-db-sync-config.json
	echo ""
	cd ..
done

echo ""
ls -l $environment

echo ""
echo "Node version: "
echo ""
./cardano-node --version

echo ""
echo "CLI version: "
echo ""
./cardano-cli --version


echo ""
echo ""
echo "Starting node."

./cardano-node run --topology ${environment}/${environment}-topology.json --database-path ${environment}/db --socket-path ${environment}/node.socket --config ${environment}/${environment}-config.json >> $node_logfile &

sleep 1

cat $node_logfile
