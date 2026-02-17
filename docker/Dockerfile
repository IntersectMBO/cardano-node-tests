# Dockerfile for cardano-node-tests (Antithesis/Moog driver image)
#
# Minimal image: nix is configured and the repo is copied in.
# All heavy setup (cardano binaries, Python venv) happens at runtime
# via regression.sh, which manages its own nix environment through its shebang.
#
# Build and push to GHCR before submitting to Moog:
#   docker build -f docker/Dockerfile -t ghcr.io/intersectmbo/cardano-node-tests-antithesis:latest .
#   docker push ghcr.io/intersectmbo/cardano-node-tests-antithesis:latest

FROM nixos/nix:2.25.5

ARG GIT_REVISION
ENV GIT_REVISION=${GIT_REVISION}

RUN mkdir -p /etc/nix && \
    echo "extra-substituters = https://cache.iog.io" >> /etc/nix/nix.conf && \
    echo "extra-trusted-public-keys = hydra.iohk.io:f/Ea+s+dFdN+3Y/G+FDgSq+a5NEWhJGzdjvKNGv0/EQ=" >> /etc/nix/nix.conf && \
    echo "experimental-features = nix-command flakes" >> /etc/nix/nix.conf && \
    echo "accept-flake-config = true" >> /etc/nix/nix.conf

WORKDIR /work
COPY . /work/
