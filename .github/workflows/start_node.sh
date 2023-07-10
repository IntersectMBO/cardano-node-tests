#!/bin/bash

node_logfile="logs/node_logfile.log"

function usage() {
    cat << HEREDOC

    arguments:
    -e          environment - possible options: mainnet, preprod, preview, shelley-qa
    -t          tag - e.g. 8.1.0-pre or 8.1.1 - sometimes tags have non numeric suffix
    -v          version - e.g. 8.0.0, 8.1.1

    optional arguments:
    -h        show this help message and exit

Example:

./start_node.sh -e shelley-qa -t 8.1.0-pre -v 8.1.0

HEREDOC
}

while getopts ":h:e:t:v:" o; do
    case "${o}" in
        h)
            usage
            ;;
        e)
            environment=${OPTARG}
            ;;
        t)
            tag=${OPTARG}
            ;;
        v)
            version=${OPTARG}
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

echo ""
echo "Downloading cardano-node & cli archive:"

wget -q --content-disposition "https://github.com/input-output-hk/cardano-node/releases/download/${tag}/cardano-node-${version}-linux.tar.gz"
downloaded_archive=$(ls | grep tar)

echo ""
echo "Unpacking and removing archive ..."

tar -xf $downloaded_archive
rm $downloaded_archive

echo ""
echo "Downloading node configuration files from book.world.dev.cardano.org for ${environment}  ..."
echo ""

# Get latest configs for environment you need:

mkdir ${environment}
cd ${environment}
echo "Node configuration files located in ${PWD}:"
wget --quiet "https://book.world.dev.cardano.org/environments/${environment}/config.json"
wget --quiet "https://book.world.dev.cardano.org/environments/${environment}/db-sync-config.json"
wget --quiet "https://book.world.dev.cardano.org/environments/${environment}/submit-api-config.json"
wget --quiet "https://book.world.dev.cardano.org/environments/${environment}/topology.json"
wget --quiet "https://book.world.dev.cardano.org/environments/${environment}/byron-genesis.json"
wget --quiet "https://book.world.dev.cardano.org/environments/${environment}/shelley-genesis.json"
wget --quiet "https://book.world.dev.cardano.org/environments/${environment}/alonzo-genesis.json"
wget --quiet "https://book.world.dev.cardano.org/environments/${environment}/conway-genesis.json"
cd ..


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

./cardano-node run --topology ${environment}/topology.json --database-path ${environment}/db --socket-path ${environment}/node.socket --config ${environment}/config.json >> $node_logfile &

sleep 1

cat $node_logfile