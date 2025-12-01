#!/usr/bin/env bash

# Builds 'cardano-node' and 'cardano-cli' on Ubuntu or Fedora using Cabal and verifies
# successful installation.
#
# Based on instructions from
# <https://developers.cardano.org/docs/get-started/infrastructure/node/installing-cardano-node/#building-via-cabal>
#
# Please note: sudo is not used because user is root
# Please note: 'source ~/.bashrc' cmd is not used because Docker runs this script as subscript

# Versions
GHC_VERSION="9.6.7"
CABAL_VERSION="3.12.1.0"

echo ""

if [[ -z "${GIT_OBJECT}" ]]; then
  >&2 printf "Please specify 'GIT_OBJECT' on docker run.\ne.g. '-e GIT_OBJECT=10.6.1' for tags, or '-e GIT_OBJECT=78wagy3aw87ef' for commits."
  exit 1
fi

on_exit() {
  if [[ "${KEEP_RUNNING:-0}" = "1" ]]; then
      echo "Container will stay alive for debugging..."
      echo "You can use 'docker exec -it <container-id> /bin/bash' in a separate session to play in the environment."
      echo "Press Ctrl+C here to stop the container."
      tail -f /dev/null
  fi
}
trap on_exit EXIT

echo "Using git object '$GIT_OBJECT'"

cd ~ || exit 1


# Set up ~/.local/bin
mkdir -p ~/.local/bin || exit 1

if [[ "$PATH" != *"~/.local/bin"* ]]; then
  export PATH=~/.local/bin:"$PATH"
else
  echo "'PATH' already contains ~/.local/bin"
fi

# Install dependencies
echo "Install dependencies"
if [[ "$(</etc/os-release)" == *"fedora"* ]]; then
  echo "Running on Fedora"
  yum update -y
  yum install git gcc gcc-c++ tmux gmp-devel make tar xz wget zlib-devel libtool autoconf -y
  yum install systemd-devel ncurses-devel ncurses-compat-libs which jq openssl-devel lmdb-devel -y
elif [[ "$(</etc/os-release)" == *"ubuntu"* ]]; then
  echo "Running on Ubuntu"
  apt-get update -y
  apt-get install automake build-essential pkg-config libffi-dev libgmp-dev libssl-dev libncurses-dev libsystemd-dev zlib1g-dev make g++ tmux git jq wget libtool autoconf liblmdb-dev -y
else
  >&2 echo "/etc/os-relase does not contain 'fedora' or 'ubuntu'"
  >&2 cat /etc/os-release
  exit 1
fi

# Version of iohk-nix
IOHKNIX_VERSION="$(curl "https://raw.githubusercontent.com/IntersectMBO/cardano-node/$GIT_OBJECT/flake.lock" | jq -r '.nodes.iohkNix.locked.rev')"
echo "iohk-nix version: $IOHKNIX_VERSION"

# Install GHCup - the main installer for Haskell
echo "Install GHCup"
curl --proto '=https' --tlsv1.2 -sSf https://get-ghcup.haskell.org | BOOTSTRAP_HASKELL_NONINTERACTIVE=1 BOOTSTRAP_HASKELL_MINIMAL=1 BOOTSTRAP_HASKELL_ADJUST_BASHRC=P sh

# Source changes in order to use GHCup in current terminal session
# shellcheck source=/dev/null
source /root/.ghcup/env

# Download, unpack, install and update Cabal
echo "Download, unpack, install Cabal and GHC"
ghcup install ghc $GHC_VERSION --set
ghcup install cabal $CABAL_VERSION --set

if [[ "$(cabal --version)" != *"cabal-install version $CABAL_VERSION"* ]]; then
  >&2 echo "cabal version '$(cabal --version)' is not '$CABAL_VERSION'"
  exit 1
else
  echo "cabal version '$CABAL_VERSION' is correct"
fi

if [[ $(ghc --version) != *"version $GHC_VERSION"* ]]; then
  >&2 echo "ghc version '$(ghc --version)' is not '$GHC_VERSION'"
  exit 1
else
  echo "ghc version '$GHC_VERSION' is correct"
fi

# Install Libsodium
echo "Install Libsodium"

mkdir -p ~/src || exit 1
cd ~/src || exit 1

LIBSODIUM_VERSION="$(curl "https://raw.githubusercontent.com/input-output-hk/iohk-nix/$IOHKNIX_VERSION/flake.lock" | jq -r '.nodes.sodium.original.rev')"
echo "Using sodium version: $LIBSODIUM_VERSION"

git clone https://github.com/IntersectMBO/libsodium
cd libsodium || exit 1
git checkout "$LIBSODIUM_VERSION"
./autogen.sh
./configure
make
make check
make install

export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH}"
export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:${PKG_CONFIG_PATH}"
# shellcheck source=/dev/null
source ~/.bashrc

# Install Secp256k1
echo "Install Secp256k1"

mkdir -p ~/src || exit 1
cd ~/src || exit 1

SECP256K1_VERSION="$(curl "https://raw.githubusercontent.com/input-output-hk/iohk-nix/$IOHKNIX_VERSION/flake.lock" | jq -r '.nodes.secp256k1.original.ref')"
echo "Using secp256k1 version: ${SECP256K1_VERSION}"

git clone --depth 1 --branch "${SECP256K1_VERSION}" https://github.com/bitcoin-core/secp256k1
cd secp256k1 || exit 1
./autogen.sh
./configure --enable-module-schnorrsig --enable-experimental
make
make check
make install

# Install BLST
echo "Install BLST"

mkdir -p ~/src || exit 1
cd ~/src || exit 1

BLST_VERSION="$(curl "https://raw.githubusercontent.com/input-output-hk/iohk-nix/$IOHKNIX_VERSION/flake.lock" | jq -r '.nodes.blst.original.ref')"
echo "Using blst version: ${BLST_VERSION}"

git clone --depth 1 --branch "${BLST_VERSION}" https://github.com/supranational/blst
cd blst || exit 1
./build.sh
cat > libblst.pc << EOF
prefix=/usr/local
exec_prefix=\${prefix}
libdir=\${exec_prefix}/lib
includedir=\${prefix}/include

Name: libblst
Description: Multilingual BLS12-381 signature library
URL: https://github.com/supranational/blst
Version: ${BLST_VERSION#v}
Cflags: -I\${includedir}
Libs: -L\${libdir} -lblst
EOF
cp libblst.pc /usr/local/lib/pkgconfig/
cp bindings/blst_aux.h bindings/blst.h bindings/blst.hpp  /usr/local/include/
cp libblst.a /usr/local/lib
chmod u=rw,go=r /usr/local/{lib/{libblst.a,pkgconfig/libblst.pc},include/{blst.{h,hpp},blst_aux.h}}

# Download the source code for cardano-node
echo "Download the source code for cardano-node"

mkdir -p ~/src || exit 1
cd ~/src || exit 1
git clone https://github.com/IntersectMBO/cardano-node.git
cd cardano-node || exit 1
git fetch --all --recurse-submodules --tags

# Checkout with prechecks for git object and git object type
OBJ_TYPE="$(git cat-file -t "$GIT_OBJECT")"
if [[ "$OBJ_TYPE" != "commit" && "$OBJ_TYPE" != "tag" ]]; then
  >&2 echo "'$OBJ_TYPE' does not refer to a commit/tag."
  exit 1
fi

git checkout "$GIT_OBJECT"
GIT_REV=$(git rev-parse HEAD)

# Configure build options
echo "Configure build options"
echo "with-compiler: ghc-${GHC_VERSION}" >> cabal.project.local

# Build and install the node and cli
echo "Build and install the node and cli"
cabal update
cabal build exe:cardano-node
cabal build cardano-cli

cp -p "$(cabal list-bin cardano-node)" ~/.local/bin/
cp -p "$(cabal list-bin cardano-cli)" ~/.local/bin/

# Verify installation
echo "Verify 'cardano-cli' is installed"
if ! [[ "$(cardano-cli --version | tail -n 1)" == *"$GIT_REV"* ]]; then
  >&2 echo "'$(cardano-cli --version)' failed"
  exit 1
fi

echo "Verify 'cardano-node' is installed"
if ! [[ "$(cardano-node --version | tail -n 1)" == *"$GIT_REV"* ]]; then
  >&2 echo "'$(cardano-node --version)' failed"
  exit 1
fi

# Success message
printf "\nSuccess\n\n"
