#!/usr/bin/env bash

# Builds 'cardano-node' and 'cardano-cli' on Ubuntu or Fedora using Cabal and verifies
# successful installation.
#
# Please note: sudo is not used because user is root
# Please note: 'source ~/.bashrc' cmd is not used because Docker runs this script as subscript

# Versions
GHC_VERSION="8.10.7"
CABAL_VERSION="3.6.2.0"
LIBSODIUM_VERSION="dbb48cc"
SECP256K1_VERSION="ac83be33"

echo ""

# Check that environment variables are correctly set up.
case "$GIT_OBJECT_TYPE" in
  tag | commit)
    :
    ;;
  *)
    >&2 printf "Please specify 'GIT_OBJECT_TYPE' on docker run.\ne.g. '-e GIT_OBJECT_TYPE=tag' or '-e GIT_OBJECT_TYPE=commit'"
    exit 1
    ;;
esac

if [[ -z "${GIT_OBJECT}" ]]; then
  >&2 printf "Please specify 'GIT_OBJECT' on docker run.\ne.g. '-e GIT_OBJECT=8.0.0' for tags, or '-e GIT_OBJECT=78wagy3aw87ef' for commits."
  exit 1
fi

echo "Using git object '$GIT_OBJECT' of type '$GIT_OBJECT_TYPE'"

cd ~ || exit 1

# Set up ~/.local/bin
mkdir -p ~/.local/bin || exit 1

if [[ "$PATH" != *"~/.local/bin"* ]]; then
  export PATH=~/.local/bin:"$PATH"
else
  echo "'PATH' already contains ~/.local/bin"
fi

# Set up GHC and cabal-install versions
GHC="ghc-${GHC_VERSION}"
GHC_SUFFIX="x86_64-deb10-linux.tar.xz"
GHC_FULL="${GHC}-${GHC_SUFFIX}"

echo "Using GHC version $GHC_VERSION"

CABAL="cabal-install-${CABAL_VERSION}"
CABAL_SUFFIX="x86_64-linux-deb10.tar.xz"
CABAL_FULL="${CABAL}-${CABAL_SUFFIX}"

echo "Using cabal-install version '$CABAL_VERSION'"

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
  apt-get install automake build-essential pkg-config libffi-dev libgmp-dev libssl-dev libtinfo-dev libsystemd-dev zlib1g-dev make g++ tmux git jq wget libncursesw5 libtool autoconf liblmdb-dev -y
else
  >&2 echo "/etc/os-relase does not contain 'fedora' or 'ubuntu'"
  >&2 cat /etc/os-release
  exit 1
fi

# Download, unpack, install and update Cabal
echo "Download, unpack, install and update Cabal"
wget "https://downloads.haskell.org/~cabal/${CABAL}/${CABAL_FULL}"
tar -xf "$CABAL_FULL"
rm "$CABAL_FULL"
mv cabal ~/.local/bin/

cabal update

if [[ "$(cabal --version)" != *"cabal-install version $CABAL_VERSION"* ]]; then
  >&2 echo "cabal version '$(cabal --version)' is not '$CABAL_VERSION'"
  exit 1
else
  echo "cabal version '$CABAL_VERSION' is correct"
fi

# Download, unpack and install GHC
echo "Download and install GHC"

mkdir -p ~/src || exit 1
cd ~/src || exit 1
wget "https://downloads.haskell.org/ghc/${GHC_VERSION}/${GHC_FULL}"
tar -xf "$GHC_FULL"
rm "$GHC_FULL"
cd "$GHC" || exit 1
./configure
make install

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
git clone https://github.com/input-output-hk/libsodium
cd libsodium || exit 1
git checkout "$LIBSODIUM_VERSION"
./autogen.sh
./configure
make
make install

export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH}"
export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:${PKG_CONFIG_PATH}"

# Install Secp256k1
echo "Install Secp256k1"

mkdir -p ~/src || exit 1
cd ~/src || exit 1
git clone https://github.com/bitcoin-core/secp256k1
cd secp256k1 || exit 1
git checkout "$SECP256K1_VERSION"
./autogen.sh
./configure --enable-module-schnorrsig --enable-experimental
make
make install

# Download the source code for cardano-node
echo "Download the source code for cardano-node"

mkdir -p ~/src || exit 1
cd ~/src || exit 1
git clone https://github.com/input-output-hk/cardano-node.git
cd cardano-node || exit 1
git fetch --all --recurse-submodules --tags

# Checkout with prechecks for git object and git object type
if [[ "$(git cat-file -t "$GIT_OBJECT")" != "commit" ]]; then
  >&2 echo "'$(git cat-file -t "$GIT_OBJECT")' does not refer to a commit/tag."
  exit 1
fi

case "$GIT_OBJECT_TYPE" in
  tag)
    if [[ "$(git tag)" != *"$GIT_OBJECT"* ]]; then
      >&2 echo "'$(git tag)' does not contain '$GIT_OBJECT'"
      exit 1
    fi
    ;;
  commit)
    :
    ;;
  *)
    exit 1
    ;;
esac

git checkout "$GIT_OBJECT"

# Configure build options
echo "Configure build options"
echo "with-compiler: ghc-${GHC_VERSION}" >> cabal.project.local

# Build and install the node
echo "Build and install the node"
cabal update
cabal build all

cp -p "$(./scripts/bin-path.sh cardano-node)" ~/.local/bin/
cp -p "$(./scripts/bin-path.sh cardano-cli)" ~/.local/bin/

# Verify installation
case "$GIT_OBJECT_TYPE" in
  tag)
    GIT_TAG="$GIT_OBJECT"

    echo "Verify 'cardano-cli' is installed"
    if [[ "$(cardano-cli --version)" == *"$GIT_TAG"* ]]; then
      cardano-cli --version
    else
      >&2 echo "'$(cardano-cli --version)' does not contain '$GIT_TAG'"
      exit 1
    fi

    echo "Verify 'cardano-node' is installed"
    if [[ "$(cardano-node --version)" == *"$GIT_TAG"* ]]; then
      cardano-node --version
    else
      >&2 echo "'$(cardano-node --version)' does not contain '$GIT_TAG'"
      exit 1
    fi
    ;;
  commit)
    echo "Verify 'cardano-cli' is installed"
    if ! cardano-cli --version; then
      >&2 echo "'cardano-cli --version' failed"
      exit 1
    fi

    # Verify node is installed
    echo "Verify 'cardano-node' is installed"
    if ! cardano-node --version; then
      >&2 echo "'cardano-node --version' failed"
      exit 1
    fi
    ;;
  *)
    exit 1
    ;;
esac

# Succes message
printf "\nSuccess\n\n"
echo "You can use 'docker exec -it <container-id> /bin/bash' in a separate session to play in the environment."
echo "Press any key to stop the container."
read -r
