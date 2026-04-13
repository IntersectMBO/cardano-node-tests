#!/usr/bin/env bash
# Antithesis entrypoint for cardano-node-tests.
#
# Runs the full test suite without any network access by:
#   1. Forcing nix into offline mode (all store paths were pre-built into
#      the image by docker/Dockerfile).
#   2. Pointing regression.sh at the pre-built cardano binaries and Python
#      venv so it skips all download / build steps.
#   3. Emitting the Antithesis `setup_complete` lifecycle signal before
#      starting pytest.
#
# This file is installed at:
#   /opt/antithesis/test/v1/quickstart/singleton_driver_regression.sh
# and is also usable directly as the docker-compose `command`.

set -Eeuo pipefail

# ---------------------------------------------------------------------------
# 1. Force nix offline — all required store paths were pre-built into the
#    image.  This prevents nix from attempting any network calls at runtime,
#    which would fail inside the Antithesis environment.
# ---------------------------------------------------------------------------
echo "offline = true" >> /etc/nix/nix.conf

# ---------------------------------------------------------------------------
# 2. Tell regression.sh to use the pre-built binaries and Python venv that
#    were baked into the image at docker build time.
# ---------------------------------------------------------------------------
export CARDANO_PREBUILT_DIR=/opt/cardano
export _VENV_DIR=/opt/tests-venv

# ---------------------------------------------------------------------------
# 3. Emit the Antithesis setup_complete signal.
#    Written as JSONL to $ANTITHESIS_OUTPUT_DIR/sdk.jsonl.
#    Antithesis begins fault injection / test orchestration after receiving
#    this message.
# ---------------------------------------------------------------------------
_output_dir="${ANTITHESIS_OUTPUT_DIR:-/tmp/antithesis}"
mkdir -p "$_output_dir"
printf '{"antithesis_setup": {"status": "complete", "details": {"info": ["cardano-node-tests driver ready, node_rev=%s"]}}}\n' \
    "${BAKED_NODE_REV:-unknown}" >> "$_output_dir/sdk.jsonl"
unset _output_dir

# ---------------------------------------------------------------------------
# 4. Hand off to regression.sh.  The shebang in that script will invoke
#    `nix develop .#base` which now resolves entirely from the local nix
#    store (offline = true).
#
#    Do not exec directly: Antithesis treats any non-zero container exit
#    code (other than 137/143) as an error property violation.  Test
#    failures are expected and communicated via SDK assertions, not the
#    process exit code.  Always exit 0 so the container is not flagged.
# ---------------------------------------------------------------------------
set +e
/work/.github/regression.sh
_rc=$?
set -e
echo "regression.sh finished with exit code ${_rc}"
exit 0
