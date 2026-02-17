# Docker setup for cardano-node-tests (Antithesis/Moog)

This directory contains the driver image and compose file for submitting
`cardano-node-tests` to Antithesis via the Moog platform.

## How it works

- `Dockerfile` — minimal image: configures Nix and copies the repo into
  `/work/`. No binaries are pre-built; `regression.sh` handles all setup at
  runtime via its own Nix shebang. Requires `GIT_REVISION` build arg (the
  current commit hash) so pytest can identify the revision without a `.git`
  directory inside the image.
- `docker-compose.yaml` — single `driver` service for Moog submission.

## Workflow

### 1. Build and push the image

```bash
docker build -f docker/Dockerfile \
  --build-arg GIT_REVISION=$(git rev-parse HEAD) \
  -t ghcr.io/intersectmbo/cardano-node-tests-antithesis:latest .

docker push ghcr.io/intersectmbo/cardano-node-tests-antithesis:latest
```

### 2. Validate the compose locally

```bash
docker compose -f docker/docker-compose.yaml config
docker compose -f docker/docker-compose.yaml up --build
```

### 3. Submit to Moog

```bash
moog requester create-test \
  --platform github \
  --username saratomaz \
  --repository IntersectMBO/cardano-node-tests \
  --directory ./docker \
  --commit $(git rev-parse HEAD) \
  --try 1 \
  --duration 2
```

## Environment variables

| Variable          | Default    | Description                              |
|-------------------|------------|------------------------------------------|
| `NODE_REV`        | `master`   | cardano-node git revision                |
| `CARDANO_CLI_REV` | (built-in) | cardano-cli revision, empty = use node's |
| `DBSYNC_REV`      | (disabled) | db-sync revision, empty = disabled       |
| `RUN_TARGET`      | `tests`    | `tests`, `testpr`, or `testnets`         |
| `MARKEXPR`        |            | pytest marker expression                 |
| `CLUSTERS_COUNT`  |            | number of local cluster instances        |
| `CLUSTER_ERA`     |            | e.g. `conway`                            |
| `PROTOCOL_VERSION`|            | e.g. `11`                                |
| `UTXO_BACKEND`    |            | e.g. `disk`, `mem`                       |
