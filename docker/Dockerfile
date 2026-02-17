# Dockerfile for cardano-node-tests
# Ready-to-use environment for running tests directly

FROM nixos/nix:2.25.5

# Set labels
LABEL maintainer="cardano-node-tests team"
LABEL description="Functional tests for cardano-node - ready-to-use environment"

# Install system dependencies
RUN nix-channel --update && \
    nix-env -iA nixpkgs.bash \
                nixpkgs.coreutils \
                nixpkgs.curl \
                nixpkgs.git \
                nixpkgs.gnugrep \
                nixpkgs.gnutar \
                nixpkgs.jq \
                nixpkgs.xz \
                nixpkgs.procps \
                nixpkgs.findutils \
                nixpkgs.which \
                nixpkgs.gnused

# Configure Nix to use IOG cache
RUN mkdir -p /etc/nix && \
    echo "extra-substituters = https://cache.iog.io" >> /etc/nix/nix.conf && \
    echo "extra-trusted-public-keys = hydra.iohk.io:f/Ea+s+dFdN+3Y/G+FDgSq+a5NEWhJGzdjvKNGv0/EQ=" >> /etc/nix/nix.conf && \
    echo "experimental-features = nix-command flakes" >> /etc/nix/nix.conf && \
    echo "accept-flake-config = true" >> /etc/nix/nix.conf && \
    echo "allow-import-from-derivation = true" >> /etc/nix/nix.conf

# Set working directory
WORKDIR /work

# Copy project files
COPY . /work/

# Set environment variables
ENV WORKDIR=/work/run_workdir \
    TMPDIR=/work/run_workdir/tmp \
    ARTIFACTS_DIR=/work/.artifacts \
    SCHEDULING_LOG=/work/scheduling.log \
    PATH_PREPEND=/work/.bin \
    CARDANO_NODE_SOCKET_PATH=/work/run_workdir/state-cluster0/bft1.socket \
    CARDANO_NODE_SOCKET_PATH_CI=/work/run_workdir/state-cluster0/bft1.socket

# Pre-build Nix environments to cache dependencies
RUN nix develop --accept-flake-config .#base --command bash -c 'echo "Base environment ready"' && \
    nix develop --accept-flake-config .#testenv --command bash -c 'echo "Test environment ready"'

# Create required directories
RUN mkdir -p "$WORKDIR" "$TMPDIR" "$ARTIFACTS_DIR" /work/.bin && \
    touch "$SCHEDULING_LOG"

# Set up Python virtual environment with dependencies
RUN nix develop --accept-flake-config .#testenv --command bash -c '\
    export WORKDIR=/work/run_workdir && \
    mkdir -p "$WORKDIR/.venv" && \
    python3 -m venv "$WORKDIR/.venv" --prompt tests-venv && \
    . "$WORKDIR/.venv/bin/activate" && \
    uv sync --active --no-dev \
    '

# Build cardano-node binaries and make them available
RUN nix develop --accept-flake-config .#base --command bash -c '\
    export WORKDIR=/work/run_workdir && \
    export PATH_PREPEND=/work/.bin && \
    . scripts/common.sh && \
    . .github/source_cardano_node.sh && \
    cardano_bins_build_all "${NODE_REV:-master}" "" && \
    cardano_bins_print_path_prepend "" > /work/.path_prepend \
    '

# Create entrypoint script that sets up the environment
RUN cat > /work/entrypoint.sh <<'EOF'
#!/usr/bin/env bash
set -e

# Activate Python virtual environment
source /work/run_workdir/.venv/bin/activate

# Set up PATH with cardano binaries
if [ -f /work/.path_prepend ]; then
    export PATH="$(cat /work/.path_prepend):${PATH_PREPEND}:${PATH}"
fi

# Display environment info
echo "=========================================="
echo "Cardano Node Tests - Ready Environment"
echo "=========================================="
echo ""
echo "Python virtual environment: ACTIVATED"
echo "Cardano binaries: AVAILABLE"
echo ""
echo "Quick start:"
echo "  pytest cardano_node_tests/tests/           # Run all tests"
echo "  pytest cardano_node_tests/tests/ -m smoke  # Run smoke tests"
echo "  ./.github/run_tests.sh testpr              # Run PR tests"
echo ""
echo "Current directory: $(pwd)"
echo "=========================================="
echo ""

# Execute command or start bash
exec "$@"
EOF

RUN chmod +x /work/entrypoint.sh

# Set the entrypoint to activate environment
ENTRYPOINT ["/work/entrypoint.sh"]

# Default command: start bash shell
CMD ["bash"]
