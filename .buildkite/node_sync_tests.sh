#! /usr/bin/env nix-shell
#! nix-shell -i bash -p python39Full python39Packages.virtualenv python39Packages.pip
#! nix-shell -I nixpkgs=./nix
# shellcheck shell=bash

pip install pymysql
pip install requests
pip install psutil
pip install pandas
pip install blockfrost-python
