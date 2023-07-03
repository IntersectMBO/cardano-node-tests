#!/usr/bin/env bash

set -euo pipefail

TESTNET_MAGIC="${TESTNET_MAGIC:-$(<relay1-db/protocolMagicId)}"
TESTNET_NAME="${TESTNET_NAME:-preview}"

if [ ! -e shelley/faucet.addr ]; then
  echo "Creating faucet address"
  mkdir -p shelley
  cardano-cli address key-gen --verification-key-file shelley/faucet.vkey --signing-key-file shelley/faucet.skey
  cardano-cli address build --payment-verification-key-file shelley/faucet.vkey --testnet-magic "$TESTNET_MAGIC" --out-file shelley/faucet.addr
fi

if [ -n "${APIKEY:-""}" ]; then
  ADDR="$(<shelley/faucet.addr)"
  echo "Fund the faucet address '$ADDR'? Press Ctrl+C to cancel."
  read -r

  curl -v -XPOST "https://faucet.${TESTNET_NAME}.dev.cardano.org/send-money/${ADDR}?apiKey=${APIKEY}"
  sleep 10
  cardano-cli query utxo --out-file /dev/stdout --testnet-magic "$TESTNET_MAGIC" --address "$ADDR"
  echo
fi
