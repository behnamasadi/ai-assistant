#!/usr/bin/env bash
# First-time conda setup. Creates the env, installs Playwright browsers,
# and installs the Claude Code CLI (required by claude-agent-sdk headless).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

ENV_NAME="ai-assistant"

if ! command -v conda >/dev/null 2>&1; then
  echo "✖ conda not found. Install Miniconda or Anaconda first."
  exit 1
fi

# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"

if conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
  echo "→ Updating existing conda env: $ENV_NAME"
  conda env update -n "$ENV_NAME" -f environment.yml --prune
else
  echo "→ Creating conda env: $ENV_NAME"
  conda env create -f environment.yml
fi

conda activate "$ENV_NAME"

echo "→ Installing Playwright Chromium"
python -m playwright install chromium
python -m playwright install-deps chromium || true

if ! command -v claude >/dev/null 2>&1; then
  echo "→ Installing Claude Code CLI"
  curl -fsSL https://code.claude.com/install.sh | bash || {
    echo "⚠ Claude Code CLI install failed — install manually if needed."
  }
fi

# Claude Max authentication check. The agents inherit auth from ~/.claude/.
if [[ ! -f "$HOME/.claude/credentials.json" && ! -f "$HOME/.claude/.credentials.json" ]]; then
  echo
  echo "⚠ Claude Code is not logged in yet."
  echo "  Run this interactively to log in with your Claude Max account:"
  echo "    claude"
  echo "  (opens a browser, prompts for sign-in, stores auth in ~/.claude/)"
  echo "  Do this BEFORE running scripts/run_local.sh — the agents cannot start without it."
fi

if [[ ! -f .env ]]; then
  echo "→ Creating .env from template"
  cp .env.example .env
  echo "   Edit .env and fill in credentials."
fi

mkdir -p workspace logs run

echo
echo "✔ Conda setup complete."
echo "  Activate: conda activate $ENV_NAME"
echo "  Next:     edit .env, then run scripts/run_local.sh"
