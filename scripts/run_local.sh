#!/usr/bin/env bash
# Start redis + bot + developer agent + qa agent as local processes.
# PIDs are tracked in run/ so stop_local.sh can clean them up.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f .env ]]; then
  echo "✖ .env missing. Run scripts/setup_conda.sh first."
  exit 1
fi

# Preflight: agents need either Claude Max login or an API key.
if [[ -z "${ANTHROPIC_API_KEY:-}" ]] && \
   [[ ! -f "$HOME/.claude/credentials.json" ]] && \
   [[ ! -f "$HOME/.claude/.credentials.json" ]]; then
  echo "✖ No Claude auth found."
  echo "  Either log in with your Max account:  claude"
  echo "  or set ANTHROPIC_API_KEY in .env."
  exit 1
fi

# Load REDIS_* for the redis-server boot command.
set -a
# shellcheck disable=SC1091
source .env
set +a

mkdir -p logs run

start() {
  local name="$1"; shift
  local pidfile="run/${name}.pid"
  local logfile="logs/${name}.log"

  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "  $name already running (pid $(cat "$pidfile"))"
    return
  fi

  echo "→ Starting $name → $logfile"
  PYTHONPATH="$REPO_ROOT" nohup "$@" >>"$logfile" 2>&1 &
  echo $! >"$pidfile"
}

# 1. Redis (conda-provided redis-server) — skip if already reachable.
if ! redis-cli -h "${REDIS_HOST:-localhost}" -p "${REDIS_PORT:-6379}" \
      ${REDIS_PASSWORD:+-a "$REDIS_PASSWORD"} ping >/dev/null 2>&1; then
  REDIS_ARGS=(--port "${REDIS_PORT:-6379}" --daemonize no)
  if [[ -n "${REDIS_PASSWORD:-}" ]]; then
    REDIS_ARGS+=(--requirepass "$REDIS_PASSWORD")
  fi
  start redis redis-server "${REDIS_ARGS[@]}"
  sleep 1
else
  echo "  redis already reachable at ${REDIS_HOST}:${REDIS_PORT}"
fi

# 2. Agents + bot + dashboard
start bot              python -m bot.main
start developer_agent  python -m developer_agent.main
start code_reviewer    python -m code_reviewer.main
start ui_tester        python -m ui_tester.main
start dashboard        python -m bot.dashboard

echo
echo "✔ All services started."
echo "  Logs:   tail -f logs/*.log"
echo "  Status: scripts/healthcheck.sh"
echo "  Dashboard: http://localhost:8095"
echo "  Stop:   scripts/stop_local.sh"
