#! /usr/bin/env nix-shell
#! nix-shell -p zip unzip git
#! nix-shell -I nixpkgs=./nix

: "${sshkey:=/run/keys/buildkite-ssh-iohk-devops-private}"
echo "Authenticating push using SSH with $sshkey"
export GIT_SSH_COMMAND="ssh -i $sshkey -F /dev/null"
remote="git@github.com:input-output-hk/cardano-node-tests.git"

git config --global user.name "sync_tests"
git config --global user.email "action@github.com"

zip -9 -k sync_tests/node_db.zip sync_tests/node_sync_tests_results.db
git add sync_tests/node_db.zip
git add sync_tests/csv_files

git commit -m "added mainnet sync test values"
git push origin HEAD:sync_tests --force
