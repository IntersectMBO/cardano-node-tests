#!/usr/bin/env bash

ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"
LOGFILE="$PWD/errors_all.log"

# there should be only one dir matching "pytest-*" in the artifacts dir
pushd "$ARTIFACTS_DIR"/pytest-* || { echo "Cannot switch to $ARTIFACTS_DIR/pytest-*"; ls -1a "$ARTIFACTS_DIR"; exit 1; } > "$LOGFILE"
grep -r --include "*.stdout" --include "*.stderr" -Ei ":error:|failed|failure" . > "$LOGFILE"
popd || exit 1
