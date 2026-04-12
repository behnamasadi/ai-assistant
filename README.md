# 🤖 Claude Code Multi-Agent System via Telegram

A production-ready autonomous development pipeline powered by Claude Code agents, triggered by voice messages through Telegram. Send a voice message, and two specialized agents — a Developer and a QA Reviewer — collaborate to build, review, and deliver your feature automatically.

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
4. Notifies a **QA Agent** once the developer is done
5. The QA Agent reviews the code, runs the web app, clicks through UI flows, and provides feedback
6. If issues are found, the Developer Agent fixes them and the loop continues
7. Once QA passes, the Developer Agent deploys the branch to **dev.magic-inspection.com** for your review
8. You receive a Telegram message with a link to the dev site — **you confirm or reject**
9. On confirmation: the branch is merged to `main`, pushed, and deployed to **production**

---

## Quick Start

Follow these steps in order. This assumes the **conda / local** workflow — for Docker, jump to [Docker Setup](#docker-setup).

### Step 1 — Create your Telegram bot (2 minutes)

A Telegram bot is a bot account you own, controlled via an HTTP API token. You must create one yourself via BotFather:

1. Open Telegram, search for **@BotFather**, send `/newbot`
2. Pick a display name and a username ending in `bot` (e.g. `my_coder_bot`)
3. BotFather replies with a token like `123456789:AAE...` — this is your `TELEGRAM_BOT_TOKEN`
4. Open a chat with your new bot and send `/start` once so Telegram registers the conversation
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
| `OPENAI_API_KEY` | optional | Fallback for voice transcription if local Whisper fails. Not needed — local Whisper is free |
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

You should see: task queued → dev agent creates `feature/t-<id>` branch → commits and pushes → QA agent reviews → dev↔QA feedback loop if needed → deploys to dev.magic-inspection.com → you get a Telegram message asking to approve or reject → on approve, merged to main and deployed to prod. Every transition is sent to you as a Telegram notification.

To stop everything: `./scripts/stop_local.sh`

### Minimum smoke test

If you just want to verify the pipeline end-to-end without Google/GitHub/web-app complexity: set only the required values, leave `ANTHROPIC_API_KEY` empty, log in with `claude`, and send text messages only. Voice messages work out of the box (local Whisper, no API key needed). QA will still do Claude-driven code review — it just won't boot a browser against a running app if `WEB_APP_START_COMMAND` is empty.

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
│  - Task queue (incoming tasks)                              │
│  - Event bus (agent-to-agent notifications)                 │
│  - Status tracking per task                                 │
└──────────────┬────────────────────────────┬─────────────────┘
               │                            │
               ▼                            ▼
┌──────────────────────────┐  ┌─────────────────────────────┐
│    DEVELOPER AGENT       │  │        QA AGENT             │
│                          │  │                             │
│ - Pulls tasks from Redis │  │ - Listens for dev complete  │
│ - Creates feature branch │  │   events on Redis           │
│ - Runs Claude Code SDK   │  │ - Checks out feature branch │
│   (headless)             │  │ - Runs Claude Code SDK      │
│ - Implements the feature │  │   (headless)                │
│ - Commits + pushes       │  │ - Starts web app on dev     │
│ - Publishes DEV_COMPLETE │──▶ - Runs Playwright browser   │
│   event to Redis         │  │   automation tests          │
└──────────────────────────┘  │ - Reviews code quality      │
               ▲              │ - Approves OR publishes     │
               │              │   feedback to Redis         │
               │              └──────────────┬──────────────┘
               │                             │
               │         (if issues)         │
               └─────────────────────────────┘
                         Feedback Loop

                              │ (if QA approved)
                              ▼
              ┌───────────────────────────────┐
              │    DEPLOY TO DEV + NOTIFY     │
              │  - Deploy branch to           │
              │    dev.magic-inspection.com    │
              │  - Send Telegram message:     │
              │    "Feature ready for review  │
              │    on dev — approve or reject" │
              └───────────────┬───────────────┘
                              │
                              ▼
              ┌───────────────────────────────┐
              │      YOU REVIEW ON DEV        │
              │  - Open dev.magic-inspection  │
              │    .com in your browser       │
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
- Uses Claude Code SDK in headless mode with tools: `Read`, `Write`, `Edit`, `Bash`
- Implements the requested feature or fix
- Runs basic unit tests after implementation
- Commits and pushes the branch
- Publishes a `DEV_COMPLETE` event to Redis with branch name, commit hash, and summary
- After QA approval: deploys branch to dev.magic-inspection.com for human review
- After human approval: merges to `main`, pushes, and deploys to production

### 🔍 QA Agent

- Listens for `DEV_COMPLETE` events on Redis
- Checks out the feature branch
- Deploys to dev environment (`make deploy-dev`)
- Uses **Playwright** (headless browser) to navigate dev.magic-inspection.com and test UI flows
- Logs in through HTTP basic auth + Authentik OAuth (Google/GitHub federated)
- Runs security review and code quality checks using specialized Claude Code prompts
- Publishes either `QA_APPROVED` or `QA_FEEDBACK` event to Redis
- Feedback includes specific file paths, line numbers, and issue descriptions

---

## Workflow Pipeline

The full lifecycle of a feature request, from Telegram to production:

### Phase 1 — Task Intake
1. You send a voice or text message on Telegram describing the feature
2. Voice is transcribed locally by Whisper running on your GPU (free, no API key)
3. The bot creates a task and pushes it to the Redis queue

### Phase 2 — Development
4. The Developer Agent picks up the task and creates a `feature/t-<task-id>` branch
5. It implements the feature using Claude Code SDK in headless mode
6. It commits and pushes the branch, then publishes a `DEV_COMPLETE` event

### Phase 3 — QA Review
7. The QA Agent picks up the `DEV_COMPLETE` event
8. It checks out the branch, deploys to the dev environment, and runs Playwright browser tests against dev.magic-inspection.com
9. It also runs code review (security, style, correctness) via Claude Code
10. If issues are found → `QA_FEEDBACK` event → Dev Agent fixes → back to step 7 (max 3 rounds)
11. If everything passes → `QA_APPROVED` event

### Phase 4 — Human Review (you)
12. The branch is deployed to **dev.magic-inspection.com**
13. You receive a Telegram message: _"Feature `t-<id>` is ready for review on dev.magic-inspection.com — reply **approve** or **reject**"_
14. You open the dev site in your browser, test the feature manually
15. You reply on Telegram:
    - **"approve"** → proceed to Phase 5
    - **"reject"** → task is closed, branch is kept for future rework

### Phase 5 — Production Deploy
16. The branch is merged to `main`
17. Pushed to the remote repository
18. Deployed to **production** (app.magic-inspection.com) via `make deploy-prod`
19. You receive a final Telegram message: _"Feature `t-<id>` deployed to production"_

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
claude-agent-system/
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
│   ├── task_publisher.py         # Pushes tasks to Redis
│   └── notification_listener.py # Listens for completion events
│
├── developer_agent/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                   # Developer agent worker
│   ├── git_manager.py            # Branch creation, commit, push
│   └── prompts/
│       └── developer.md          # Developer agent system prompt
│
├── qa_agent/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                   # QA agent worker
│   ├── browser_tester.py         # Playwright browser automation
│   ├── oauth_helper.py           # Test OAuth login flow
│   └── prompts/
│       ├── qa_reviewer.md        # QA agent system prompt
│       └── security_review.md    # Security review prompt
│
├── shared/
│   ├── redis_client.py           # Shared Redis connection helper
│   ├── task_schema.py            # Task and event data models
│   └── logger.py                 # Centralized logging
│
└── scripts/
    ├── setup.sh                  # First-time setup script
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
version: "3.9"

services:
  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis_data:/data
    ports:
      - "6379:6379"

  bot:
    build: ./bot
    env_file: .env
    depends_on:
      - redis
    restart: unless-stopped

  developer-agent:
    build: ./developer_agent
    env_file: .env
    volumes:
      - ./workspace:/workspace
      - ~/.ssh:/root/.ssh:ro
    depends_on:
      - redis
    restart: unless-stopped

  qa-agent:
    build: ./qa_agent
    env_file: .env
    volumes:
      - ./workspace:/workspace
      - ~/.ssh:/root/.ssh:ro
    depends_on:
      - redis
    restart: unless-stopped

  web-app:
    build: ./workspace
    env_file: .env
    ports:
      - "3000:3000"
    volumes:
      - ./workspace:/app

volumes:
  redis_data:
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
4. **QA Agent** — reviews code, deploys to dev, runs Playwright browser tests against dev.magic-inspection.com
5. **Dev ↔ QA loop** — if QA finds issues, it sends feedback to the Dev Agent which fixes and resubmits (up to 3 rounds)
6. **Deploy to dev** — once QA passes, the branch is deployed to dev.magic-inspection.com
7. **You review** — you receive a Telegram message: _"Feature ready on dev — reply **approve** or **reject**"_
8. **You approve** — the branch is merged to `main`, pushed to remote, and deployed to **production** (app.magic-inspection.com)
9. **You reject** — the branch is kept but the task is closed; you can request changes in a follow-up message

You stay in control: **nothing reaches production without your explicit approval on Telegram.**

### Monitor Progress

```bash
# Watch all agent logs in real-time
docker-compose logs -f developer-agent qa-agent bot

# Check Redis task queue
docker exec -it <redis-container-id> redis-cli -a $REDIS_PASSWORD KEYS "*"
```

---

## Error Handling & Monitoring

- All agents write structured logs to stdout (captured by Docker)
- If the Developer Agent fails, it publishes a `DEV_ERROR` event and Telegram notifies you
- If QA fails to start the browser, it retries three times before publishing a `QA_ERROR` event
- Maximum feedback loop iterations: **3 rounds** — after that, the task is flagged for manual review and you are notified via Telegram
- Redis task status field tracks: `queued` → `dev_in_progress` → `qa_in_progress` → `awaiting_review` → `approved` → `deployed` | `rejected` | `failed`
- Nothing is merged or deployed to production without explicit human approval via Telegram

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
- [ ] Dev environment accessible: dev.magic-inspection.com loads and basic auth works
- [ ] Prod deploy command works: `make deploy-prod` or equivalent
- [ ] End-to-end test: send a message → dev agent codes → QA reviews → deployed to dev → you approve on Telegram → merged + deployed to prod

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
