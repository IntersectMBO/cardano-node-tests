FROM fedora:latest

# workaround for timezone manual intervention
ENV TZ=Europe/London
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

COPY install-node.sh /
RUN yum update -y \
    && yum install curl diffutils awk -y \
    && chmod +x /install-node.sh

CMD ./install-node.sh
