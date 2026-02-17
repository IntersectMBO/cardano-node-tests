# Docker Setup for cardano-node-tests

This directory contains Docker configuration files for a ready-to-use containerized testing environment for the cardano-node-tests suite.

## Overview

The Dockerfile is based on the `.github/regression.sh` workflow and provides a **ready-to-use** testing environment with:

- Nix package manager with flakes support
- Python 3.13 with all test dependencies pre-installed and **activated by default**
- Cardano node binaries built and **available in PATH**
- All required system utilities
- Automatic environment setup on container start

**You can run tests immediately** after starting the container - no manual activation needed!

## Quick Start

### Build the Docker image

```bash
cd docker/
docker build -t cardano-node-tests:latest -f Dockerfile ..
```

### Run tests immediately

```bash
# Start container - environment is automatically activated!
docker run -it --rm cardano-node-tests:latest

# Inside container, run tests directly:
pytest cardano_node_tests/tests/              # Run all tests
pytest cardano_node_tests/tests/ -m smoke     # Run smoke tests only
pytest cardano_node_tests/tests/test_staking.py  # Run specific test file

# Or use the test runner scripts:
./.github/run_tests.sh tests       # All tests
./.github/run_tests.sh testpr      # PR tests (smoke)
./.github/run_tests.sh testnets    # Testnet tests

# Or run full regression:
./.github/regression.sh
```

### That's it

No need to activate virtual environments or set up paths - everything is ready to use!

## Using Docker Compose

Docker Compose provides easier management of volumes and environment variables.

### Start container and run tests

```bash
cd docker/
docker-compose run --rm cardano-node-tests

# Inside container, environment is ready - run tests directly
pytest cardano_node_tests/tests/ -m smoke
```

### Configure environment variables

```bash
# Pass environment variables when starting
docker-compose run --rm \
  -e MARKEXPR="smoke" \
  -e CLUSTERS_COUNT=5 \
  cardano-node-tests

# Inside container, run tests (variables are already set)
./.github/run_tests.sh testpr
```

## Environment Variables

Key environment variables for test configuration:

### Test Execution

- `RUN_TARGET` - Test target: `tests`, `testpr`, or `testnets` (default: `tests`)
- `MARKEXPR` - Pytest marker expression to filter tests (e.g., `smoke`, `dbsync`)
- `TEST_THREADS` - Number of pytest workers (default varies by target)
- `CLUSTERS_COUNT` - Number of local testnet clusters to launch
- `SESSION_TIMEOUT` - Timeout for test session (e.g., `3h`, `45m`)

### Cluster Configuration

- `CLUSTER_ERA` - Cluster era: `conway`, `babbage`, etc.
- `PROTOCOL_VERSION` - Protocol version (e.g., `11` for Conway 11)
- `UTXO_BACKEND` - UTxO backend type (e.g., `disk`, `mem`)
- `TESTNET_VARIANT` - Testnet variant (e.g., `conway_slow`)
- `BOOTSTRAP_DIR` - Path to bootstrap directory for testnet runs

### Dependencies

- `NODE_REV` - Cardano node git revision/tag (default: `master`)
- `CARDANO_CLI_REV` - Cardano CLI git revision (optional, uses built-in if unset)
- `DBSYNC_REV` - DB-sync revision (default: disabled, set to enable)

### Other

- `KEEP_CLUSTERS_RUNNING` - Set to `1` to keep clusters running after tests
- `NO_ARTIFACTS` - Set to skip saving test artifacts
- `PYTEST_ARGS` - Additional arguments passed to pytest

## Examples

### Run smoke tests quickly

```bash
docker run -it --rm cardano-node-tests:latest

# Inside container - just run pytest!
pytest cardano_node_tests/tests/ -m smoke
```

### Run specific test with environment variables

```bash
docker run -it --rm \
  -e MARKEXPR="smoke" \
  -e CLUSTERS_COUNT=3 \
  cardano-node-tests:latest

# Inside container
pytest cardano_node_tests/tests/ -m smoke -n 3
```

### Run Conway era tests

```bash
docker run -it --rm \
  -e CLUSTER_ERA="conway" \
  -e PROTOCOL_VERSION=11 \
  cardano-node-tests:latest

# Inside container
pytest cardano_node_tests/tests/ -m conway
```

### Debug with persistent volumes

```bash
# Start container with persistent volumes
docker run -it --rm \
  -v test-artifacts:/work/.artifacts \
  -v test-workdir:/work/run_workdir \
  cardano-node-tests:latest

# Inside container - run tests
pytest cardano_node_tests/tests/ -v
```

### Build with specific base image version

```bash
docker build \
  --build-arg NIX_VERSION=2.25.5 \
  -t cardano-node-tests:custom \
  -f Dockerfile ..
```

## File Structure

```text
docker/
├── Dockerfile           # Main Dockerfile based on regression.sh
├── docker-compose.yml   # Docker Compose configuration
├── .dockerignore       # Files to exclude from build context
└── README.md           # This file
```

## Troubleshooting

### Build fails during Nix setup

If Nix fails to download dependencies:

- Check network connectivity
- Verify IOG cache is accessible: `curl -I https://cache.iog.io`
- Try building without cache: add `--option substituters ""` to nix commands

### Tests fail to start clusters

- Ensure sufficient resources (CPU, memory, disk)
- Check logs in `/work/run_workdir/state-cluster*/`
- Try reducing `CLUSTERS_COUNT` or `TEST_THREADS`

### Permission issues with volumes

If you encounter permission errors with mounted volumes:

```bash
# Run as current user
docker run --rm --user $(id -u):$(id -g) -v $PWD:/work cardano-node-tests:latest
```

## Advanced Usage

### Custom test selection

```bash
# Start container
docker run -it --rm cardano-node-tests:latest

# Inside container - run specific tests directly
pytest cardano_node_tests/tests/test_staking.py::test_stake_pool_low_cost
pytest cardano_node_tests/tests/tests_conway/ -k "governance"
pytest -v --collect-only  # List all available tests
```

### Mount local cardano binaries

```bash
# Use locally built cardano-cli/cardano-node
docker run -it --rm \
  -v /path/to/cardano-cli:/work/.bin/cardano-cli:ro \
  -v /path/to/cardano-node:/work/.bin/cardano-node:ro \
  cardano-node-tests:latest

# Inside container - binaries are automatically in PATH
cardano-cli --version
pytest cardano_node_tests/tests/
```

### Extract test artifacts

```bash
# Start container with name
docker run -it --name test-run cardano-node-tests:latest

# Inside container - run tests
pytest cardano_node_tests/tests/
# Exit container (Ctrl+D)

# Copy artifacts from container
docker cp test-run:/work/.artifacts ./artifacts
docker cp test-run:/work/.reports ./reports

# Remove container
docker rm test-run
```

## Notes

- The Docker image provides a **fully ready environment** - Python venv is activated and cardano binaries are in PATH automatically
- Everything is configured on container start via the entrypoint script
- First build may take significant time due to Nix dependency resolution and building cardano binaries
- Python dependencies are pre-installed using `uv` for faster resolution
- The image size will be large due to Nix store and Haskell dependencies
- Perfect for interactive testing, MOOG integration, or CI/CD pipelines
- You can run tests from the test directory immediately after container starts

## Contributing

When modifying the Dockerfile:

1. Ensure it stays in sync with `.github/regression.sh`
2. Test both interactive and automated test runs
3. Verify all environment variables work as expected
4. Update this README with any new features or changes
