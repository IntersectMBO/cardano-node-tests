FROM docker.io/library/fedora:latest

# workaround for timezone manual intervention
ENV TZ=Europe/London
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

COPY install-node.sh /
RUN chmod +x /install-node.sh

ENTRYPOINT ["./install-node.sh"]
