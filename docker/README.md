# Docker setup for cardano-node-tests (Antithesis/Moog)

This directory contains the driver image and compose file for submitting
`cardano-node-tests` to Antithesis via the Moog platform.

## How it works

- `Dockerfile` — minimal image: configures Nix and copies the repo into
  `/work/`. No binaries are pre-built; `regression.sh` handles all setup at
  runtime via its own Nix shebang.
- `docker-compose.yml` — single `driver` service for Moog submission.
- The image places a `singleton_driver_regression.sh` wrapper at
  `/opt/antithesis/test/v1/regression/`, which is where Antithesis looks for
  test commands.

## Workflow

### 1. Build and push the image

```bash
docker build -f docker/Dockerfile \
  -t ghcr.io/intersectmbo/cardano-node-tests:antithesis .

docker push ghcr.io/intersectmbo/cardano-node-tests:antithesis
```

### 2. Validate the compose locally

```bash
docker compose -f docker/docker-compose.yml config
docker compose -f docker/docker-compose.yml up --build
```

### 3. Submit to Moog

```bash
moog requester create-test \
  --platform github \
  --username <your-github-username> \
  --repository IntersectMBO/cardano-node-tests \
  --directory ./docker \
  --commit <commit-hash> \
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
