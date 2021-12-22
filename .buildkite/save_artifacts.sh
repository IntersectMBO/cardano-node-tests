#!/usr/bin/env bash

ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"
ARTIFACTS_TAR="$PWD/testing_artifacts.tar.xz"

pushd "$ARTIFACTS_DIR" || { echo "Cannot switch to $ARTIFACTS_DIR"; ls -1a; exit 1; }
# shellcheck disable=SC2012
latest_artifacts="$(ls -1td --quoting-style=shell-escape pytest-* | head -1)"
popd || exit 1

echo "Creating artifacts archive $ARTIFACTS_TAR"
rm -f "$ARTIFACTS_TAR"
tar -C "$ARTIFACTS_DIR" -cJf "$ARTIFACTS_TAR" "$latest_artifacts"
