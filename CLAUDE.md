# AI Assistant — Claude Code Multi-Agent System

## What this project does

This is a generic agent orchestration layer. It takes voice/text commands from
Telegram, runs a Developer agent to implement features on a branch, then
a Code Reviewer and UI Tester review and test against the dev environment,
looping until approved, then merges to `main` and deploys.

## Target project

The agents work on **any repository** configured via environment variables.
Set `GIT_REPO_PATH` to the local path and `GIT_REMOTE_URL` to the remote.
The dev site URL is configured via `WEB_APP_URL`.

## Running locally

```bash
conda activate ai-assistant
./scripts/run_local.sh      # starts redis + bot + dev agent + reviewers
tail -f logs/*.log           # watch in real time
./scripts/stop_local.sh      # stop everything
```

## Configuration

All project-specific settings come from `.env`:

| Variable | Purpose |
|---|---|
| `GIT_REPO_PATH` | Absolute path to the target repo |
| `GIT_REMOTE_URL` | SSH URL for git push |
| `WEB_APP_URL` | Dev site URL for testing |
| `WEB_APP_START_COMMAND` | Command to start the dev server (optional) |
| `DEPLOY_PROD_COMMAND` | Command to deploy to production (optional) |

## Key rules

- Never add `Co-Authored-By` lines to commits
- Only commercial-safe licenses (no GPL, CC-NC, AGPL)
- Agent prompts are generic — project-specific context comes from the target repo's own CLAUDE.md
