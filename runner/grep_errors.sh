#!/usr/bin/env bash

ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"
ERR_LOGFILE="${PWD}/errors_all.log"

cd "$ARTIFACTS_DIR" || { echo "Cannot switch to $ARTIFACTS_DIR" >&2; exit 1; }
grep -r --include "*.stdout" --include "*.stderr" -Ei ":error:|failed|failure" . > "$ERR_LOGFILE" || :
