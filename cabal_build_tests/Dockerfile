ARG BASE_IMAGE
FROM ${BASE_IMAGE}

COPY install-node.sh /
RUN chmod +x /install-node.sh

ENTRYPOINT ["./install-node.sh"]
