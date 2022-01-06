#! /usr/bin/env nix-shell
#! nix-shell -p zip unzip git
#! nix-shell -I nixpkgs=./nix

: "${sshkey:=/run/keys/buildkite-ssh-iohk-devops-private}"
echo "Authenticating push using SSH with $sshkey"
export GIT_SSH_COMMAND="ssh -i $sshkey -F /dev/null"
remote="git@github.com:input-output-hk/cardano-node-tests.git"

git fetch origin
git merge origin/sync_tests
unzip sync_tests/node_db.zip && mv sync_tests/NODE_SYN.DB sync_tests/node_sync_tests_results.db
rm -rf sync_tests/node_db.zip
