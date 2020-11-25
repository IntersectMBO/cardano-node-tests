#!/bin/bash

docker build . -f fedora/Dockerfile -t cardano-node-fedora && \
	docker run -it -e TAGGED_VERSION=$1 cardano-node-fedora
