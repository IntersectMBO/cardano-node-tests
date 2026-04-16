#! /usr/bin/env -S nix develop --accept-flake-config .#postgres -i -k PGHOST -k PGPORT -k PGUSER -c bash
# shellcheck shell=bash

set -Eeuo pipefail
trap 'echo "Error at line $LINENO"' ERR

usage() {
  echo "Usage: $(basename "$0") [-k|--kill] [-h|--help] POSTGRES_DIR"
  echo ""
  echo "Start a PostgreSQL instance in the given directory."
  echo ""
  echo "Arguments:"
  echo "  POSTGRES_DIR  Path to the postgres data directory"
  echo ""
  echo "Options:"
  echo "  -k, --kill  Kill running postgres on PGPORT and clear data before starting"
  echo "  -h, --help  Show this help message"
  exit "${1:-0}"
}

is_postgres_pid() {
  local pid="$1"
  local pg_dir="$2"
  local cmdline
  cmdline="$(ps -o args= -p "$pid" 2>/dev/null || echo "")"
  # verify process is postgres serving expected data directory
  # use grep -F for literal string match (glob metacharacters in pg_dir are safe)
  [[ "$cmdline" == *postgres* ]] && printf '%s' "$cmdline" | grep -qF "$pg_dir/data"
}

kill_postgres() {
  local pg_dir="$1"
  local pg_port="$2"

  # prefer pg_ctl for clean shutdown when data dir exists
  if [ -d "$pg_dir/data" ] && command -v pg_ctl &>/dev/null; then
    echo "Stopping postgres via pg_ctl -D $pg_dir/data"
    pg_ctl stop -D "$pg_dir/data" -m fast 2>/dev/null || true
  fi

  local pids_to_kill=""

  # collect PID from pidfile if present, verifying it's actually our postgres
  if [ -f "$pg_dir/postgres.pid" ]; then
    local file_pid
    file_pid="$(head -n 1 "$pg_dir/postgres.pid" 2>/dev/null || echo "")"
    if [ -n "$file_pid" ] && kill -0 "$file_pid" 2>/dev/null; then
      if is_postgres_pid "$file_pid" "$pg_dir"; then
        pids_to_kill="$file_pid"
      else
        echo "Warning: PID $file_pid from pidfile is not postgres for $pg_dir/data, skipping"
      fi
    fi
  fi

  # collect postgres PIDs listening on target port, verifying each is actually postgres
  local port_pids
  port_pids="$(lsof -iTCP:"$pg_port" -sTCP:LISTEN -t 2>/dev/null || echo "")"
  local pid
  for pid in $port_pids; do
    local port_cmdline
    port_cmdline="$(ps -o args= -p "$pid" 2>/dev/null || echo "")"
    if [[ "$port_cmdline" != *postgres* ]]; then
      echo "Error: non-postgres process (PID $pid) listening on port $pg_port: $port_cmdline" >&2
      exit 1
    fi
    # avoid duplicates
    case " $pids_to_kill " in
      *" $pid "*) ;;
      *) pids_to_kill="$pids_to_kill $pid" ;;
    esac
  done

  pids_to_kill="$(echo "$pids_to_kill" | xargs)"

  if [ -n "$pids_to_kill" ]; then
    echo "Sending SIGTERM to PIDs on port $pg_port: $pids_to_kill"
    # shellcheck disable=SC2086
    kill $pids_to_kill 2>/dev/null || true

    # wait up to 10s for processes to exit
    local all_dead
    for _ in $(seq 1 20); do
      all_dead=1
      for pid in $pids_to_kill; do
        if kill -0 "$pid" 2>/dev/null; then
          all_dead=0
          break
        fi
      done
      if [ "$all_dead" -eq 1 ]; then
        break
      fi
      sleep 0.5
    done

    # force kill any survivors
    for pid in $pids_to_kill; do
      if kill -0 "$pid" 2>/dev/null; then
        echo "Process $pid still alive, sending SIGKILL"
        kill -9 "$pid" 2>/dev/null || true
      fi
    done

    # verify all processes actually died before touching data
    sleep 0.5
    for pid in $pids_to_kill; do
      if kill -0 "$pid" 2>/dev/null; then
        echo "Error: process $pid still alive after SIGKILL, refusing to remove data" >&2
        exit 1
      fi
    done
  else
    echo "No running postgres found on port $pg_port"
  fi

  rm -rf "$pg_dir/data"
  rm -f "$pg_dir"/.*.lock
}

KILL_EXISTING=""
POSTGRES_DIR=""

for arg in "$@"; do
  case "$arg" in
    -k|--kill) KILL_EXISTING=1 ;;
    -h|--help) usage 0 ;;
    -*)
      echo "Error: Unknown option '$arg'" >&2
      usage 2
      ;;
    *)
      if [ -n "$POSTGRES_DIR" ]; then
        echo "Error: Unexpected argument '$arg'" >&2
        usage 2
      fi
      POSTGRES_DIR="$arg"
      ;;
  esac
done

if [ -z "$POSTGRES_DIR" ]; then
  echo "Error: POSTGRES_DIR is required" >&2
  usage 2
fi

POSTGRES_DIR="$(readlink -m "$POSTGRES_DIR")"

# validate POSTGRES_DIR basename matches expected postgres directory naming
pg_basename="$(basename "$POSTGRES_DIR")"
if [[ ! "$pg_basename" =~ postgres ]]; then
  echo "Error: POSTGRES_DIR basename '$pg_basename' must match '*postgres*'" >&2
  exit 1
fi

parent_dir="$(dirname "$POSTGRES_DIR")"
if [ ! -d "$parent_dir" ]; then
  echo "Error: parent directory '$parent_dir' does not exist" >&2
  exit 1
fi
if [ ! -w "$parent_dir" ] && [ ! -d "$POSTGRES_DIR" ]; then
  echo "Error: parent directory '$parent_dir' is not writable" >&2
  exit 1
fi
if [ -d "$POSTGRES_DIR" ] && [ ! -w "$POSTGRES_DIR" ]; then
  echo "Error: POSTGRES_DIR '$POSTGRES_DIR' is not writable" >&2
  exit 1
fi

# set postgres env variables
export PGHOST="${PGHOST:-localhost}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"

# kill running postgres and clear its data
if [ -n "$KILL_EXISTING" ]; then
  kill_postgres "$POSTGRES_DIR" "$PGPORT"
fi

# setup db
if [ ! -e "$POSTGRES_DIR/data" ]; then
  mkdir -p "$POSTGRES_DIR/data"
  initdb -D "$POSTGRES_DIR/data" --encoding=UTF8 --locale=en_US.UTF-8 -A trust -U "$PGUSER"
fi

# start postgres
postgres -D "$POSTGRES_DIR/data" -k "$POSTGRES_DIR" > "$POSTGRES_DIR/postgres.log" 2>&1 &
PSQL_PID="$!"
echo "$PSQL_PID" > "$POSTGRES_DIR/postgres.pid"

sleep 5
cat "$POSTGRES_DIR/postgres.log"
echo
ps -fp "$PSQL_PID"
