#!/bin/bash

docker build . -f ubuntu/Dockerfile -t cardano-node-ubuntu && \
	docker run --security-opt label:disable -it -e TAGGED_VERSION="$1" cardano-node-ubuntu
