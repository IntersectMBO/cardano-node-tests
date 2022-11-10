#! /usr/bin/env -S nix develop --accept-flake-config .#python -c bash
# shellcheck shell=bash

pip install blockfrost-python

tag_no1=$1
tag_no2=$2
hydra_eval_no1=$3
hydra_eval_no2=$4

python ./sync_tests/node_sync_test.py -t1 "$tag_no1" -t2 "$tag_no2" -e "mainnet" -e1 "$hydra_eval_no1" -e2 "$hydra_eval_no2"
python ./sync_tests/node_write_sync_values_to_db.py -e "mainnet"
