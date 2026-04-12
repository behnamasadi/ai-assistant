#!/usr/bin/env bash
# Stop all locally-running services started by run_local.sh.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -d run ]]; then
  echo "nothing to stop"
  exit 0
fi

for pidfile in run/*.pid; do
  [[ -f "$pidfile" ]] || continue
  name="$(basename "$pidfile" .pid)"
  pid="$(cat "$pidfile")"
  if kill -0 "$pid" 2>/dev/null; then
    echo "→ Stopping $name (pid $pid)"
    kill "$pid" || true
    for _ in 1 2 3 4 5; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$pidfile"
done

echo "✔ Stopped."
