# Dockerfile for cardano-node-tests (Antithesis-compatible driver image)
#
# All heavy dependencies are baked in at image build time so the container
# runs without any network access (required by Antithesis environments).
#
# Build args:
#   GIT_REVISION  — git commit hash stored as $GIT_REVISION in the image
#   NODE_REV      — cardano-node git ref to pre-build (default: master)
#
# Build and push to GHCR before submitting to Antithesis:
#   docker build -f docker/Dockerfile \
#     --build-arg GIT_REVISION=$(git rev-parse HEAD) \
#     --build-arg NODE_REV=master \
#     -t ghcr.io/saratomaz/cardano-node-tests-antithesis:latest .
#   docker push ghcr.io/saratomaz/cardano-node-tests-antithesis:latest

FROM nixos/nix:2.25.5

ARG GIT_REVISION
ARG NODE_REV=master

ENV GIT_REVISION=${GIT_REVISION}
# Store the baked-in node revision for reference at runtime.
ENV BAKED_NODE_REV=${NODE_REV}

# Configure Nix with IOG binary cache and required experimental features.
RUN mkdir -p /etc/nix && \
    printf 'extra-substituters = https://cache.iog.io\n\
extra-trusted-public-keys = hydra.iohk.io:f/Ea+s+dFdN+3Y/G+FDgSq+a5NEWhJGzdjvKNGv0/EQ=\n\
experimental-features = nix-command flakes\n\
accept-flake-config = true\n' >> /etc/nix/nix.conf

WORKDIR /work
COPY . /work/

# Pre-build cardano-node, cardano-submit-api, cardano-cli, and bech32 into /opt/cardano/.
# NODE_REV is fixed at image build time — no network access is needed at runtime.
RUN mkdir -p /opt/cardano && \
    nix build \
      --accept-flake-config --no-write-lock-file \
      "github://github.com/IntersectMBO/cardano-node?ref=${NODE_REV}#cardano-node" \
      -o /opt/cardano/cardano-node && \
    nix build \
      --accept-flake-config --no-write-lock-file \
      "github://github.com/IntersectMBO/cardano-node?ref=${NODE_REV}#cardano-submit-api" \
      -o /opt/cardano/cardano-submit-api && \
    nix build \
      --accept-flake-config --no-write-lock-file \
      "github://github.com/IntersectMBO/cardano-node?ref=${NODE_REV}#cardano-cli" \
      -o /opt/cardano/cardano-cli && \
    nix build \
      --accept-flake-config --no-write-lock-file \
      "github://github.com/IntersectMBO/cardano-node?ref=${NODE_REV}#bech32" \
      -o /opt/cardano/bech32

# Pre-warm the testenv dev shell (pulls nixpkgs, postgres, uv, python313 into the
# nix store) and create the Python venv at /opt/tests-venv with all project
# dependencies installed.  This is the same step regression.sh does at runtime
# but done here so no pip/uv network calls are needed in the Antithesis env.
RUN nix develop --accept-flake-config .#testenv --command \
    bash -c 'python3 -m venv /opt/tests-venv --prompt tests-venv && \
             . /opt/tests-venv/bin/activate && \
             cd /work && \
             uv sync --active --no-dev'

# Pre-warm the base dev shell (bash, coreutils, git, jq, …) so its store
# paths are cached and the regression.sh shebang resolves offline.
RUN nix develop --accept-flake-config .#base --command true

# Create the Antithesis test driver directory and install the entry-point.
# singleton_driver_* files are run once per test run by Antithesis.
RUN mkdir -p /opt/antithesis/test/v1/quickstart && \
    cp /work/docker/antithesis_run.sh \
       /opt/antithesis/test/v1/quickstart/singleton_driver_regression.sh && \
    chmod +x /opt/antithesis/test/v1/quickstart/singleton_driver_regression.sh
