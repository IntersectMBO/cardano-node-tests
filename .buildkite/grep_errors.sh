#!/usr/bin/env bash

ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"
LOGFILE="$PWD/errors_all.log"

# shellcheck disable=SC2012
latest_artifacts="$(ls -1td --quoting-style=shell-escape "$ARTIFACTS_DIR"/pytest-* | head -1)"
pushd "$latest_artifacts" || { echo "Cannot switch to $latest_artifacts"; ls -1a "$ARTIFACTS_DIR"; exit 1; } > "$LOGFILE"
grep -r --include "*.stdout" --include "*.stderr" -Ei ":error:|failed|failure" . > "$LOGFILE"
popd || exit 1
