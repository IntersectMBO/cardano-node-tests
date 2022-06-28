#!/bin/bash

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

docker build . -f ubuntu/Dockerfile -t cardano-node-ubuntu && \
	docker run --security-opt label:disable -it -e GIT_OBJECT_TYPE="$GIT_OBJECT_TYPE" -e GIT_OBJECT="$GIT_OBJECT" cardano-node-ubuntu
