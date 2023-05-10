#!/bin/bash

if command -v podman > /dev/null; then
  container_manager="podman"
elif command -v docker > /dev/null; then
  container_manager="docker"
fi

GIT_OBJECT_TYPE=""
GIT_OBJECT=""

while getopts t:o: flag
do
  case "${flag}" in
    t) GIT_OBJECT_TYPE=${OPTARG};;
    o) GIT_OBJECT=${OPTARG};;
    *) echo "Error in command line parsing" >&2
      exit 1
  esac
done

$container_manager build . -f fedora/Dockerfile -t cardano-node-fedora || exit 1
$container_manager run \
  --security-opt label:disable \
  -it \
  -e GIT_OBJECT_TYPE="$GIT_OBJECT_TYPE" \
  -e GIT_OBJECT="$GIT_OBJECT" \
  cardano-node-fedora
