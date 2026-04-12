#!/usr/bin/env bash
# First-time setup for the multi-agent system.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f .env ]]; then
  echo "→ Creating .env from template"
  cp .env.example .env
  echo "   Edit .env and fill in credentials before running docker compose up."
fi

mkdir -p workspace

if [[ ! -d workspace/.git ]]; then
  echo "→ workspace/ is empty. Clone your target project into it:"
  echo "   git clone <your-repo-url> workspace"
fi

echo "→ Building docker images"
docker compose build

echo "✔ Setup complete. Next:"
echo "   1. Fill in .env"
echo "   2. Clone your target repo into ./workspace"
echo "   3. Run: docker compose up -d"
