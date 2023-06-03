#! /usr/bin/env nix-shell
#! nix-shell -i bash -p python39Full python39Packages.virtualenv python39Packages.pip python39Packages.pandas python39Packages.psutil python39Packages.requests python39Packages.pymysql
# ! nix-shell -I nixpkgs=./nix
# shellcheck shell=bash

set -xeuo pipefail

echo " ==== set WORKDIR"
WORKDIR="/scratch/workdir"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"

echo " ==== create and activate python virtual env"
python3 -m venv "$WORKDIR/.env_sync"
# shellcheck disable=SC1090,SC1091
. "$WORKDIR/.env_sync/bin/activate"

# shellcheck disable=SC2046
echo "test: $(python -c 'import sys, sys.prefix == sys.base_prefix')"

echo " ==== install packages into python virtual env"
python3 -m pip install blockfrost-python
python3 -m pip install GitPython
python3 -m pip install colorama

echo " ==== importing packages from nix (https://search.nixos.org/packages)"
python3 -c "import requests, pandas, psutil, pymysql;"

env=$1
build_mode=$2
node_rev1=$3
node_rev2=$4
tag_no1=$5
tag_no2=$6
node_topology1=$7
node_topology2=$8
node_start_arguments1=$9
node_start_arguments2=${10}

echo " ==== start sync test"
python ./sync_tests/node_sync_test.py -b "$build_mode" -e "$env" -t1 "$tag_no1" -t2 "$tag_no2" -r1 "$node_rev1" -r2 "$node_rev2" -n1 "$node_topology1" -n2 "$node_topology2" -a1="$node_start_arguments1" -a2="$node_start_arguments2"

echo "--- Prepare for adding sync test results to the AWS Database"
python ./sync_tests/node_write_sync_values_to_db.py -e "$env"
