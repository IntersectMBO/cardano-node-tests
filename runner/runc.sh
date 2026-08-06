#!/usr/bin/env bash
# Build a container image and run tests inside it.
# Run with -h/--help for usage details.
#
# See:
# * NixOS: <https://hub.docker.com/r/nixos/nix/tags>
# * Alpine: <https://hub.docker.com/_/alpine/tags>
# * Ubuntu: <https://hub.docker.com/_/ubuntu/tags>
# * Debian: <https://hub.docker.com/_/debian/tags>
# * Mint: <https://hub.docker.com/u/linuxmintd>

set -Eeuo pipefail

default_mint_version="22.3"

container_type=""     # empty = auto-detect
container_version="latest"
mint_version="$default_mint_version"
extra_mounts=()

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
                                    VERSION defaults to 'latest'.
  --debian-container[=VERSION]      Use a Debian container (requires host /nix).
                                    VERSION is the image tag, e.g. 'bookworm'.
                                    VERSION defaults to 'stable'.
  --mint-container[=VERSION]        Use a Linux Mint container (requires host
                                    /nix).  VERSION is the Mint version and
                                    defaults to '${default_mint_version}'.
                                    amd64 hosts only.
  --extra-mount=HOST_PATH:CONTAINER_PATH[:OPTIONS]
                                    Bind-mount an additional path from the host
                                    into the container.  Both paths must be
                                    absolute and the host path must exist.
                                    OPTIONS are passed to the container manager
                                    (e.g. 'ro').  Can be specified multiple
                                    times.

Examples:
  $script_name NODE_REV="10.7.0" UTXO_BACKEND=disk ./runner/regression.sh
  $script_name --nixos-container NODE_REV="10.7.0" UTXO_BACKEND=disk ./runner/regression.sh
  $script_name --ubuntu-container=24.04 NODE_REV="10.7.0" UTXO_BACKEND=disk ./runner/regression.sh
USAGE
}

# Exit with a usage error when an --option=VALUE argument has an empty VALUE.
require_value() {
  if [ -z "${1#*=}" ]; then
    echo "Error: ${1%%=*} requires a non-empty value." >&2
    exit 2
  fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)            usage; exit 0 ;;
    --nixos-container)    container_type="nixos";  container_version="latest"; shift ;;
    --nixos-container=*)  require_value "$1"; container_type="nixos";  container_version="${1#*=}"; shift ;;
    --ubuntu-container)   container_type="ubuntu"; container_version="latest"; shift ;;
    --ubuntu-container=*) require_value "$1"; container_type="ubuntu"; container_version="${1#*=}"; shift ;;
    --debian-container)   container_type="debian"; container_version="stable"; shift ;;
    --debian-container=*) require_value "$1"; container_type="debian"; container_version="${1#*=}"; shift ;;
    --mint-container)     container_type="mint";   mint_version="$default_mint_version"; shift ;;
    --mint-container=*)   require_value "$1"; container_type="mint"; mint_version="${1#*=}"; shift ;;
    --extra-mount)
      echo "Error: --extra-mount requires a value: --extra-mount=HOST_PATH:CONTAINER_PATH[:OPTIONS]." >&2
      exit 2
      ;;
    --extra-mount=*)
      mount_spec="${1#*=}"
      if [[ ! "$mount_spec" =~ ^/[^:]+:/[^:]+(:[a-zA-Z,]+)?$ ]]; then
        echo "Error: --extra-mount expects absolute HOST_PATH:CONTAINER_PATH, got '$mount_spec'." >&2
        exit 2
      fi
      if [ ! -e "${mount_spec%%:*}" ]; then
        echo "Error: --extra-mount host path '${mount_spec%%:*}' does not exist." >&2
        exit 2
      fi
      extra_mounts+=("-v" "$mount_spec")
      shift
      ;;
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

if [ -z "$container_type" ]; then
  # Auto-detect: Alpine with bind-mounted /nix when available, NixOS otherwise.
  if [ -d "/nix" ]; then
    container_type="alpine"
  else
    container_type="nixos"
  fi
fi
if command -v podman > /dev/null; then
  container_manager="podman"
elif command -v docker > /dev/null; then
  container_manager="docker"
else
  echo "Neither podman nor docker are installed. Please install one of them and try again." >&2
  exit 1
fi

script_dir="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$script_dir/.." && pwd)"
readonly REPO_DIR

# When running from a git worktree, .git is a file referencing the main repo's
# .git directory.  The path it points to won't exist inside the container unless
# we also mount the main .git directory at the same absolute path.
# Appends to the global extra_mounts array when a main .git dir must be mounted.
add_worktree_git_mount() {
  local repo_dir="$1"
  local gitdir main_git_dir

  [ -f "$repo_dir/.git" ] || return 0
  gitdir="$(sed -n 's/^gitdir: //p' "$repo_dir/.git" | head -n 1)"
  if [ -z "$gitdir" ]; then
    echo "Warning: '$repo_dir/.git' has no 'gitdir:' line; git will not work inside the container." >&2
    return 0
  fi
  if [[ "$gitdir" != /* ]]; then
    gitdir="$repo_dir/$gitdir"
  fi
  # Strip the trailing /worktrees/<name> to get the main .git dir
  main_git_dir="${gitdir%/worktrees/*}"
  if [ ! -d "$main_git_dir" ]; then
    echo "Warning: main git dir '$main_git_dir' not found; git will not work inside the container." >&2
    return 0
  fi
  echo "Git worktree detected; mounting main .git: $main_git_dir"
  extra_mounts+=("-v" "$main_git_dir:$main_git_dir")
}

add_worktree_git_mount "$REPO_DIR"

# Validate .bin contents for container compatibility.
# Two regimes exist:
# * Containers with the host /nix bind-mounted (alpine, ubuntu, debian, mint):
#   symlinks into /nix and binaries whose dynamic deps all live under /nix work.
# * NixOS containers with their own separate /nix store: host /nix paths do not
#   exist inside, so only statically-linked binaries work.
# Anything else (symlinks outside /nix, dynamically-linked system binaries,
# broken symlinks) is not guaranteed to exist inside the container and is
# rejected.  Non-binary entries (scripts, data files) carry no dynamic deps
# and are accepted when they can be identified as such.  Symlinks are always
# checked; other entries that are non-regular, empty, or exec-bit-less are
# ignored, since they either cannot run at all or behave identically inside
# and outside the container.
validate_bin_dir() {
  local bin_dir="$1"
  local container_type="$2"
  local bad=()
  local entries=()
  local f direct real b perms file_out file_rc ldd_out ldd_rc glob_restore

  if [ ! -d "$bin_dir" ]; then
    return 0
  fi
  if [ ! -r "$bin_dir" ] || [ ! -x "$bin_dir" ]; then
    echo "Error: cannot access '$bin_dir'; unable to validate its contents." >&2
    return 1
  fi

  # Collect all entries (including dotfiles) via globbing; combined with the
  # access check above, this cannot silently miss entries the way an
  # unchecked pipeline could.
  glob_restore="$(shopt -p nullglob dotglob || true)"
  shopt -s nullglob dotglob
  entries=("$bin_dir"/*)
  eval "$glob_restore"

  if [ ${#entries[@]} -eq 0 ]; then
    return 0
  fi

  for f in "${entries[@]}"; do
    # Broken symlink or non-existent
    if [ ! -e "$f" ]; then
      bad+=("$(basename "$f") (broken symlink)")
      continue
    fi
    # For symlinks, only the direct (one-hop) target is checked: intermediate
    # symlinks pointing outside /nix are not guaranteed to exist in the
    # container even if the fully-resolved path is under /nix, so only direct
    # /nix targets are supported.
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
    # Non-regular, empty, or exec-bit-less entries cannot misbehave in the
    # container: they cannot run at all, or (empty files) fail or no-op
    # identically inside and outside.  Skip them.
    # PATH lookup in the container runs as root, for which any exec bit
    # counts, so test the mode bits rather than the host user's access
    # ([ -x ] would miss e.g. a root-owned 0700 file).
    # Fail closed: if stat fails or emits something non-octal, validate the
    # entry anyway.
    perms="$(stat -c '%a' "$real" 2>/dev/null || echo 777)"
    [[ "$perms" =~ ^[0-7]+$ ]] || perms=777
    if ! { [ -f "$real" ] && [ -s "$real" ] && (( 8#$perms & 8#111 )); }; then
      continue
    fi
    if [ ! -r "$real" ]; then
      bad+=("$(basename "$f") (not readable; cannot verify)")
      continue
    fi
    # Classify regular files with file(1).  Only dynamically-linked ELF
    # binaries need dependency checks; statically-linked (incl. static-pie)
    # binaries work anywhere and non-ELF entries have no dynamic deps.
    if command -v file >/dev/null 2>&1; then
      file_rc=0
      # LC_ALL=C for stable output; capture stderr so failures are diagnosable.
      file_out="$(LC_ALL=C file -b "$real" 2>&1)" || file_rc=$?
      if [ "$file_rc" -ne 0 ] || [ -z "$file_out" ]; then
        bad+=("$(basename "$f") (cannot verify binary type: file(1) failed: ${file_out%%$'\n'*})")
        continue
      fi
      case "$file_out" in
        *ELF*dynamically\ linked*) ;;
        *) continue ;;
      esac
      if [ "$container_type" = "nixos" ]; then
        # NixOS container has its own /nix store; dynamic deps (whether under
        # the host /nix or system paths) will not exist inside it.
        bad+=("$(basename "$f") -> $real (dynamically linked; its deps will not exist in NixOS container with separate /nix store)")
        continue
      fi
    elif [ "$container_type" = "nixos" ]; then
      # Without file(1) we cannot tell static from dynamic binaries, and only
      # static ones work in a NixOS container.
      bad+=("$(basename "$f") (cannot verify binary type: 'file' not installed)")
      continue
    fi
    # Dynamically-linked (or, without file(1), unclassified) entry: check
    # that all dynamic deps live under the bind-mounted /nix.
    if ! command -v ldd >/dev/null 2>&1; then
      bad+=("$(basename "$f") -> $real (cannot verify dynamic deps: 'ldd' not installed)")
      continue
    fi
    ldd_rc=0
    # Capture stderr too: glibc ldd prints 'not a dynamic executable' there.
    # LC_ALL=C keeps the messages untranslated for the pattern match below.
    ldd_out="$(LC_ALL=C ldd "$real" 2>&1)" || ldd_rc=$?
    if [ -z "$ldd_out" ]; then
      if [ "$ldd_rc" -ne 0 ]; then
        bad+=("$(basename "$f") -> $real (cannot verify dynamic deps: ldd failed)")
      fi
      continue
    fi
    if grep -Evq '^[[:space:]]*(/nix/|[^[:space:]]+[[:space:]]*=>[[:space:]]*/nix/|linux-vdso|statically linked|ldd: warning:|.*not a dynamic executable)' \
        <<<"$ldd_out"; then
      bad+=("$(basename "$f") -> $real (dynamic deps outside /nix or unrecognized ldd output)")
    fi
  done

  if [ ${#bad[@]} -gt 0 ]; then
    echo "Error: the following .bin/ entries will not work inside the container:" >&2
    for b in "${bad[@]}"; do echo "  $b" >&2; done
    if [ "$container_type" = "nixos" ]; then
      echo "NixOS containers use their own /nix store; only statically-linked binaries are supported there." >&2
    else
      echo "Only symlinks into the bind-mounted host /nix and statically-linked binaries are supported." >&2
    fi
    return 1
  fi
}

# Select base image, tag, and runtime options based on the container type.
# Sets globals BASE_IMAGE, tag, and appends to nix_mounts.
select_image() {
  local container_type="$1"
  local container_version="$2"
  local mint_version="$3"
  local host_arch

  case "$container_type" in
    nixos)
      BASE_IMAGE="docker.io/nixos/nix:${container_version}"
      tag="cardano-tests-nixos"
      echo "NixOS container selected; /nix is provided by the base image."
      ;;
    alpine)
      echo "Host /nix found; mounting into Alpine container."
      BASE_IMAGE="docker.io/library/alpine:${container_version}"
      tag="cardano-tests-alpine"
      nix_mounts+=("-v" "/nix:/nix")
      ;;
    ubuntu|debian|mint)
      if [ ! -d "/nix" ]; then
        echo "Error: Host /nix not found; --${container_type}-container requires /nix on the host." >&2
        return 1
      fi
      nix_mounts+=("-v" "/nix:/nix")
      tag="cardano-tests-${container_type}"
      case "$container_type" in
        ubuntu) BASE_IMAGE="docker.io/library/ubuntu:${container_version}" ;;
        debian) BASE_IMAGE="docker.io/library/debian:${container_version}" ;;
        mint)
          host_arch="$(uname -m)"
          if [ "$host_arch" != "x86_64" ]; then
            echo "Error: Mint images are amd64-only; host architecture is ${host_arch}." >&2
            return 1
          fi
          BASE_IMAGE="docker.io/linuxmintd/mint${mint_version}-amd64:latest"
          ;;
      esac
      echo "Host /nix found; mounting into ${container_type} container."
      ;;
    *)
      echo "Error: Unknown container type '${container_type}'. Expected one of: nixos, alpine, ubuntu, debian, or mint." >&2
      return 1
      ;;
  esac
}

# NOTE: 'fn ... || exit 1' disables errexit inside the function body, so every
# failure path in these functions must be (and is) explicitly checked.
nix_mounts=()
select_image "$container_type" "$container_version" "$mint_version" || exit 1
validate_bin_dir "$REPO_DIR/.bin" "$container_type" || exit 1

echo "Using base image:  $BASE_IMAGE"
echo "Building image:    $tag"
echo "Repository:        $REPO_DIR"
echo "Command:           $*"
echo

$container_manager build "$script_dir" \
  --pull \
  -f "$script_dir/Dockerfile" \
  --build-arg BASE_IMAGE="$BASE_IMAGE" \
  -t "$tag"

tty_flag=()
if [ -t 0 ] && [ -t 1 ]; then
  tty_flag=("-t")
fi

# `seccomp=unconfined` is needed so GHC's RTS can call io_uring_setup
$container_manager run \
  --rm \
  --security-opt label=disable \
  --security-opt seccomp=unconfined \
  -i \
  "${tty_flag[@]}" \
  "${nix_mounts[@]}" \
  -v "$REPO_DIR":"$REPO_DIR" \
  "${extra_mounts[@]}" \
  -e REPO_DIR="$REPO_DIR" \
  "$tag" \
  "$@"
