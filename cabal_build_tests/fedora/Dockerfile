FROM fedora:latest

# workaround for timezone manual intervention
ENV TZ=Europe/London
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

ENV TAGGED_VERSION ''
COPY install-node.sh /
RUN chmod +x /install-node.sh

CMD ./install-node.sh $TAGGED_VERSION
