#!/usr/bin/env bash

set -Eeuo pipefail
trap 'echo "Error at line $LINENO"' ERR

usage() {
  echo "Usage: $(basename "$0") [-k|--kill] [-h|--help] postgres_dir"
  echo ""
  echo "Start a PostgreSQL instance in the given directory."
  echo ""
  echo "Arguments:"
  echo "  postgres_dir  Path to the postgres data directory"
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

kill_existing=""
postgres_dir=""

for arg in "$@"; do
  case "$arg" in
    -k|--kill) kill_existing=1 ;;
    -h|--help) usage 0 ;;
    -*)
      echo "Error: Unknown option '$arg'" >&2
      usage 2
      ;;
    *)
      if [ -n "$postgres_dir" ]; then
        echo "Error: Unexpected argument '$arg'" >&2
        usage 2
      fi
      postgres_dir="$arg"
      ;;
  esac
done

if [ -z "$postgres_dir" ]; then
  echo "Error: postgres_dir is required" >&2
  usage 2
fi

postgres_dir="$(readlink -m "$postgres_dir")"

# validate postgres_dir basename matches expected postgres directory naming
pg_basename="$(basename "$postgres_dir")"
if [[ ! "$pg_basename" =~ postgres ]]; then
  echo "Error: postgres_dir basename '$pg_basename' must match '*postgres*'" >&2
  exit 1
fi

parent_dir="$(dirname "$postgres_dir")"
if [ ! -d "$parent_dir" ]; then
  echo "Error: parent directory '$parent_dir' does not exist" >&2
  exit 1
fi
if [ ! -w "$parent_dir" ] && [ ! -d "$postgres_dir" ]; then
  echo "Error: parent directory '$parent_dir' is not writable" >&2
  exit 1
fi
if [ -d "$postgres_dir" ] && [ ! -w "$postgres_dir" ]; then
  echo "Error: postgres_dir '$postgres_dir' is not writable" >&2
  exit 1
fi

# set postgres env variables
export PGHOST="${PGHOST:-localhost}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"

# kill running postgres and clear its data
if [ -n "$kill_existing" ]; then
  kill_postgres "$postgres_dir" "$PGPORT"
fi

# setup db
if [ ! -e "$postgres_dir/data" ]; then
  mkdir -p "$postgres_dir/data"
  initdb -D "$postgres_dir/data" --encoding=UTF8 --locale=en_US.UTF-8 -A trust -U "$PGUSER"
fi

# start postgres
postgres -D "$postgres_dir/data" -k "$postgres_dir" > "$postgres_dir/postgres.log" 2>&1 &
psql_pid="$!"
echo "$psql_pid" > "$postgres_dir/postgres.pid"

sleep 5
cat "$postgres_dir/postgres.log"
echo
ps -fp "$psql_pid"
