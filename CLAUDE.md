# AI Assistant — Claude Code Multi-Agent for magic-inspection-colmap

## What this project does

This is the agent orchestration layer. It takes voice/text commands from
Telegram, runs a Developer agent to implement features on a branch, then
a QA agent reviews and tests against `https://dev.magic-inspection.com/`,
looping until approved, then merges to `main`.

## Target project

The agents develop **magic-inspection-colmap** — a Gradio + FastAPI 3D
reconstruction pipeline for civil/industrial inspection.

- **Repo:** `/home/behnam/workspace/magic-inspection-colmap`
- **Prod:** `https://app.magic-inspection.com/` (port 7860)
- **Dev:** `https://dev.magic-inspection.com/` (port 7870)
- **GPU host:** 192.168.1.3 (this machine, RTX 3090)
- **Pi (nginx):** 192.168.1.2

## Running locally

```bash
conda activate ai-assistant
./scripts/run_local.sh      # starts redis + bot + dev agent + qa agent
tail -f logs/*.log           # watch in real time
./scripts/stop_local.sh      # stop everything
```

## Auth flow for dev site

Two layers:
1. HTTP basic auth (nginx) — creds in `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD`
2. Authentik OAuth via oauth2-proxy — creds in `TEST_GOOGLE_EMAIL` / `TEST_GOOGLE_PASSWORD`

## Key rules

- Never add `Co-Authored-By` lines to commits
- Only commercial-safe licenses (no GPL, CC-NC, AGPL)
- Use Rerun for 3D visualization, not Three.js
- Deploy dev: `make deploy-dev` (from magic-inspection-colmap)
- Deploy prod: `make deploy-here` (only from `main` branch)
