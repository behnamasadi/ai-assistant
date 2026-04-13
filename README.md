# 🤖 Claude Code Multi-Agent System via Telegram

A production-ready autonomous development pipeline powered by Claude Code agents, triggered by voice or text messages through Telegram. Works with **any repository** — just point it at your project via environment variables. Three specialized agents — a Developer, a Code Reviewer, and a UI Tester — collaborate to build, review, test, and deliver your feature automatically. Questions and conversations are answered directly without creating tasks.

---

## 📋 Table of Contents

- [Overview](#overview)
- [Quick Start](#quick-start)
- [System Architecture](#system-architecture)
- [Agent Roles](#agent-roles)
- [Workflow Pipeline](#workflow-pipeline)
- [Tech Stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Project Structure](#project-structure)
- [Installation](#installation)
- [Configuration & Environment Variables](#configuration--environment-variables)
- [Security & Credentials](#security--credentials)
- [OAuth & Authentication Strategy](#oauth--authentication-strategy)
- [Docker Setup](#docker-setup)
- [Agent Prompt Templates](#agent-prompt-templates)
- [Claude Code Skills & Prompt Collections](#claude-code-skills--prompt-collections)
- [Running the System](#running-the-system)
- [Bot Commands](#bot-commands)
- [Workflow Walkthrough](#workflow-walkthrough-1)
- [Error Handling & Monitoring](#error-handling--monitoring)
- [Deployment Checklist](#deployment-checklist)
- [References](#references)

---

## Overview

This system allows you to act as a remote technical director. You send a voice or text message from anywhere via Telegram, and the system:

1. Transcribes your voice message (local Whisper on GPU, free) or accepts text directly
2. Parses the task and pushes it to a Redis task queue
3. Spins up a **Developer Agent** that creates a feature branch and implements the change
4. A **Code Reviewer** performs static analysis — security, architecture, correctness (gate 1)
5. If code review passes, a **UI Tester** deploys to dev and runs Playwright browser tests with health scoring (gate 2)
6. If either gate finds issues, feedback is sent back to the Developer Agent and the loop continues
7. Once both gates pass, the branch is deployed to the **dev environment** for your review
8. You receive a Telegram message with a screenshot and link — **you confirm or reject**
9. On confirmation: the branch is merged to `main`, pushed, and deployed to **production**

---

## Quick Start

Follow these steps in order. This assumes the **conda / local** workflow — for Docker, jump to [Docker Setup](#docker-setup).

### Step 1 — Create your Telegram bot (2 minutes)

A Telegram bot is a bot account you own, controlled via an HTTP API token. You must create one yourself via BotFather:

1. Open Telegram, search for **@BotFather**, send `/newbot`
2. Pick a display name and a username ending in `bot` (e.g. `my_coder_bot`)
3. BotFather replies with a token like `123456789:AAE...` — this is your `TELEGRAM_BOT_TOKEN`
4. Open a chat with your new bot and send `/start` once — the bot replies with available commands (`/status`, `/tasks`, `/task <id>`)
5. Find your own Telegram user ID: message **@userinfobot** → it replies with your numeric ID. This is your `TELEGRAM_ALLOWED_USER_ID`

**Who the bot replies to:** only you. Every incoming message is filtered against `TELEGRAM_ALLOWED_USER_ID` in `bot/main.py`. Without this lock, anyone who discovers your bot's username could trigger the agents and spend your Anthropic credit.

**If you ever leak your token** (pasted in chat, committed to git, screenshotted): immediately run `/revoke` in BotFather to invalidate it and generate a new one.

### Step 2 — Authenticate Claude (Max subscription, no API key needed)

This project is configured to use your **Claude Max subscription** by default. No separate API billing, no `ANTHROPIC_API_KEY` required.

How it works: `claude-agent-sdk` is a wrapper around the `claude` CLI in headless mode, and the CLI stores its auth token in `~/.claude/` after a one-time browser login. Every agent run then draws from your Max quota — exactly the same pool as your interactive Claude Code sessions.

Do this once, after `setup_conda.sh` has installed the CLI:

```bash
conda activate ai-assistant
claude                # opens a browser → sign in with your Claude Max account
```

Leave `ANTHROPIC_API_KEY` **empty** in `.env`. If it's set, the SDK will prefer the API key and bill your API account instead of using Max.

**Rate limits to know about:** Max's Claude Code quota is generous for a single interactive user but not unlimited. A tight dev↔QA feedback loop running multiple times per hour can hit the cap — if that happens you'll see `429` / rate-limit errors in `logs/developer_agent.log` and the agent will back off. For this project's normal use (a handful of tasks per day) it's fine.

**Docker:** if you switch to Docker Compose later, `~/.claude` is already mounted into the dev and qa containers so the same login carries over — no extra setup.

### Step 3 — Credentials reference

| Variable | Required | How to get it |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | **yes** | BotFather (Step 1) |
| `TELEGRAM_ALLOWED_USER_ID` | **yes** | @userinfobot |
| `REDIS_PASSWORD` | **yes** | pick any strong random string |
| `GIT_REPO_PATH` | **yes** | path to the target repo (e.g. `./workspace/project`) |
| `GIT_REMOTE_URL` | yes for push | SSH URL — `~/.ssh` is mounted so keys are reused |
| `ANTHROPIC_API_KEY` | **no** (leave empty) | Only set if you want to bypass Max and bill API credit instead |
| `WHISPER_MODEL` | optional | Local Whisper model size: `tiny`, `base` (default), `small`, `medium`, `large-v3`, `turbo` |
| `OPENAI_API_KEY` | optional | Powers message triage (question vs task classification) and direct question answering. Also fallback for voice transcription if local Whisper fails |
| `DEPLOY_PROD_COMMAND` | optional | Shell command to deploy to production after approval (e.g. `make deploy-prod`). Empty → skip deploy |
| `TEST_GOOGLE_EMAIL` / `_PASSWORD` | optional | Only if your target app has Google OAuth and QA needs to log in |
| `TEST_GITHUB_USERNAME` / `_PASSWORD` | optional | Same, for GitHub OAuth |
| `WEB_APP_START_COMMAND` | optional | Only if QA should boot a live web app (e.g. `npm run dev`). Empty → code-review-only |

### Step 4 — Clone the target repo into `workspace/`

**Important:** the agents do not edit this ai-assistant repo. They edit whichever project you clone into `workspace/project`. SSH keys must be authorized on that remote, because the developer agent pushes feature branches.

```bash
git clone git@github.com:you/your-project.git workspace/project
```

### Step 5 — Install and run

```bash
./scripts/setup_conda.sh            # creates conda env, installs Playwright + Claude Code CLI
conda activate ai-assistant
claude                               # one-time Max login (opens browser, only needs to run once)
nano .env                            # fill in the required values
./scripts/run_local.sh               # starts redis + bot + dev agent + qa agent
tail -f logs/*.log                   # watch the pipeline in real time
```

`run_local.sh` will refuse to start if it can't find either a `~/.claude/credentials.json` or an `ANTHROPIC_API_KEY`, so if you forget the login step it fails fast with a clear message.

Then open Telegram, message your bot, e.g.:

> *"Add a /health endpoint that returns `{status: ok}`."*

You should see: task queued → dev agent creates `feature/t-<id>` branch → commits and pushes → code reviewer checks security/architecture → UI tester deploys and runs browser tests → dev↔review↔test feedback loop if needed → deploys to dev → you get a Telegram message with screenshot asking to approve or reject → on approve, merged to main and deployed to prod. Every transition is sent to you as a Telegram notification.

To stop everything: `./scripts/stop_local.sh`

### Minimum smoke test

If you just want to verify the pipeline end-to-end without Google/GitHub/web-app complexity: set only the required values, leave `ANTHROPIC_API_KEY` empty, log in with `claude`, and send text messages only. Voice messages work out of the box (local Whisper, no API key needed). The Code Reviewer will still do static analysis; the UI Tester will skip browser tests if `WEB_APP_START_COMMAND` is empty.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        YOU (Remote)                         │
│          Send voice or text message via Telegram            │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  TELEGRAM BOT SERVICE                       │
│  - Listens for messages (voice + text)                      │
│  - Transcribes voice via local Whisper (GPU, free)          │
│  - Parses intent and creates task object                    │
│  - Pushes task to Redis queue                               │
│  - Listens for completion events → sends reply to you       │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                     REDIS (Message Broker)                  │
│  - 3 task queues: dev, review, ui_test                      │
│  - Event bus (agent-to-agent notifications)                 │
│  - Status tracking per task                                 │
└────────┬──────────────────┬───────────────────┬─────────────┘
         │                  │                   │
         ▼                  ▼                   ▼
┌────────────────┐ ┌────────────────┐ ┌──────────────────────┐
│ DEVELOPER AGENT│ │ CODE REVIEWER  │ │     UI TESTER        │
│                │ │   (Gate 1)     │ │     (Gate 2)         │
│ - Creates      │ │                │ │                      │
│   feature      │ │ - Static code  │ │ - Deploys branch     │
│   branch       │ │   analysis     │ │   to dev env         │
│ - Implements   │ │ - Security:    │ │ - Playwright browser │
│   the feature  │ │   injection,   │ │   smoke tests        │
│ - Has Docker   │ │   auth, paths  │ │ - Claude-driven      │
│   socket + GPU │ │ - Architecture │ │   interactive UI     │
│ - Commits +    │ │   review       │ │   testing            │
│   pushes       │ │ - No browser,  │ │ - Health score       │
│                │ │   no edits     │ │   (0-100)            │
│ DEV_COMPLETE──▶│ │ REVIEW_PASSED─▶│ │ UI_TEST_PASSED──▶   │
└────────────────┘ └────────────────┘ └──────────────────────┘
         ▲                  │                   │
         │    (FEEDBACK)    │     (FEEDBACK)    │
         └──────────────────┴───────────────────┘
                    Feedback Loop
                  (max 3 iterations)

                              │ (if both gates passed)
                              ▼
              ┌───────────────────────────────┐
              │    DEPLOY TO DEV + NOTIFY     │
              │  - Deploy branch to dev env    │
              │    ($WEB_APP_URL)              │
              │  - Take screenshot for review │
              │  - Send Telegram message with │
              │    screenshot + health score  │
              └───────────────┬───────────────┘
                              │
                              ▼
              ┌───────────────────────────────┐
              │      YOU REVIEW ON DEV        │
              │  - Open dev site in your      │
              │    browser                    │
              │  - Test the feature yourself  │
              │  - Reply on Telegram:         │
              │    ✅ "approve" or ❌ "reject"│
              └───────────────┬───────────────┘
                              │
                ┌─────────────┴─────────────┐
                │                           │
         ✅ approved                  ❌ rejected
                │                           │
                ▼                           ▼
 ┌──────────────────────┐   ┌──────────────────────────┐
 │  MERGE + DEPLOY PROD │   │  NOTIFY — task closed    │
 │  - Merge to main     │   │  (branch kept for future │
 │  - Push to remote    │   │   rework if needed)      │
 │  - Deploy to prod    │   └──────────────────────────┘
 │  - Telegram: ✅ Done │
 └──────────────────────┘
```

---

## Agent Roles

### 🧑‍💻 Developer Agent

- Receives task prompt from Redis queue
- Creates a new `feature/<task-id>` git branch
- Uses Claude Code SDK in headless mode with tools: `Read`, `Write`, `Edit`, `Bash`, plus Playwright MCP browser
- Has Docker socket + GPU access + host networking for deployment tasks
- Implements the requested feature or fix
- Runs compile checks and tests after implementation
- Browses the dev URL (`WEB_APP_URL`) to verify UI changes visually
- Commits and pushes the branch
- Publishes a `DEV_COMPLETE` event → hands off to Code Reviewer
- If feedback comes back from either gate, addresses every item before resubmitting

### 🔍 Code Reviewer (Gate 1)

- Listens for `DEV_COMPLETE` events on Redis
- Checks out the feature branch
- Runs a 3-pass review using Claude Code SDK (read-only — no browser, no edits):
  - **Pass 1 — Critical**: SQL injection, command injection, auth/authz, secret leakage, race conditions, path traversal
  - **Pass 2 — Structural**: state consistency, config schema, enum completeness, license compliance, dead code, test gaps
  - **Pass 3 — Style**: naming, import order, magic numbers (informational only)
- Produces a structured verdict: `PASSED`, `FEEDBACK`, or `BLOCKED`
- If `PASSED` → hands off to UI Tester (gate 2)
- If `FEEDBACK` → sends back to Developer with specific file:line references

### 🧪 UI Tester (Gate 2)

- Listens for `REVIEW_PASSED` events on Redis
- Deploys branch to dev environment
- Runs automated Playwright browser smoke test (login, page walk, console errors, network failures)
- Then runs Claude-driven interactive UI testing via MCP Playwright:
  - Verifies the requested feature works visually
  - Checks for regressions in adjacent tabs/panels
  - Takes screenshots as evidence
  - Produces a health score (0-100)
- Produces a structured verdict: `PASSED` (health >= 70), `FEEDBACK`, or `BLOCKED`
- If `PASSED` → deploys to dev, takes final screenshot, notifies you for human review
- If `FEEDBACK` → sends back to Developer with screenshot evidence

---

## Workflow Pipeline

The full lifecycle of a feature request, from Telegram to production:

### Phase 1 — Task Intake
1. You send a voice or text message on Telegram describing the feature
2. Voice is transcribed locally by Whisper running on your GPU (free, no API key)
3. The bot creates a task and pushes it to the Redis queue

### Phase 2 — Development
4. The Developer Agent picks up the task and creates a `feature/t-<task-id>` branch
5. It implements the feature using Claude Code SDK in headless mode (with Docker/GPU access)
6. It commits and pushes the branch, then publishes a `DEV_COMPLETE` event

### Phase 3 — Code Review (Gate 1)
7. The Code Reviewer picks up the `DEV_COMPLETE` event
8. It checks out the branch and runs a 3-pass static analysis (security → structural → style)
9. If critical issues are found → `REVIEW_FEEDBACK` event → Dev Agent fixes → back to step 7 (max 3 rounds)
10. If review passes → `REVIEW_PASSED` event → hands off to UI Tester

### Phase 4 — UI Testing (Gate 2)
11. The UI Tester picks up the `REVIEW_PASSED` event
12. It deploys the branch to dev, runs automated Playwright smoke tests, then Claude-driven interactive UI testing
13. It produces a health score (0-100) and takes screenshots as evidence
14. If issues are found → `UI_TEST_FEEDBACK` event → Dev Agent fixes → back to step 7 (max 3 rounds total)
15. If UI test passes → `UI_TEST_PASSED` event → deploys to dev environment

### Phase 5 — Human Review (you)
16. The branch is live on the **dev environment**
17. You receive a Telegram message with a screenshot and health score: _"Feature `t-<id>` is ready for review — reply **approve** or **reject**"_
18. You open the dev site in your browser, test the feature manually
19. You reply on Telegram:
    - **"approve"** → proceed to Phase 6
    - **"reject"** → task is closed, branch is kept for future rework

### Phase 6 — Production Deploy
20. The branch is merged to `main`
21. Pushed to the remote repository
22. Deployed to **production** via `DEPLOY_PROD_COMMAND`
23. You receive a final Telegram message: _"Feature `t-<id>` deployed to production"_

### Key guarantees
- **Nothing reaches production without your explicit approval** on Telegram
- Dev ↔ QA feedback loop is capped at 3 rounds to prevent infinite loops
- Every state transition is logged to Redis and notified to you on Telegram
- If any step fails, you get an error notification with details

---

## Tech Stack

| Layer | Technology |
|---|---|
| Voice Interface | Telegram Bot API |
| Voice Transcription | OpenAI Whisper (local, GPU) |
| Bot Library | `python-telegram-bot` |
| Task Queue & Events | Redis + `redis-py` |
| Agent Runtime | Claude Code Agent SDK (`claude-agent-sdk`) |
| Browser Automation | Playwright |
| Git Management | GitPython |
| Async Runtime | Python `asyncio` + `aiohttp` |
| Containerization | Docker + Docker Compose |
| Secret Management | `.env` file (dev) / Docker Secrets (prod) |
| OS | Ubuntu 22.04 LTS |

---

## Prerequisites

### System Requirements (Ubuntu 22.04+)

```bash
# Update system
sudo apt-get update && sudo apt-get upgrade -y

# Install core dependencies
sudo apt-get install -y \
  python3.10 \
  python3-pip \
  python3-venv \
  git \
  redis-server \
  curl \
  nodejs \
  npm \
  docker.io \
  docker-compose
```

### Python Packages

```bash
pip install \
  claude-agent-sdk \
  python-telegram-bot \
  redis \
  GitPython \
  playwright \
  aiohttp \
  python-dotenv \
  asyncio
```

### Playwright Browsers

```bash
playwright install chromium
playwright install-deps
```

### Anthropic API Key

Get your API key from [https://console.anthropic.com](https://console.anthropic.com)

### Telegram Bot Token

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` and follow the instructions
3. Copy the bot token

---

## Project Structure

```
ai-assistant/
├── docker-compose.yml
├── .env.example
├── .env                          # Never commit this
├── .gitignore
├── README.md
│
├── bot/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                   # Telegram bot listener
│   ├── dashboard.py              # Web dashboard (FastAPI, port 8095)
│   ├── task_publisher.py         # Pushes tasks to Redis
│   └── notification_listener.py  # Listens for completion events
│
├── developer_agent/
│   ├── Dockerfile                # python:3.11-slim + Docker CLI + make
│   ├── requirements.txt
│   ├── main.py                   # Developer agent worker
│   └── prompts/
│       └── developer.md          # System prompt (gstack patterns)
│
├── code_reviewer/                # Gate 1 — static code analysis
│   ├── Dockerfile                # python:3.11-slim + git
│   ├── requirements.txt
│   ├── main.py                   # Code reviewer worker
│   └── prompts/
│       └── reviewer.md           # 3-pass review prompt
│
├── ui_tester/                    # Gate 2 — browser UI testing
│   ├── Dockerfile                # Playwright image + Claude Code CLI
│   ├── requirements.txt
│   ├── main.py                   # UI tester worker
│   ├── browser_tester.py         # Playwright smoke test + health scoring
│   ├── oauth_helper.py           # Test OAuth login flow
│   └── prompts/
│       └── tester.md             # Systematic UI testing prompt
│
├── qa_agent/                     # Legacy (kept for backwards compat)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py
│   ├── browser_tester.py
│   ├── oauth_helper.py
│   └── prompts/
│       ├── qa_reviewer.md
│       └── security_review.md
│
├── shared/
│   ├── redis_client.py           # 3 queues: dev, review, ui_test
│   ├── task_schema.py            # Task + event data models
│   ├── git_manager.py            # Branch creation, commit, push
│   └── logger.py                 # Centralized logging
│
└── scripts/
    ├── setup_conda.sh            # First-time conda env setup
    ├── run_local.sh              # Start all services locally
    ├── stop_local.sh             # Stop all local services
    └── healthcheck.sh            # System health check
```

---

## Installation

```bash
# Clone the repository
git clone https://github.com/your-org/claude-agent-system.git
cd claude-agent-system

# Copy environment template
cp .env.example .env

# Edit .env with your credentials
nano .env

# Run setup script
chmod +x scripts/setup.sh
./scripts/setup.sh
```

---

## Configuration & Environment Variables

Create a `.env` file based on `.env.example`:

```env
# ─── Anthropic ───────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxx

# ─── Telegram ────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_ALLOWED_USER_ID=123456789   # Your Telegram user ID (security lock)

# ─── Redis ───────────────────────────────────────────────
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=your_redis_password

# ─── Git ─────────────────────────────────────────────────
GIT_REPO_PATH=/workspace/your-project
GIT_REMOTE_URL=git@github.com:your-org/your-project.git
GIT_DEFAULT_BRANCH=main

# ─── Web App ─────────────────────────────────────────────
WEB_APP_URL=http://web-app:3000
WEB_APP_START_COMMAND=npm run dev

# ─── OAuth Test Account (Google) ─────────────────────────
TEST_GOOGLE_EMAIL=qa-test@your-domain.com
TEST_GOOGLE_PASSWORD=your_test_account_password

# ─── OAuth Test Account (GitHub) ─────────────────────────
TEST_GITHUB_USERNAME=your-qa-bot-account
TEST_GITHUB_PASSWORD=your_test_account_password

# ─── App Mode ────────────────────────────────────────────
TESTING_MODE=true         # Enables mock login bypass in dev
APP_ENV=development       # development | production
```

---

## Security & Credentials

### General Rules

- **Never commit `.env`** — add it to `.gitignore` immediately
- Rotate your `ANTHROPIC_API_KEY` if it is ever exposed
- Use a **dedicated test Gmail and GitHub account** — never use your personal credentials
- Lock the Telegram bot to your own user ID via `TELEGRAM_ALLOWED_USER_ID` so only you can trigger agents
- Redis should be password-protected and not exposed to the public internet

### Passing Credentials to Containers

Credentials are injected at runtime via the `.env` file in Docker Compose. They are **never baked into the Docker image**.

```yaml
# docker-compose.yml (excerpt)
services:
  developer-agent:
    env_file:
      - .env
```

### Mounted Volumes for Shared Files

Your project codebase is mounted as a volume so all agents work on the same files:

```yaml
volumes:
  - ./workspace:/workspace  # Shared project directory
  - ~/.ssh:/root/.ssh:ro    # SSH keys for git push (read-only)
```

---

## OAuth & Authentication Strategy

Your web app uses OAuth 2.0 with Google and GitHub federated login. For automated QA testing, use the following hybrid approach:

### Strategy 1: Dedicated Test OAuth Accounts (Recommended for E2E)

1. Create a dedicated Gmail account: `qa-test@yourdomain.com`
2. Create a dedicated GitHub account for QA testing
3. Register these accounts in your app as normal users
4. Store credentials in `.env` (never in code)
5. The QA agent's Playwright script logs in via the real OAuth flow using these accounts

```python
# oauth_helper.py (simplified)
async def login_with_google(page, email, password):
    await page.goto(f"{WEB_APP_URL}/login")
    await page.click("button[data-provider='google']")
    await page.fill("input[type='email']", email)
    await page.click("#identifierNext")
    await page.fill("input[type='password']", password)
    await page.click("#passwordNext")
    await page.wait_for_url(f"{WEB_APP_URL}/dashboard")
```

### Strategy 2: Mock Login Bypass (Recommended for Fast Dev Loop)

Add a special endpoint in your app that only works when `TESTING_MODE=true`:

```
GET /test-login?user=qa-test-user
```

This sets the session cookie directly without going through OAuth. The QA agent hits this endpoint first to get authenticated, then proceeds with UI testing. **This endpoint must be disabled in production.**

---

## Docker Setup

### `docker-compose.yml`

```yaml
services:
  redis:
    image: redis:7-alpine
    command: ["redis-server", "--requirepass", "${REDIS_PASSWORD}"]
    volumes:
      - redis_data:/data
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]

  bot:
    build: { context: ., dockerfile: bot/Dockerfile }
    env_file: .env
    depends_on: { redis: { condition: service_healthy } }

  dashboard:
    build: { context: ., dockerfile: bot/Dockerfile }
    command: ["python", "-m", "bot.dashboard"]
    ports: ["8095:8095"]
    env_file: .env
    depends_on: { redis: { condition: service_healthy } }

  developer-agent:
    build: { context: ., dockerfile: developer_agent/Dockerfile }
    env_file: .env
    volumes:
      - ./workspace:/workspace
      - ${HOME}/.ssh:/root/.ssh:ro
      - ${HOME}/.claude:/root/.claude
      - /var/run/docker.sock:/var/run/docker.sock   # Docker CLI access
    network_mode: host                               # GPU containers on localhost
    deploy:
      resources:
        reservations:
          devices:
            - { driver: nvidia, count: all, capabilities: [gpu] }
    depends_on: { redis: { condition: service_healthy } }

  code-reviewer:
    build: { context: ., dockerfile: code_reviewer/Dockerfile }
    env_file: .env
    volumes:
      - ./workspace:/workspace
      - ${HOME}/.ssh:/root/.ssh:ro
      - ${HOME}/.claude:/root/.claude
    depends_on: { redis: { condition: service_healthy } }

  ui-tester:
    build: { context: ., dockerfile: ui_tester/Dockerfile }
    env_file: .env
    volumes:
      - ./workspace:/workspace
      - ${HOME}/.ssh:/root/.ssh:ro
      - ${HOME}/.claude:/root/.claude
      - ui_test_artifacts:/tmp/ui_test_artifacts
    network_mode: host                               # Playwright → dev app on localhost
    depends_on: { redis: { condition: service_healthy } }

volumes:
  redis_data:
  ui_test_artifacts:
```

### Building and Starting

```bash
# Build all containers
docker-compose build

# Start the full system
docker-compose up -d

# View logs
docker-compose logs -f

# Restart a single service
docker-compose restart developer-agent

# Stop everything
docker-compose down
```

---

## Agent Prompt Templates

Use the following as base system prompts for each agent. Customize them to match your project's conventions. Additional high-quality prompts can be found at:
- [Piebald AI Claude Code System Prompts](https://github.com/Piebald-AI/claude-code-system-prompts)
- [Awesome Claude Code Subagents](https://github.com/VoltAgent/awesome-claude-code-subagents)
- [Claude Code QA Agents](https://github.com/darcyegb/ClaudeCodeAgents)

### Developer Agent System Prompt (`prompts/developer.md`)

```markdown
You are a senior software developer working on a feature branch.
Your job is to implement the requested feature cleanly and correctly.

Guidelines:
- Always work on the feature branch you are given. Never commit to main.
- Write clean, readable, well-commented code.
- Follow the project's existing code style and conventions.
- After implementing, run existing tests with Bash to verify nothing is broken.
- Commit with a clear commit message describing what was done.
- Do not introduce new dependencies without noting them in your summary.
- When done, output a summary including: branch name, commit hash, files changed, and a brief description.
```

### QA Agent System Prompt (`prompts/qa_reviewer.md`)

```markdown
You are a senior QA engineer and code reviewer.
Your job is to review the developer's feature branch and validate it works correctly.

Guidelines:
- Check out the feature branch provided.
- Read the changed files and understand what was implemented.
- Start the web application and run Playwright browser tests.
- Test the happy path and at least two edge cases.
- Review code for: logic errors, security vulnerabilities, missing error handling, and style consistency.
- If issues are found, output them as a structured list with file path, line number, severity (high/medium/low), and description.
- If everything passes, output: APPROVED with a brief summary.
- Be constructive. Focus on blocking issues first.
```

---

## Claude Code Skills & Prompt Collections

Claude Code supports **skills** — reusable prompt files that teach the
agents specialized workflows (QA testing, code review, debugging,
deployment, etc.). Third-party collections like
[gstack](https://github.com/garrytan/gstack) bundle dozens of
production-quality skills you can install in seconds.

### How skills work

A skill is a `SKILL.md` markdown file (with YAML frontmatter) stored in
a known directory. When invoked via `/skill-name`, Claude receives the
full content as a system message for the rest of the session.

| Location | Path | Scope |
|---|---|---|
| **Personal** | `~/.claude/skills/<name>/SKILL.md` | All your projects |
| **Project** | `.claude/skills/<name>/SKILL.md` | This repo only |

### Installing gstack (recommended)

[gstack](https://github.com/garrytan/gstack) is a curated collection of
skills for QA testing, code review, debugging, browsing, design review,
and more.

```bash
# Clone into your personal skills directory (one-time)
git clone https://github.com/garrytan/gstack ~/.claude/skills/gstack

# Update later
cd ~/.claude/skills/gstack && git pull
```

After cloning, all gstack skills are immediately available as `/slash`
commands in any Claude Code session. Useful ones for this project:

| Skill | Command | What it does |
|---|---|---|
| Browse | `/browse` | Headless Chromium for QA testing, screenshots, form interaction |
| QA | `/qa` | Systematically test a web app and fix bugs found |
| Review | `/review` | Pre-landing PR review (SQL safety, trust boundaries, etc.) |
| Investigate | `/investigate` | Root-cause debugging with 4-phase methodology |
| Design Review | `/design-review` | Visual audit for spacing, hierarchy, consistency |

### Writing custom skills for your agents

You can write project-specific skills and commit them to this repo so
both the Developer and QA agents use them automatically:

```bash
mkdir -p .claude/skills/my-workflow
cat > .claude/skills/my-workflow/SKILL.md << 'EOF'
---
name: my-workflow
description: Run the standard deploy + smoke-test workflow
allowed-tools: Bash(git *) Read
---

# My Workflow

1. Run tests: `npm test`
2. Build: `npm run build`
3. Deploy: `./scripts/deploy.sh`
4. Smoke-test the /health endpoint
EOF
```

Key frontmatter fields:

| Field | Effect |
|---|---|
| `name` | Becomes the `/slash-command` |
| `description` | Tells Claude when to auto-load the skill |
| `disable-model-invocation: true` | Only you can invoke it (good for deploy/push) |
| `allowed-tools` | Pre-approve tools so Claude doesn't ask permission |
| `context: fork` | Run in an isolated subagent |

### Skills vs CLAUDE.md

- **CLAUDE.md** — always-on context loaded every session ("here's how our
  system works, our conventions, our architecture")
- **Skills** — loaded on demand when invoked ("here's how to deploy" /
  "here's how to run QA")

Use CLAUDE.md for facts and conventions. Use skills for procedures and
workflows.

### More prompt collections

- [gstack](https://github.com/garrytan/gstack) — QA, browse, review, debug, design
- [Piebald AI Claude Code System Prompts](https://github.com/Piebald-AI/claude-code-system-prompts) — system prompt templates
- [Awesome Claude Code Subagents](https://github.com/VoltAgent/awesome-claude-code-subagents) — subagent patterns
- [Claude Code QA Agents](https://github.com/darcyegb/ClaudeCodeAgents) — QA-focused agents

---

## Running the System

### Start Everything

```bash
docker-compose up -d
```

### Send a Task via Telegram

Open Telegram, find your bot, and send a voice or text message like:

> "Add a password reset feature to the login page. It should send an email with a reset link."

The system follows this pipeline:

1. **Transcribe** — voice is transcribed locally via Whisper (GPU, free); text is used directly
2. **Queue** — task is pushed to Redis
3. **Dev Agent** — creates `feature/t-<id>` branch, implements the feature, commits + pushes
4. **Code Review (gate 1)** — static analysis: security, architecture, correctness
5. **UI Test (gate 2)** — deploys to dev, Playwright smoke tests, Claude-driven interactive testing, health score
6. **Dev ↔ Review ↔ Test loop** — if either gate finds issues, feedback goes back to Dev Agent (up to 3 rounds)
7. **Deploy to dev** — once both gates pass, the branch is deployed to the dev environment
8. **You review** — you receive a Telegram message with screenshot + health score: _"Feature ready on dev — reply **approve** or **reject**"_
9. **You approve** — the branch is merged to `main`, pushed to remote, and deployed to **production**
10. **You reject** — the branch is kept but the task is closed; you can request changes in a follow-up message

You stay in control: **nothing reaches production without your explicit approval on Telegram.**

### Bot Commands

The Telegram bot supports the following commands:

| Command | Description |
|---|---|
| `/start` | Welcome message and command list |
| `/status` | Queue lengths (dev/QA pending), task counts by status, currently active tasks |
| `/tasks` | List all tasks (newest first) with status icons and short prompts |
| `/task <id>` | Full details for a specific task: status, branch, iteration, prompt, dev summary, QA feedback, errors, commit hash |

Voice messages show the transcript with inline buttons (**Confirm** / **Edit** / **Cancel**) before queuing.
When a feature is ready for review, you get inline buttons (**Approve & Deploy to Prod** / **Reject**).

### Message Triage

Text messages are automatically classified before routing:

- **Questions** (e.g. "what does the auth middleware do?") → answered directly in Telegram via an LLM call, no task created
- **Coding tasks** (e.g. "add a /health endpoint") → routed to the full developer→reviewer→tester pipeline

Classification uses a fast model (`TRIAGE_MODEL`, default: `gpt-4o-mini`) and requires `OPENAI_API_KEY` to be set. Without an API key, all messages are treated as coding tasks (original behaviour).

### Web Dashboard

A real-time web dashboard runs alongside the bot at **http://localhost:8095**:

- **Stats overview** — total tasks, dev/QA queue lengths, awaiting review, deployed, failed
- **Task list** — all tasks with status badges, prompt previews, timestamps
- **Filter bar** — filter by All, Active, Awaiting Review, Deployed, Failed
- **Task details** — click a card to expand: full prompt, branch, iteration, dev summary, QA feedback, errors, commit hash
- **Approve/Reject** — buttons appear on tasks awaiting review (same as Telegram approval)
- **Auto-refresh** — updates every 10 seconds

The dashboard starts automatically with `run_local.sh`. Configure the port with `DASHBOARD_PORT` env var (default: 8095).

### Monitor Progress

**From the dashboard:**
- Open http://localhost:8095 in your browser

**From Telegram:**
- `/status` — quick overview of what's happening
- `/tasks` — see all tasks and their current state
- `/task t-<id>` — drill into a specific task

**From the terminal:**
```bash
# Watch all agent logs in real-time
tail -f logs/*.log

# Or with docker compose
docker compose logs -f developer-agent code-reviewer ui-tester bot
```

---

## Error Handling & Monitoring

- All agents write structured logs to `logs/` (local) or stdout (Docker)
- Every state transition is sent to you as a Telegram notification
- If the Developer Agent fails, it publishes a `DEV_ERROR` event and Telegram notifies you
- If the Code Reviewer fails, it publishes a `REVIEW_ERROR` event
- If the UI Tester fails to start the browser, it publishes a `UI_TEST_ERROR` event
- Maximum feedback loop iterations: **3 rounds** across all gates — after that, the task is flagged for manual review and you are notified via Telegram
- Use `/status` to check queue lengths and active tasks at any time
- Use `/task <id>` to inspect errors, QA feedback, and dev summaries for any task
- Redis task status field tracks: `queued` → `dev_in_progress` → `review_in_progress` → `review_done` → `ui_test_in_progress` → `awaiting_review` → `approved` → `deployed` | `rejected` | `failed`
- Nothing is merged or deployed to production without explicit human approval via Telegram
- Feature branches are automatically deleted (local + remote) after merge

---

## Deployment Checklist

- [ ] `.env` file created and populated
- [ ] `.env` added to `.gitignore`
- [ ] Redis password set and working
- [ ] Telegram bot token valid and bot is running
- [ ] `TELEGRAM_ALLOWED_USER_ID` set to your own Telegram ID
- [ ] Local Whisper works (send a voice message, check transcription in logs)
- [ ] Dedicated test Google account created
- [ ] Dedicated test GitHub account created
- [ ] Test accounts registered in your web app
- [ ] SSH keys mounted for git push access
- [ ] Dev environment accessible: `WEB_APP_URL` loads correctly
- [ ] Prod deploy command works: `DEPLOY_PROD_COMMAND` runs successfully
- [ ] End-to-end test: send a message → dev agent codes → code reviewer passes → UI tester passes → deployed to dev → you approve on Telegram → merged + deployed to prod

---

## References

- [Claude Agent SDK Documentation](https://platform.claude.com/docs/en/agent-sdk/overview)
- [Claude Code Headless Mode](https://code.claude.com/docs/en/headless)
- [Claude Code System Prompts (Piebald AI)](https://github.com/Piebald-AI/claude-code-system-prompts)
- [Awesome Claude Code Subagents](https://github.com/VoltAgent/awesome-claude-code-subagents)
- [Awesome Claude Code](https://github.com/hesreallyhim/awesome-claude-code)
- [python-telegram-bot Docs](https://python-telegram-bot.org)
- [Playwright Python Docs](https://playwright.dev/python)
- [Redis Python Client](https://redis-py.readthedocs.io)

---

*This system was designed to be handed directly to a developer for implementation. All architectural decisions, security considerations, and tooling choices are documented above. Questions? Start with the Architecture section and work down.*
