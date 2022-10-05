#!/usr/bin/env bash

set -euo pipefail

# check that "src_docs" dir exists
if [ ! -d src_docs ]; then
    echo "The 'src_docs' dir doesn't exist, are you in the right directory?"
    exit 1
fi

# check that virtual env is activated
if [ -z "${VIRTUAL_ENV:-""}" ]; then
  echo "A python virtual env is not activated in this shell." >&2
  exit 1
fi

# check that current branch is "master"
cur_branch="$(git rev-parse --abbrev-ref HEAD)"
if [ "$cur_branch" != "master" ]; then
    printf "WARNING: You are in '%s' branch instead of 'master'! Press ENTER to contunue or CTRL+C to abort." "$cur_branch"
    read -r
fi

# check that there are no uncommitted changes
if ! git diff-index --quiet HEAD --; then
    echo "There are uncommitted changes, aborting."
    exit 1
fi

# build documentation
make doc

# check that there still are no uncommitted changes after building docs
if ! git diff-index --quiet HEAD --; then
    echo "There are uncommitted changes after running 'make doc', aborting."
    exit 1
fi

# checkout the "github_pages" branch
git checkout github_pages

# reset the "github_pages" branch
git reset --hard upstream/master

# copy generated documention to "src_docs" dir
rm -rf docs/*
cp -aT src_docs/build/html docs

# stage changes
git add docs

# commit changes
git commit -n -m "Documentation update"

# push to origin/github_pages (upstream/github_pages)
printf "\n-------------------------------------------\n\n"
printf "Push changes manually:\ngit push -f origin github_pages\nor\ngit push -f upstream github_pages\n\n"
printf "Then switch back to '%s' branch:\ngit checkout %s\n" "$cur_branch" "$cur_branch"
