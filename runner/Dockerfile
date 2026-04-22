ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# Install bash and coreutils if not already present. Pure Alpine ships neither;
# the NixOS container is also Alpine-based but already includes both, so this
# is a no-op there.  coreutils provides a GNU env(1) with -S support, which is
# required for shebangs like:
#   #!/usr/bin/env -S nix develop --accept-flake-config .#base -c bash
# Busybox env does not support -S and would fail with "unrecognized option".
RUN if command -v apk > /dev/null 2>&1; then \
        apk add --no-cache bash coreutils shadow; \
    fi

# Install ca-certificates on Debian-based images.
RUN if command -v apt-get > /dev/null 2>&1; then \
        apt-get update && \
        apt-get install -y ca-certificates && \
        rm -rf /var/lib/apt/lists/*; \
    fi

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
