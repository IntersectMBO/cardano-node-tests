#!/usr/bin/env bash
# Build a container image and run tests inside it.
#
# If the host has /nix, it is bind-mounted into the container (Alpine by
# default, or a specific distro via --*-container).  Otherwise the NixOS
# container image is used, which has Nix pre-installed.
#
# Usage:
#   ./runc.sh [--nixos-container[=VERSION]] [--ubuntu-container[=VERSION]]
#            [--debian-container[=VERSION]] [--mint-container[=VERSION]]
#            [--extra-mount=HOST_PATH:CONTAINER_PATH]
#            '<command>'
#
# Options:
#   --nixos-container[=VERSION]   Force use of a NixOS container (/nix
#                                 pre-installed inside it, host /nix not
#                                 required).  VERSION defaults to 'latest'.
#   --ubuntu-container[=VERSION]  Use an Ubuntu container (requires host /nix).
#                                 VERSION is the image tag, e.g. '24.04'.
#   --debian-container[=VERSION]  Use a Debian container (requires host /nix).
#                                 VERSION is the image tag, e.g. 'bookworm'.
#   --mint-container[=VERSION]    Use a Linux Mint container (requires host
#                                 /nix).  VERSION is the image tag.
#   --extra-mount=HOST_PATH:CONTAINER_PATH  Bind-mount an additional path from
#                                 the host into the container.  Can be specified
#                                 multiple times to mount multiple paths.
# See:
# * NixOS: <https://hub.docker.com/r/nixos/nix/tags>
# * Ubuntu: <https://hub.docker.com/_/ubuntu/tags>
# * Debian: <https://hub.docker.com/_/debian/tags>
# * Mint: <https://hub.docker.com/u/linuxmintd>
#
# Examples:
#   ./runc.sh NODE_REV="10.7.0" UTXO_BACKEND=disk ./runner/regression.sh
#   ./runc.sh --nixos-container NODE_REV="10.7.0" UTXO_BACKEND=disk ./runner/regression.sh
#   ./runc.sh --ubuntu-container=24.04 NODE_REV="10.7.0" UTXO_BACKEND=disk ./runner/regression.sh

set -Eeuo pipefail

if command -v podman > /dev/null; then
  container_manager="podman"
elif command -v docker > /dev/null; then
  container_manager="docker"
else
  echo "Neither podman nor docker are installed. Please install one of them and try again." >&2
  exit 1
fi

CONTAINER_TYPE=""     # empty = auto-detect
CONTAINER_VERSION="latest"
MINT_VERSION="22.3"
EXTRA_MOUNTS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --nixos-container)    CONTAINER_TYPE="nixos";  shift ;;
    --nixos-container=*)  CONTAINER_TYPE="nixos";  CONTAINER_VERSION="${1#*=}"; shift ;;
    --ubuntu-container)   CONTAINER_TYPE="ubuntu"; shift ;;
    --ubuntu-container=*) CONTAINER_TYPE="ubuntu"; CONTAINER_VERSION="${1#*=}"; shift ;;
    --debian-container)   CONTAINER_TYPE="debian"; shift ;;
    --debian-container=*) CONTAINER_TYPE="debian"; CONTAINER_VERSION="${1#*=}"; shift ;;
    --mint-container)     CONTAINER_TYPE="mint";   shift ;;
    --mint-container=*)   CONTAINER_TYPE="mint";   MINT_VERSION="${1#*=}";      shift ;;
    --extra-mount=*)      EXTRA_MOUNTS+=("-v" "${1#*=}"); shift ;;
    --) shift; break ;;
    *) break ;;
  esac
done

CMD="$*"

# Validate required arguments
if [ -z "$CMD" ]; then
  echo "Error: No command provided." >&2
  echo "Usage: $0 [--nixos-container[=VERSION] | --ubuntu-container[=VERSION] | --debian-container[=VERSION] | --mint-container[=VERSION] | --extra-mount=HOST_PATH:CONTAINER_PATH] '<command>'" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# When running from a git worktree, .git is a file referencing the main repo's
# .git directory.  The path it points to won't exist inside the container unless
# we also mount the main .git directory at the same absolute path.
if [ -f "$REPO_DIR/.git" ]; then
  GITDIR="$(sed -n 's/^gitdir: //p' "$REPO_DIR/.git" | head -n 1)"
  if [ -n "$GITDIR" ]; then
    if [[ "$GITDIR" != /* ]]; then
      GITDIR="$REPO_DIR/$GITDIR"
    fi
    # Strip the trailing /worktrees/<name> to get the main .git dir
    MAIN_GIT_DIR="${GITDIR%%/worktrees/*}"
    if [ -d "$MAIN_GIT_DIR" ]; then
      echo "Git worktree detected; mounting main .git: $MAIN_GIT_DIR"
      EXTRA_MOUNTS+=("-v" "$MAIN_GIT_DIR:$MAIN_GIT_DIR")
    fi
  fi
fi

# Validate .bin contents for container compatibility.
# Nix-store symlinks work because /nix is always available in the container.
# Statically-linked ELF binaries work anywhere.
# Anything else (symlinks outside /nix, dynamically-linked system binaries,
# broken symlinks) will silently fail inside the container.
BIN_DIR="$REPO_DIR/.bin"
# shellcheck disable=SC2010
if [ -d "$BIN_DIR" ] && ls -A "$BIN_DIR" 2>/dev/null | grep -q .; then
  bad=()
  for f in "$BIN_DIR"/*; do
    # Broken symlink or non-existent
    if [ ! -e "$f" ]; then
      bad+=("$(basename "$f") (broken symlink)")
      continue
    fi
    # For symlinks, only the direct (one-hop) target matters: intermediate
    # symlinks pointing outside /nix won't exist in the container even if the
    # fully-resolved path is under /nix.
    if [ -L "$f" ]; then
      direct=$(readlink "$f")
      if [[ "$direct" == /nix/* ]]; then
        if [ "$CONTAINER_TYPE" = "nixos" ]; then
          # NixOS container has its own /nix store; host /nix paths will not exist inside it.
          bad+=("$(basename "$f") -> $direct (symlink to host /nix path; will not work in NixOS container with separate /nix store)")
        fi
        continue
      fi
      bad+=("$(basename "$f") -> $direct (symlink does not point directly into /nix)")
      continue
    fi
    real="$f"
    # Statically-linked ELF — works anywhere
    if command -v file >/dev/null 2>&1 && file "$real" 2>/dev/null | grep -q "statically linked"; then
      continue
    fi
    # Dynamically-linked binary: check that all deps live under /nix
    if ! command -v ldd >/dev/null 2>&1; then
      bad+=("$(basename "$f") -> $real (cannot verify dynamic deps: 'ldd' not installed)")
      continue
    fi
    if ldd "$real" 2>/dev/null | grep -Evq '^[[:space:]]*(/nix/|[^[:space:]]+[[:space:]]*=>[[:space:]]*/nix/|linux-vdso|statically linked)'; then
      bad+=("$(basename "$f") -> $real (not a Nix-store path; dynamic deps outside /nix)")
    fi
  done
  if [ ${#bad[@]} -gt 0 ]; then
    echo "Error: the following .bin/ entries will not work inside the container:" >&2
    for b in "${bad[@]}"; do echo "  $b" >&2; done
    echo "Only symlinks into the container's own /nix store and statically-linked binaries are supported." >&2
    exit 1
  fi
fi

# Select base image, tag, and runtime options based on the container type.
NIX_MOUNTS=()

case "$CONTAINER_TYPE" in
  nixos)
    BASE_IMAGE="docker.io/nixos/nix:${CONTAINER_VERSION}"
    TAG="cardano-tests-nixos"
    echo "NixOS container selected; /nix will be created inside the container."
    ;;
  ubuntu|debian|mint)
    if [ ! -d "/nix" ]; then
      echo "Error: Host /nix not found; --${CONTAINER_TYPE}-container requires /nix on the host." >&2
      exit 1
    fi
    NIX_MOUNTS+=("-v" "/nix:/nix")
    TAG="cardano-tests-${CONTAINER_TYPE}"
    case "$CONTAINER_TYPE" in
      ubuntu) BASE_IMAGE="docker.io/library/ubuntu:${CONTAINER_VERSION}" ;;
      debian) BASE_IMAGE="docker.io/library/debian:${CONTAINER_VERSION}" ;;
      mint)   BASE_IMAGE="docker.io/linuxmintd/mint${MINT_VERSION}-amd64:latest" ;;
    esac
    echo "Host /nix found; mounting into ${CONTAINER_TYPE} container."
    ;;
  "")
    # Auto-detect: Alpine with bind-mounted /nix when available, NixOS otherwise.
    if [ -d "/nix" ]; then
      echo "Host /nix found; mounting into Alpine container."
      BASE_IMAGE="docker.io/library/alpine:${CONTAINER_VERSION}"
      TAG="cardano-tests-alpine"
      NIX_MOUNTS+=("-v" "/nix:/nix")
    else
      echo "Host /nix not found; NixOS container will be used."
      BASE_IMAGE="docker.io/nixos/nix:${CONTAINER_VERSION}"
      TAG="cardano-tests-nixos"
    fi
    ;;
esac

echo "Using base image:  $BASE_IMAGE"
echo "Building image:    $TAG"
echo "Repository:        $REPO_DIR"
echo "Command:           $CMD"
echo

$container_manager build "$SCRIPT_DIR" \
  --pull \
  -f "$SCRIPT_DIR/Dockerfile" \
  --build-arg BASE_IMAGE="$BASE_IMAGE" \
  -t "$TAG" \
  || exit 1

# `seccomp=unconfined` is needed so GHC's RTS can call io_uring_setup
$container_manager run \
  --rm \
  --security-opt label=disable \
  --security-opt seccomp=unconfined \
  -it \
  "${NIX_MOUNTS[@]}" \
  -v "$REPO_DIR":"$REPO_DIR" \
  "${EXTRA_MOUNTS[@]}" \
  -e REPO_DIR="$REPO_DIR" \
  "$TAG" \
  "$CMD"
