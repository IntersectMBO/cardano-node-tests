#!/usr/bin/env bash

if command -v podman > /dev/null; then
  container_manager="podman"
elif command -v docker > /dev/null; then
  container_manager="docker"
else
  echo "Neither podman nor docker are installed. Please install one of them and try again." >&2
  exit 1
fi

GIT_OBJECT=""
DISTRO=""

while getopts "o:d:" flag; do
  case "${flag}" in
    o) GIT_OBJECT=${OPTARG};;
    d) DISTRO=${OPTARG};;
    *) echo "Error in command line parsing" >&2
       exit 1
       ;;
  esac
done

# Validate required arguments
if [ -z "$GIT_OBJECT" ]; then
  echo "Error: -o <git-object> argument is required." >&2
  exit 1
fi

# Validate that DISTRO was provided
if [ -z "$DISTRO" ]; then
  echo "Error: Missing required -d flag for distro." >&2
  exit 1
fi

# Determine correct base image and tag
case "$DISTRO" in
  fedora)
    BASE_IMAGE="docker.io/library/fedora:latest"
    TAG="cardano-node-fedora"
    ;;
  ubuntu)
    BASE_IMAGE="docker.io/library/ubuntu:latest"
    TAG="cardano-node-ubuntu"
    ;;
  *)
    echo "Unsupported distro: $DISTRO. Use 'ubuntu' or 'fedora'." >&2
    exit 1
    ;;
esac

echo "Using base image: $BASE_IMAGE"
echo "Building image: $TAG"

$container_manager build . \
  -f Dockerfile \
  --build-arg BASE_IMAGE="$BASE_IMAGE" \
  -t "$TAG" \
  || exit 1

$container_manager run \
  --security-opt label=disable \
  -it \
  -e GIT_OBJECT="$GIT_OBJECT" \
  -e KEEP_RUNNING="${KEEP_RUNNING:-1}" \
  "$TAG"
