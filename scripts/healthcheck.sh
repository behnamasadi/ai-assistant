#!/usr/bin/env bash
# Health check for both docker-compose and local/conda runs.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f .env ]]; then
  echo "✖ .env missing"
  exit 1
fi
# shellcheck disable=SC1091
set -a; source .env; set +a

use_docker=false
if command -v docker >/dev/null 2>&1 && docker compose ps --status running 2>/dev/null | grep -q redis; then
  use_docker=true
fi

if $use_docker; then
  echo "→ docker compose services"
  docker compose ps
  RCLI=(docker compose exec -T redis redis-cli -a "${REDIS_PASSWORD}")
else
  echo "→ local PIDs"
  for p in run/*.pid; do
    [[ -f "$p" ]] || continue
    name="$(basename "$p" .pid)"
    pid="$(cat "$p")"
    if kill -0 "$pid" 2>/dev/null; then
      echo "  ✔ $name (pid $pid)"
    else
      echo "  ✖ $name (stale pid $pid)"
    fi
  done
  RCLI=(redis-cli -h "${REDIS_HOST:-localhost}" -p "${REDIS_PORT:-6379}")
  [[ -n "${REDIS_PASSWORD:-}" ]] && RCLI+=(-a "$REDIS_PASSWORD")
fi

echo "→ Redis ping"
"${RCLI[@]}" ping

echo "→ Queue depths"
printf "  tasks:queue     = "; "${RCLI[@]}" LLEN tasks:queue
printf "  tasks:qa_queue  = "; "${RCLI[@]}" LLEN tasks:qa_queue

echo "✔ Healthcheck complete"
