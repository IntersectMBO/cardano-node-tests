#!/usr/bin/env bash
# Build a container image and run tests inside it.
# Run with -h/--help for usage details.
#
# See:
# * NixOS: <https://hub.docker.com/r/nixos/nix/tags>
# * Ubuntu: <https://hub.docker.com/_/ubuntu/tags>
# * Debian: <https://hub.docker.com/_/debian/tags>
# * Mint: <https://hub.docker.com/u/linuxmintd>

set -Eeuo pipefail

CONTAINER_TYPE=""     # empty = auto-detect
CONTAINER_VERSION="latest"
MINT_VERSION="22.3"
EXTRA_MOUNTS=()

usage() {
  local script_name
  script_name="$(basename "$0")"

  cat <<USAGE
Usage: $script_name [OPTIONS] [--] command [args...]

Build a container image and run tests inside it.

The command and its arguments are passed through to the container as
argv (via env(1), so leading NAME=value tokens are applied as
environment variables for the command).  No extra shell escaping is
required.  If you need shell features like pipes, redirections, or
variable expansion, wrap the command explicitly, e.g.
'-- bash -c "cmd1 | cmd2"'.

Options:
  -h, --help                        Show this help message and exit.
  --nixos-container[=VERSION]       Force use of a NixOS container (/nix
                                    pre-installed inside it, host /nix not
                                    required).  VERSION defaults to 'latest'.
  --ubuntu-container[=VERSION]      Use an Ubuntu container (requires host /nix).
                                    VERSION is the image tag, e.g. '24.04'.
  --debian-container[=VERSION]      Use a Debian container (requires host /nix).
                                    VERSION is the image tag, e.g. 'bookworm'.
  --mint-container[=VERSION]        Use a Linux Mint container (requires host
                                    /nix).  VERSION is the Mint version.
  --extra-mount=HOST_PATH:CONTAINER_PATH
                                    Bind-mount an additional path from the host
                                    into the container.  Can be specified
                                    multiple times.

Examples:
  $script_name NODE_REV="10.7.0" UTXO_BACKEND=disk ./runner/regression.sh
  $script_name --nixos-container NODE_REV="10.7.0" UTXO_BACKEND=disk ./runner/regression.sh
  $script_name --ubuntu-container=24.04 NODE_REV="10.7.0" UTXO_BACKEND=disk ./runner/regression.sh
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)            usage; exit 0 ;;
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
    -*) echo "Error: Unknown option '$1'. Use -h for help." >&2; exit 2 ;;
    *) break ;;
  esac
done

if [ $# -eq 0 ]; then
  echo "Error: No command provided." >&2
  usage >&2
  exit 2
fi

if command -v podman > /dev/null; then
  container_manager="podman"
elif command -v docker > /dev/null; then
  container_manager="docker"
else
  echo "Neither podman nor docker are installed. Please install one of them and try again." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# When running from a git worktree, .git is a file referencing the main repo's
# .git directory.  The path it points to won't exist inside the container unless
# we also mount the main .git directory at the same absolute path.
# Appends to the global EXTRA_MOUNTS array when a main .git dir must be mounted.
add_worktree_git_mount() {
  local repo_dir="$1"
  local gitdir main_git_dir

  [ -f "$repo_dir/.git" ] || return 0
  gitdir="$(sed -n 's/^gitdir: //p' "$repo_dir/.git" | head -n 1)"
  [ -n "$gitdir" ] || return 0
  if [[ "$gitdir" != /* ]]; then
    gitdir="$repo_dir/$gitdir"
  fi
  # Strip the trailing /worktrees/<name> to get the main .git dir
  main_git_dir="${gitdir%%/worktrees/*}"
  [ -d "$main_git_dir" ] || return 0
  echo "Git worktree detected; mounting main .git: $main_git_dir"
  EXTRA_MOUNTS+=("-v" "$main_git_dir:$main_git_dir")
}

add_worktree_git_mount "$REPO_DIR"

# Validate .bin contents for container compatibility.
# Nix-store symlinks work because /nix is always available in the container.
# Statically-linked ELF binaries work anywhere.
# Anything else (symlinks outside /nix, dynamically-linked system binaries,
# broken symlinks) will silently fail inside the container.
validate_bin_dir() {
  local bin_dir="$1"
  local container_type="$2"
  local bad=()
  local f direct real b

  # shellcheck disable=SC2010
  if [ ! -d "$bin_dir" ] || ! ls -A "$bin_dir" 2>/dev/null | grep -q .; then
    return 0
  fi

  for f in "$bin_dir"/*; do
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
        if [ "$container_type" = "nixos" ]; then
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
    return 1
  fi
}

validate_bin_dir "$REPO_DIR/.bin" "$CONTAINER_TYPE" || exit 1

# Select base image, tag, and runtime options based on the container type.
# Sets globals BASE_IMAGE, TAG, and appends to NIX_MOUNTS.
select_image() {
  local container_type="$1"
  local container_version="$2"
  local mint_version="$3"

  case "$container_type" in
    nixos)
      BASE_IMAGE="docker.io/nixos/nix:${container_version}"
      TAG="cardano-tests-nixos"
      echo "NixOS container selected; /nix will be created inside the container."
      ;;
    ubuntu|debian|mint)
      if [ ! -d "/nix" ]; then
        echo "Error: Host /nix not found; --${container_type}-container requires /nix on the host." >&2
        return 1
      fi
      NIX_MOUNTS+=("-v" "/nix:/nix")
      TAG="cardano-tests-${container_type}"
      case "$container_type" in
        ubuntu) BASE_IMAGE="docker.io/library/ubuntu:${container_version}" ;;
        debian) BASE_IMAGE="docker.io/library/debian:${container_version}" ;;
        mint)   BASE_IMAGE="docker.io/linuxmintd/mint${mint_version}-amd64:latest" ;;
      esac
      echo "Host /nix found; mounting into ${container_type} container."
      ;;
    "")
      # Auto-detect: Alpine with bind-mounted /nix when available, NixOS otherwise.
      if [ -d "/nix" ]; then
        echo "Host /nix found; mounting into Alpine container."
        BASE_IMAGE="docker.io/library/alpine:${container_version}"
        TAG="cardano-tests-alpine"
        NIX_MOUNTS+=("-v" "/nix:/nix")
      else
        echo "Host /nix not found; NixOS container will be used."
        BASE_IMAGE="docker.io/nixos/nix:${container_version}"
        TAG="cardano-tests-nixos"
      fi
      ;;
    *)
      echo "Error: Unknown container type '${container_type}'. Expected one of: nixos, ubuntu, debian, mint, or empty for auto-detect." >&2
      return 1
      ;;
  esac
}

NIX_MOUNTS=()
select_image "$CONTAINER_TYPE" "$CONTAINER_VERSION" "$MINT_VERSION" || exit 1

echo "Using base image:  $BASE_IMAGE"
echo "Building image:    $TAG"
echo "Repository:        $REPO_DIR"
echo "Command:           $*"
echo

$container_manager build "$SCRIPT_DIR" \
  --pull \
  -f "$SCRIPT_DIR/Dockerfile" \
  --build-arg BASE_IMAGE="$BASE_IMAGE" \
  -t "$TAG" \
  || exit 1

TTY_FLAG=()
if [ -t 0 ] && [ -t 1 ]; then
  TTY_FLAG=("-t")
fi

# `seccomp=unconfined` is needed so GHC's RTS can call io_uring_setup
$container_manager run \
  --rm \
  --security-opt label=disable \
  --security-opt seccomp=unconfined \
  -i \
  "${TTY_FLAG[@]}" \
  "${NIX_MOUNTS[@]}" \
  -v "$REPO_DIR":"$REPO_DIR" \
  "${EXTRA_MOUNTS[@]}" \
  -e REPO_DIR="$REPO_DIR" \
  "$TAG" \
  "$@"
