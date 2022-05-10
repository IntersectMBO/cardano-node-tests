#!/usr/bin/env bash

ARTIFACTS_DIR="${ARTIFACTS_DIR:-".artifacts"}"
ARTIFACTS_TAR="$PWD/testing_artifacts.tar.xz"

NEW_DIR="artifacts_$(date +%Y%m%d%H%M%S)"
mv "$ARTIFACTS_DIR" "$NEW_DIR" || { echo "Cannot move $ARTIFACTS_DIR to $NEW_DIR"; ls -1a; exit 1; }

echo "Creating artifacts archive $ARTIFACTS_TAR"
rm -f "$ARTIFACTS_TAR"
tar -cJf "$ARTIFACTS_TAR" "$NEW_DIR"
