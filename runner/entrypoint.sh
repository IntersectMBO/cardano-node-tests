#!/bin/sh
# Entrypoint for running regression tests inside a container.
#
# Sources the nix profile to make 'nix' available in PATH, then executes the
# command passed as arguments inside the repo directory (REPO_DIR env var).
# Works whether /nix is bind-mounted from the host or nix was installed inside
# the container at image-build time (single-user, no-daemon install).
#
# The command is run via env(1), so leading NAME=value arguments become
# environment variables for the command itself.
#
# Usage (via runc.sh):
#   ./runc.sh -- NODE_REV=10.7.0 MARKEXPR='not long' ./runner/regression.sh

set -eu

# Set up nix config: enable flakes and nix-command.
mkdir -p /root/.config/nix
cat > /root/.config/nix/nix.conf << 'EOF'
experimental-features = nix-command flakes
allow-import-from-derivation = true
substituters = https://cache.nixos.org https://cache.iog.io
trusted-public-keys = hydra.iohk.io:f/Ea+s+dFdN+3Y/G+FDgSq+a5NEWhJGzdjvKNGv0/EQ= cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY=
EOF

# Source the nix profile to add nix to PATH and set up NIX_PATH etc.
# Search order: multi-user profile (host bind-mount or Determinate Systems
# installer), legacy single-user system profile, legacy single-user per-root
# profile.
for _nix_profile in \
  "/nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh" \
  "/nix/var/nix/profiles/default/etc/profile.d/nix.sh" \
  "/root/.nix-profile/etc/profile.d/nix.sh"
do
    if [ -f "$_nix_profile" ]; then
      # shellcheck source=/dev/null
      . "$_nix_profile"
      break
    fi
  done

if ! command -v nix > /dev/null; then
  echo "ERROR: 'nix' not found in PATH after sourcing nix profile." \
       "Either mount /nix from the host or ensure the image is NixOS."
  exit 1
fi

cd "${REPO_DIR:?REPO_DIR is not set}" || exit 1

# Use env(1) so leading NAME=value arguments are applied as environment
# variables for the command, matching the behaviour of a plain shell command
# line without needing bash -c (which would require the caller to pre-quote
# everything into a single string).
exec env -- "$@"
