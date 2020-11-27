#!/bin/bash

docker build . -f fedora/Dockerfile -t cardano-node-fedora && \
	docker run --security-opt label:disable -it -e TAGGED_VERSION="$1" cardano-node-fedora
