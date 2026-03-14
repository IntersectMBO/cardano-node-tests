# Dockerfile for cardano-node-tests (Antithesis/Moog driver image)
#
# Minimal image: nix is configured and the repo is copied in.
# All heavy setup (cardano binaries, Python venv) happens at runtime
# via regression.sh, which manages its own nix environment through its shebang.
#
# Build and push to GHCR before submitting to Moog:
#   docker build -f docker/Dockerfile -t ghcr.io/intersectmbo/cardano-node-tests:antithesis .
#   docker push ghcr.io/intersectmbo/cardano-node-tests:antithesis

FROM nixos/nix:2.25.5

RUN mkdir -p /etc/nix && \
    echo "extra-substituters = https://cache.iog.io" >> /etc/nix/nix.conf && \
    echo "extra-trusted-public-keys = hydra.iohk.io:f/Ea+s+dFdN+3Y/G+FDgSq+a5NEWhJGzdjvKNGv0/EQ=" >> /etc/nix/nix.conf && \
    echo "experimental-features = nix-command flakes" >> /etc/nix/nix.conf && \
    echo "accept-flake-config = true" >> /etc/nix/nix.conf && \
    echo "allow-import-from-derivation = true" >> /etc/nix/nix.conf

WORKDIR /work
COPY . /work/

# Place the singleton_driver script where Antithesis expects it.
# The wrapper calls regression.sh directly so that regression.sh resolves
# its own REPODIR correctly from its own $0.
RUN mkdir -p /opt/antithesis/test/v1/regression && \
    printf '#!/usr/bin/env bash\nexec /work/.github/regression.sh\n' \
        > /opt/antithesis/test/v1/regression/singleton_driver_regression.sh && \
    chmod +x /opt/antithesis/test/v1/regression/singleton_driver_regression.sh
