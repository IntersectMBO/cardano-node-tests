#!/usr/bin/env bash

ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"
ERR_LOGFILE="$PWD/errors_all.log"

# shellcheck disable=SC2012
pushd "$ARTIFACTS_DIR" || { echo "Cannot switch to $ARTIFACTS_DIR"; ls -1a "$ARTIFACTS_DIR"; exit 1; } > "$ERR_LOGFILE"
grep -r --include "*.stdout" --include "*.stderr" -Ei ":error:|failed|failure" . > "$ERR_LOGFILE"
popd || exit 1
