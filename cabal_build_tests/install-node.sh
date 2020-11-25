#!/bin/bash

### Builds cardano-node on Ubuntu or Fedora using Cabal and verifys successful installation
### Please note: sudo is not used because user is root
### Please note: 'source ~/.bashrc' cmd is not used because Docker runs this script as subscript

echo ""

if [[$TAGGED_VERSION == "null" ]]
then
	printf "Please specify TAGGED_VERSION on docker run.\ne.g. '-e TAGGED_VERSION=1.23.0'"
fi

echo "Using tagged version $TAGGED_VERSION"
cd ~

# Install dependencies
echo "Install dependencies"
if [[ $(cat /etc/os-release) == *"fedora"* ]]
then
	echo "Running on Fedora"
	yum update -y
	yum install git gcc gcc-c++ tmux gmp-devel make tar xz wget zlib-devel libtool autoconf -y
	yum install systemd-devel ncurses-devel ncurses-compat-libs -y
elif [[ $(cat /etc/os-release) == *"ubuntu"* ]]
then
	echo "Running on Ubuntu"
	apt-get update -y
	apt-get install automake build-essential pkg-config libffi-dev libgmp-dev libssl-dev libtinfo-dev libsystemd-dev zlib1g-dev make g++ tmux git jq wget libncursesw5 libtool autoconf -y
else
	echo "/etc/os-relase does not contain 'fedora' or 'ubuntu'"
	echo "$(cat /etc/os-release)"
	exit 1
fi

# Download, unpack, install and update Cabal
echo "Download, unpack, install and update Cabal"
wget https://downloads.haskell.org/~cabal/cabal-install-3.2.0.0/cabal-install-3.2.0.0-x86_64-unknown-linux.tar.xz
tar -xf cabal-install-3.2.0.0-x86_64-unknown-linux.tar.xz
rm cabal-install-3.2.0.0-x86_64-unknown-linux.tar.xz cabal.sig
mkdir -p ~/.local/bin
mv cabal ~/.local/bin/

if [[ $PATH != *"~/.local/bin"* ]]
then
	#echo "\$PATH does not contain ~/.local/bin - Adding..."
	#echo "export PATH=\"~/.local/bin:\$PATH\"" >> ~/.bashrc
	#echo "END OF BASHRC IS $(tail -n1 ~/.bashrc)" #REMOVE
	#bash -c 'source ~/.bashrc' # CANT USE source IN THIS SCRIPT BECAUSE DOCKER RUNS THIS AS SUBSCRIPT
	export PATH="~/.local/bin:$PATH"
else
	echo "$PATH already contains ~/.local/bin"
fi


cabal update

if [[ $(cabal --version) != *"cabal-install version 3.2.0.0"* ]]
then
        echo "cabal version $(cabal --version) is not 3.2.0.0"
	exit 1
else
	echo "cabal version 3.2.0.0 is correct"
fi

# Download and install GHC
echo "Download and install GHC"
wget https://downloads.haskell.org/ghc/8.10.2/ghc-8.10.2-x86_64-deb9-linux.tar.xz
tar -xf ghc-8.10.2-x86_64-deb9-linux.tar.xz
rm ghc-8.10.2-x86_64-deb9-linux.tar.xz
cd ghc-8.10.2
./configure
make install

cd ~

# Install Libsodium
echo "Install Lobsodium"
git clone https://github.com/input-output-hk/libsodium
cd libsodium
git checkout 66f017f1
./autogen.sh
./configure
make
make install

export LD_LIBRARY_PATH="/usr/local/lib:$LD_LIBRARY_PATH"
export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH"

# Download the source code for cardano-node
echo "Download the source code for cardano-node"
cd ~
git clone https://github.com/input-output-hk/cardano-node.git
cd cardano-node
git fetch --all --tags

if [[ $(git tag) != *"$TAGGED_VERSION"* ]]
then
	echo "$(git tag) does not contain $TAGGED_VERSION"
	exit 1
fi

git checkout tags/$TAGGED_VERSION

# Build and install the node
echo "Build and install the node"
cabal build all
cp -p dist-newstyle/build/x86_64-linux/ghc-8.10.2/cardano-node-$TAGGED_VERSION/x/cardano-node/build/cardano-node/cardano-node ~/.local/bin/
cp -p dist-newstyle/build/x86_64-linux/ghc-8.10.2/cardano-cli-$TAGGED_VERSION/x/cardano-cli/build/cardano-cli/cardano-cli ~/.local/bin/

# Verify node is installed
echo "Verify node is installed"
if [[ $(cardano-cli --version) == *"$TAGGED_VERSION"* ]]
then
	echo "$(cardano-cli --version)"
else
	echo "$(cardano-cli --version) does not contain $TAGGED_VERSION"
	exit 1
fi

printf "\nSuccess\n"
