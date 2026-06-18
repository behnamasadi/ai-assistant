# AI Assistant — Claude Code Multi-Agent System

## What this project does

This is a generic agent orchestration layer. It takes voice/text commands from
Telegram, runs a Developer agent to implement features on a branch, then
a Code Reviewer and UI Tester review and test against the dev environment,
looping until approved, then merges to `main` and deploys.

## Conversation-first: nothing is built unless you ask explicitly

By default the bot is **read-only**. A plain text or voice message is answered as
a conversation — it can read the target repo and explain, review, or rate code,
but it will **never** create a branch or commit. Code only changes when you ask
explicitly:

- Type `/build <what to build>` (the guaranteed trigger), or
- Tap **🔨 Build it** on a transcribed voice message.

Even then, the planner posts the plan to Telegram and waits for your approval tap
before the developer agent commits anything. Two layers of consent.

Read-only project inspection (`bot/project_inspector.py`) uses the Claude Agent
SDK against `GIT_REPO_PATH` with a hard read-only guarantee: `Write`/`Edit` are
disallowed and a `can_use_tool` gate rejects any non-read-only Bash command.

Set `BUILD_REQUIRES_TRIGGER=false` to restore the old behaviour where the triage
classifier could auto-route a message into a build.

## Agent mode: full Claude Code over Telegram

`/agent on` turns the chat into a **full-access Claude Code session on the host
machine** — every text or voice message becomes a turn in one continuous agentic
conversation that can read, edit, run commands, and spawn sub-agents against
`GIT_REPO_PATH`, exactly like the CLI. This bypasses the read-only/plan gate, so
it is **off by default** and only ever reachable by the single allowed Telegram
user (`TELEGRAM_ALLOWED_USER_ID`) — that one gate is what makes exposing
Write/Edit/Bash acceptable. `/agent off` returns to safe mode; `/reset` clears the
conversation. Implemented in `bot/agent_session.py` (`permission_mode=
"bypassPermissions"`, per-chat session resume for context continuity).

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
| `BUILD_REQUIRES_TRIGGER` | `true` (default) = plain messages never build; only `/build`/🔨 do. `false` restores auto-routing |
| `INSPECT_TIMEOUT_SECONDS` | Max seconds for a read-only repo inspection (default `180`) |
| `AGENT_TURN_TIMEOUT_SECONDS` | Max seconds for one full-access agent-mode turn (default `900`) |

## Key rules

- Never add `Co-Authored-By` lines to commits
- Only commercial-safe licenses (no GPL, CC-NC, AGPL)
- Agent prompts are generic — project-specific context comes from the target repo's own CLAUDE.md
