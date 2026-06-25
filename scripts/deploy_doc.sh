#!/usr/bin/env bash

set -euo pipefail

top_dir="$(cd "$(dirname "$0")/.." && pwd)" || { echo "Cannot determine top dir, exiting." >&2; exit 1; }
cd "$top_dir"

doc_src="src_docs"

unset GITHUB_TOKEN

# check that "$doc_src" dir exists
if [ ! -d "$doc_src" ]; then
    echo "The '$doc_src' dir doesn't exist, are you in the right directory?"
    exit 1
fi

# shellcheck disable=SC1091
. "$top_dir/scripts/common.sh"

# check that correct virtual env is activated
assert_correct_venv "$top_dir"

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
make build-doc

# drop changes made automatically by `make build-doc`
if ! git diff-index --quiet HEAD -- "$doc_src"; then
  git stash -- "$doc_src"
  git stash drop
fi

# checkout the "github_pages" branch
git checkout github_pages

# reset the "github_pages" branch
git reset --hard upstream/master

# copy generated documention to 'docs' dir
rm -rf docs/*
cp -aT "$doc_src"/build/html docs

# stage changes
git add docs

# commit changes
git commit -n -m "Documentation update"

# push to origin/github_pages (upstream/github_pages)
printf "\n-------------------------------------------\n\n"
printf "👉  Check the documentation:\n    %s\n\n" "file://${PWD}/src_docs/build/html/index.html"
printf "👉  Push the changes:\n    git push -f origin github_pages\n    or\n    git push -f upstream github_pages\n\n"
printf "👉  Then switch back to '%s' branch:\n    git checkout %s\n" "$cur_branch" "$cur_branch"
