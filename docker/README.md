# Docker setup for cardano-node-tests (Antithesis)

This directory contains the driver image and compose files for submitting
`cardano-node-tests` to Antithesis.

## How it works

Antithesis environments have **no internet access** at runtime, so all
dependencies are baked into the image at build time:

- `Dockerfile` — builds the driver image.  At build time it:
  1. Pre-builds `cardano-node`, `cardano-submit-api`, `cardano-cli`, and
     `bech32` from `NODE_REV` into `/opt/cardano/` via `nix build`.
  2. Pre-warms the `testenv` dev shell and creates the Python venv at
     `/opt/tests-venv/` with all project dependencies installed.
  3. Pre-warms the `base` dev shell so the `regression.sh` shebang resolves
     from the local nix store without network access.
  4. Installs `antithesis_run.sh` as the Antithesis test driver at
     `/opt/antithesis/test/v1/quickstart/singleton_driver_regression.sh`.

- `antithesis_run.sh` — container entrypoint that:
  1. Forces nix into offline mode (`offline = true`).
  2. Exports `CARDANO_PREBUILT_DIR=/opt/cardano` and `_VENV_DIR=/opt/tests-venv`
     so `regression.sh` skips all downloads and uses the pre-built artefacts.
  3. Emits the Antithesis `setup_complete` lifecycle signal.
  4. Hands off to `.github/regression.sh`.

- `Dockerfile.config` — builds the Antithesis config image (`FROM scratch`)
  containing only `docker-compose.yaml`.

- `docker-compose.yaml` — single `driver` service.

## Workflow

### 1. Build and push the driver image

```bash
docker build -f docker/Dockerfile \
  --build-arg GIT_REVISION=$(git rev-parse HEAD) \
  --build-arg NODE_REV=master \
  -t ghcr.io/intersectmbo/cardano-node-tests-antithesis:latest .

docker push ghcr.io/intersectmbo/cardano-node-tests-antithesis:latest
```

`NODE_REV` is locked at build time — the same binaries are used every run
regardless of what is on the `master` branch when the container starts.

### 2. Build and push the config image

```bash
docker build -f docker/Dockerfile.config \
  -t us-central1-docker.pkg.dev/<tenant>/antithesis/config:latest .

docker push us-central1-docker.pkg.dev/<tenant>/antithesis/config:latest
```

### 3. Validate locally (internet-connected build, isolated network at runtime)

```bash
docker compose -f docker/docker-compose.yaml config
docker compose -f docker/docker-compose.yaml up --build
```

To fully simulate the Antithesis no-internet constraint, run inside an
isolated network namespace on Linux:

```bash
unshare -n docker compose -f docker/docker-compose.yaml up
```

## Environment variables

`NODE_REV` is baked into the image at build time and must **not** be set at
runtime.  All other variables are passed through docker-compose as before.

| Variable          | Default    | Description                              |
|-------------------|------------|------------------------------------------|
| `CARDANO_CLI_REV` | (built-in) | cardano-cli revision, empty = use node's |
| `DBSYNC_REV`      | (disabled) | db-sync revision, empty = disabled       |
| `RUN_TARGET`      | `tests`    | `tests`, `testpr`, or `testnets`         |
| `MARKEXPR`        |            | pytest marker expression                 |
| `CLUSTERS_COUNT`  |            | number of local cluster instances        |
| `CLUSTER_ERA`     |            | e.g. `conway`                            |
| `PROTOCOL_VERSION`|            | e.g. `11`                                |
| `UTXO_BACKEND`    |            | e.g. `disk`, `mem`                       |
