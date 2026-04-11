# 🤖 Claude Code Multi-Agent System via Telegram

A production-ready autonomous development pipeline powered by Claude Code agents, triggered by voice messages through Telegram. Send a voice message, and two specialized agents — a Developer and a QA Reviewer — collaborate to build, review, and deliver your feature automatically.

---

## 📋 Table of Contents

- [Overview](#overview)
- [System Architecture](#system-architecture)
- [Agent Roles](#agent-roles)
- [Tech Stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Project Structure](#project-structure)
- [Installation](#installation)
- [Configuration & Environment Variables](#configuration--environment-variables)
- [Security & Credentials](#security--credentials)
- [OAuth & Authentication Strategy](#oauth--authentication-strategy)
- [Docker Setup](#docker-setup)
- [Agent Prompt Templates](#agent-prompt-templates)
- [Running the System](#running-the-system)
- [Workflow Walkthrough](#workflow-walkthrough)
- [Error Handling & Monitoring](#error-handling--monitoring)
- [Deployment Checklist](#deployment-checklist)
- [References](#references)

---

## Overview

This system allows you to act as a remote technical director. You send a voice message from anywhere via Telegram, and the system:

1. Transcribes your voice message (handled natively by Telegram)
2. Parses the task and pushes it to a Redis task queue
3. Spins up a **Developer Agent** that writes code on a feature branch
4. Notifies a **QA Agent** once the developer is done
5. The QA Agent reviews the code, runs the web app, clicks through UI flows, and provides feedback
6. If issues are found, the Developer Agent fixes them and the loop continues
7. Once approved, the code is merged to `main` and you receive a Telegram notification

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        YOU (Remote)                         │
│              Send voice message via Telegram                │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  TELEGRAM BOT SERVICE                       │
│  - Listens for messages (voice + text)                      │
│  - Telegram auto-transcribes voice to text                  │
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
┌──────────────────────┐      ┌─────────────────────────────┐
│   DEVELOPER AGENT    │      │        QA AGENT             │
│                      │      │                             │
│ - Pulls tasks from   │      │ - Listens for dev complete  │
│   Redis              │      │   events on Redis           │
│ - Runs Claude Code   │      │ - Checks out feature branch │
│   SDK (headless)     │      │ - Runs Claude Code SDK      │
│ - Works on feature   │      │   (headless)                │
│   branch             │      │ - Starts web app server     │
│ - Commits + pushes   │      │ - Runs Playwright browser   │
│ - Publishes done     │──────▶  automation tests           │
│   event to Redis     │      │ - Reviews code quality      │
└──────────────────────┘      │ - Approves OR publishes     │
               ▲              │   feedback to Redis         │
               │              └──────────────┬──────────────┘
               │                             │
               │         (if issues)         │
               └─────────────────────────────┘
                         Feedback Loop

                              │ (if approved)
                              ▼
              ┌───────────────────────────────┐
              │     MERGE TO MAIN + NOTIFY    │
              │  - git merge feature branch   │
              │  - Push to remote             │
              │  - Send Telegram message: ✅  │
              └───────────────────────────────┘
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

### 🔍 QA Agent

- Listens for `DEV_COMPLETE` events on Redis
- Checks out the feature branch
- Starts the local web application server
- Uses **Playwright** (headless browser) to navigate the app and test UI flows
- Logs in using a dedicated test OAuth account (Google/GitHub)
- Runs security review and code quality checks using specialized Claude Code prompts
- Publishes either `QA_APPROVED` or `QA_FEEDBACK` event to Redis
- Feedback includes specific file paths, line numbers, and issue descriptions

---

## Tech Stack

| Layer | Technology |
|---|---|
| Voice Interface | Telegram Bot API |
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

## Running the System

### Start Everything

```bash
docker-compose up -d
```

### Send a Task via Telegram

Open Telegram, find your bot, and send a voice message like:

> "Add a password reset feature to the login page. It should send an email with a reset link."

The system will:
1. Transcribe your voice
2. Create a task and push to Redis
3. Developer agent picks it up and starts coding
4. QA agent reviews when done
5. You get a Telegram notification when complete ✅

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
- Redis task status field tracks: `queued` → `dev_in_progress` → `qa_in_progress` → `approved` | `failed`

---

## Deployment Checklist

- [ ] `.env` file created and populated
- [ ] `.env` added to `.gitignore`
- [ ] Redis password set and working
- [ ] Telegram bot token valid and bot is running
- [ ] `TELEGRAM_ALLOWED_USER_ID` set to your own Telegram ID
- [ ] Dedicated test Google account created
- [ ] Dedicated test GitHub account created
- [ ] Test accounts registered in your web app
- [ ] SSH keys mounted for git push access
- [ ] `TESTING_MODE` endpoint disabled in production
- [ ] Docker Compose builds successfully
- [ ] End-to-end test: send a voice message and verify all steps complete

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
