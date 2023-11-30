FROM fedora:latest

# workaround for timezone manual intervention
ENV TZ=Europe/London
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

ENV TAGGED_VERSION ''
COPY install-node.sh /
# pkg-config 1.9.5 breaks cabal dependency checking
# pkg-config 2 isn't available yet for fedora so we need to build it from source
RUN yum update -y && \
    yum install git diffutils gcc gcc-c++ tmux gmp-devel make tar xz wget zlib-devel libtool autoconf -y && \
    mkdir -p /root/src/ && cd /root/src && \
    curl -O https://distfiles.ariadne.space/pkgconf/pkgconf-2.0.3.tar.xz && \
    tar -xvf pkgconf-2.0.3.tar.xz && \
    cd pkgconf-2.0.3 && \
    ./configure && make && make install && \
    chmod +x /install-node.sh

CMD ./install-node.sh $TAGGED_VERSION
