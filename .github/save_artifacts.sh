#!/usr/bin/env bash

set -euo pipefail

ARTIFACTS_TAR="${PWD}/testing_artifacts.tar.xz"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"
if [ "$(echo "$ARTIFACTS_DIR"/*)" = "$ARTIFACTS_DIR/*" ]; then
  echo "No artifacts found in $ARTIFACTS_DIR" >&2
  exit 1
fi

NEW_DIR="artifacts_$(date +%Y%m%d%H%M%S)"
mv "$ARTIFACTS_DIR" "$NEW_DIR" || { echo "Cannot move $ARTIFACTS_DIR to $NEW_DIR" >&2; exit 1; }

echo "Creating artifacts archive $ARTIFACTS_TAR"
rm -f "$ARTIFACTS_TAR"
tar -cJf "$ARTIFACTS_TAR" "$NEW_DIR"
