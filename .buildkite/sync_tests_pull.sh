#!/usr/bin/env bash

: "${sshkey:=/run/keys/buildkite-ssh-iohk-devops-private}"
echo "Authenticating push using SSH with $sshkey"
export GIT_SSH_COMMAND="ssh -i $sshkey -F /dev/null"
remote="git@github.com:input-output-hk/cardano-node-tests.git"

git fetch origin
git merge origin/sync_tests
unzip node_db.zip && mv NODE_SYN.DB node_sync_tests_results.db
