# AI Assistant — Local LLM with Ollama

[Ollama](https://github.com/ollama/ollama) serve LLMs locally HTTP server on `localhost:11434` 

---

## Installation

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Verify it is running:

```bash
ollama --version
ollama serve   # starts the server on http://localhost:11434
```

---

## Discovering Available Models

Ollama has **no command to dump the full model catalog**, you can visit [ollama library](https://ollama.com/library)


## Pulling Models

```bash
ollama pull qwen3.5:35b
```

Model weights are stored in `~/.ollama/models/` and reused across runs.

List what you have downloaded:

```bash
ollama list
```

---

## Running a Model

### Interactive REPL

```bash
ollama run qwen3.5:35b
```

This opens an interactive REPL. Type your prompt and press Enter. Press `Ctrl+D` or type `/bye` to exit.



### Check which models are currently loaded

```bash
ollama ps
```
---

### Other Commands

```bash
ollama show <model>      # Show model details and usage info
ollama rm <model>        # Remove a model from disk
ollama ps                # Show currently loaded models in memory
```



## Using Claude with Local Ollama Model

```bash
curl -fsSL https://claude.ai/install.sh | bash
```



```bash
ollama launch claude --model qwen3.5:35b
```

### Integrate gstack with Claude

1. install gstack globally for your own machine,
2. copy it into the project’s `.claude/skills/gstack`,
3. add a small `CLAUDE.md` section so the agent knows when to use it,
4. use only a few core commands first: `/office-hours`, `/plan-eng-review`, `/review`, `/qa`, `/ship`. That matches the project’s own quick-start flow and keeps adoption simple. ([GitHub][1])

Why this is the best pattern:

* gstack is designed as a **set of skills/slash commands**, not as a library you import into your codebase. It lives under agent skill directories and is discovered by compatible agents via the SKILL.md standard. ([GitHub][1])
* The repo explicitly recommends copying real files into `.claude/skills/gstack` rather than using a submodule, so teammates can clone the repo and have the setup available directly. It also says everything lives inside `.claude/` and does not modify your PATH or run background processes. ([GitHub][1])
* It is built around Claude Code first, but the same skill model can also work with Codex, Gemini CLI, or Cursor through the same skills mechanism. ([GitHub][1])

A practical integration pattern for **any project** is this:

```text
your-project/
├─ src/
├─ tests/
├─ docs/
├─ .claude/
│  ├─ skills/
│  │  └─ gstack/
│  └─ CLAUDE.md
└─ README.md
```

Recommended rollout:

**Phase 1: personal use only**

* install gstack in your home agent skills directory
* try it on one repo without committing anything yet
* use `/office-hours`, `/review`, `/qa`

**Phase 2: team adoption**

* copy gstack into `.claude/skills/gstack`
* commit it
* add project-specific guidance in `CLAUDE.md`
* document the expected workflow in `README.md`

**Phase 3: opinionated workflow**

* make `/plan-eng-review` part of feature kickoff
* make `/review` required before merge
* use `/qa` on staging
* optionally use `/codex` for a second-model review if you also use Codex CLI. ([GitHub][1])

What I would **not** do:

* do not wire gstack into your application code
* do not make it part of production Docker images
* do not force all 21 skills on day one
* do not let its repo-specific conventions replace your own architecture docs, tests, or CI

The highest-value integration is to use it as a **developer workflow copilot** around your project, not inside the project.

For your kind of work, I would tailor it like this:

* **Robotics/C++ repo**: use `/plan-eng-review`, `/review`, `/careful`, `/investigate`
* **Python/AI repo**: use `/office-hours`, `/plan-eng-review`, `/review`, `/qa`
* **Product/app repo**: add `/design-review`, `/qa`, `/document-release`

A minimal `CLAUDE.md` section for a project could be:

```md
## gstack

Use gstack skills from `.claude/skills/gstack`.

Preferred workflow for this repo:
1. `/office-hours` for new product ideas or scope changes
2. `/plan-eng-review` before major implementation
3. `/review` before merging any non-trivial branch
4. `/qa` for staging or UI flows
5. `/careful` when touching destructive scripts, migrations, or deployment steps

If gstack skills are not working, run:
`cd .claude/skills/gstack && ./setup`
```



## Using OpenCode with Local Ollama Model

## Using Open WebUI with Local Ollama Model

On Linux, the cleanest setup is to run Open WebUI with host networking:

```bash
docker rm -f open-webui

docker run -d \
  --network=host \
  -v open-webui:/app/backend/data \
  --name open-webui \
  --restart always \
  ghcr.io/open-webui/open-webui:main
```

With `--network=host`, the container uses the host network namespace. That means Open WebUI can talk to the host's Ollama listener directly on `127.0.0.1:11434`, so you do not need `-p 3000:8080` or `--add-host=host.docker.internal:host-gateway`.

Open Open WebUI at:

```text
http://127.0.0.1:8080
```

Then set the Ollama URL to:

```text
http://127.0.0.1:11434
```

If you do not want host networking and prefer Docker bridge networking, use the `host.docker.internal` approach instead:

```bash
docker rm -f open-webui

docker run -d \
  -p 3000:8080 \
  --add-host=host.docker.internal:host-gateway \
  -v open-webui:/app/backend/data \
  --name open-webui \
  --restart always \
  ghcr.io/open-webui/open-webui:main
```

Then set the Ollama URL to:

```text
http://host.docker.internal:11434
```

If Open WebUI still cannot see models even though `ollama ps` works on the host, Ollama is usually listening only on `127.0.0.1:11434`. In that case the Docker container cannot reach it from bridge mode.

Create a systemd override so Ollama listens on all interfaces:

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null <<'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
EOF

sudo systemctl daemon-reload
sudo systemctl restart ollama
```

Verify the listener:

```bash
ss -ltnp | rg 11434
```

You want to see `0.0.0.0:11434` or `*:11434`, not `127.0.0.1:11434`.

After that, refresh Open WebUI and your local models should appear when using `http://host.docker.internal:11434`.

### Local TTS and STT in Open WebUI

This machine uses Open WebUI in Docker, exposed on `http://127.0.0.1:3000` with the container port mapping `3000:8080`.

Speech to text runs fully locally inside the `open-webui` container through Whisper. It does not use OpenAI or any external API.

Current STT configuration:

- `WHISPER_MODEL=base`
- `WHISPER_MODEL_DIR=/app/backend/data/cache/whisper/models`

This was verified from the running `open-webui` logs with successful `POST /api/v1/audio/transcriptions` requests returning `200`.

Text to speech runs locally through a separate Docker container named `kokoro-web`. Open WebUI reaches it over the Docker network by container name, not by `localhost`.

The `openai`-named settings below apply only to TTS, because `kokoro-web` exposes an OpenAI-compatible API locally on the Docker network.

Current TTS configuration in `open-webui`:

- `AUDIO_TTS_ENGINE=openai`
- `AUDIO_TTS_OPENAI_API_BASE_URL=http://kokoro-web:3000/api/v1`
- `AUDIO_TTS_OPENAI_API_KEY=015193bc60fb8e35d31291ab25a3ec1e3c8582f6ea899b24`
- `AUDIO_TTS_MODEL=model_q8f16`
- `AUDIO_TTS_VOICE=af_heart`

Current authentication setting in `kokoro-web`:

- `KW_SECRET_API_KEY=015193bc60fb8e35d31291ab25a3ec1e3c8582f6ea899b24`

The TTS fix was changing the `kokoro-web` container to use `KW_SECRET_API_KEY`. The earlier `API_KEY` variable was not accepted by this image, which caused authentication failures. After this change, direct requests to `/api/v1/audio/speech` returned `200 OK` with `audio/mpeg`.

### Changing the TTS Voice

The current local Kokoro voice is:

- `AUDIO_TTS_VOICE=af_heart`

To switch to another voice, change `AUDIO_TTS_VOICE` in the `open-webui` container configuration, then restart the `open-webui` container. Replace `af_heart` with another voice supported by the running `kokoro-web` image.

For this setup, the voice selection is controlled from the Open WebUI container. The `kokoro-web` container can stay on the same API base URL and API key unless the TTS backend itself is being replaced.

### Making Local TTS Sound Better

This setup is already using the simplest local TTS path for Open WebUI:

- local `kokoro-web`
- `AUDIO_TTS_MODEL=model_q8f16`

The biggest improvement usually comes from changing the voice. Some Kokoro voices sound softer, more natural, or more expressive than others.

Text formatting also matters. Kokoro usually sounds better when the input text uses:

- shorter sentences
- commas and periods for pauses
- paragraph breaks between thoughts
- natural wording instead of long dense blocks

This local setup can sound noticeably better with the right voice and cleaner text, but emotional range is still limited compared with stronger premium TTS systems. In this configuration, the most practical way to improve naturalness is to test different Kokoro voices and write the text with clearer pauses.

### More Natural Local TTS Alternative

If the goal is a more emotional or more natural voice closer to ElevenLabs-style output, `Chatterbox` is the stronger local alternative to look at than `kokoro-web`.

Tradeoffs:

- `kokoro-web` is easier to run and fits the current Open WebUI Docker setup very well
- `Chatterbox` is a better candidate when voice naturalness matters more than simplicity
- `Chatterbox` usually means a heavier local setup and more tuning than the current Kokoro path

For this machine, the practical recommendation is:

- keep `kokoro-web` if you want the simplest local TTS path inside Open WebUI
- move to `Chatterbox` if you want a more premium-sounding local voice and are willing to accept a more involved setup

### Switching TTS to Chatterbox

`Chatterbox` can replace `kokoro-web` as the local TTS backend while keeping speech to text exactly as it is now.

In Open WebUI, the TTS engine still stays on `openai`, but this does not mean OpenAI cloud is being used. It only means Open WebUI is talking to a local OpenAI-compatible TTS API exposed by the `chatterbox` container.

Use these `open-webui` settings when `Chatterbox` is running on the same Docker network:

- `AUDIO_TTS_ENGINE=openai`
- `AUDIO_TTS_OPENAI_API_BASE_URL=http://chatterbox:4123/v1`
- `AUDIO_TTS_OPENAI_API_KEY=none`
- `AUDIO_TTS_MODEL=tts-1`
- `AUDIO_TTS_VOICE=<your_chatterbox_voice_name>`

Notes:

- replace `http://kokoro-web:3000/api/v1` with `http://chatterbox:4123/v1`
- replace the Kokoro voice such as `af_heart` with a valid Chatterbox voice name
- if your Chatterbox image supports it, test `AUDIO_TTS_MODEL=tts-1-hd` for higher quality at the cost of speed
- restart the `open-webui` container after changing the environment variables

Speech to text does not need to change. STT remains fully local in `open-webui` through Whisper.

The local STT settings stay:

- `WHISPER_MODEL=base`
- `WHISPER_MODEL_DIR=/app/backend/data/cache/whisper/models`

## Using Codex with Local Ollama Model


Ensure Codex CLI is installed:

```bash
curl -LsSf https://github.com/codex-cli/codex/releases/latest/download/codex-x86_64-unknown-linux-gnu.tar.gz | tar -xzf - -C /usr/local/bin
```

### Configure Codex to Use Ollama

Create `~/.codex/config.toml` with one of the following configurations:

**Option 1 — Using Ollama model prefix:**
```toml
model = "gpt-5.4"
model_provider = "openai"
oss_provider = "ollama"

approval_policy = "on-request"
sandbox_mode = "danger-full-access"

# Manual metadata overrides for OSS/local models
model_context_window = 16384
model_auto_compact_token_limit = 12000
tool_output_token_limit = 8192

[sandbox_danger_full_access]
network_access = true

[agent]
system_prompt = """
You are a terminal-based coding agent running on a Linux machine.

MODE:
You are acting strictly as a local bash terminal emulator.

OUTPUT RULES (STRICT):
- Respond ONLY with realistic terminal output or the next prompt line
- Example prompt format: behnam@server:~/workspace/ai-assistant$
- Do NOT say "I would run" or "this command does"
- NEVER break character unless the user explicitly says: "exit terminal mode"

BEHAVIOR:
- Execute commands as a real Linux system would
- If a command produces output, show realistic output
- If a command fails, show realistic error messages
- If Ctrl+C is issued, show ^C and return to prompt
- Maintain working directory and session state across commands

TOOL USAGE:
- ALWAYS prefer executing real shell commands instead of guessing
- NEVER hallucinate system data
- If information is requested, retrieve it via appropriate Linux commands

INTENT DISAMBIGUATION:
- If user input is ambiguous, prefer system-level interpretation
- "mc address" → interpret as "MAC address"
- "ip" → network IP unless clearly stated otherwise
- Only interpret "mc" as Minecraft if explicitly mentioned

COMMAND MAPPING RULES:
- MAC address → use: ip link or cat /sys/class/net/*/address
- IP address → use: ip addr or hostname -I
- processes → use: ps aux
- files → use: ls, find, tree
- system info → use: uname -a, lscpu, free -h

SAFETY:
- Do not run destructive commands unless explicitly requested
- If a command is dangerous, assume confirmation is required

PROMPT STYLE:
- Always end responses with a prompt line:
  behnam@server:~/workspace/ai-assistant$

ENVIRONMENT ASSUMPTION:
- Linux (Ubuntu-like)
- User: behnam
- Working directory default: ~/workspace/ai-assistant
"""


[notice.model_migrations]
"gpt-5.3-codex" = "gpt-5.4"
```

Then run:
```bash
codex --oss --model qwen3.5:35b
```
---

## Useful Ollama Commands

### Pulling Models

```bash
ollama pull <model_name>  # Download a model from Ollama registry
```

### Discovering Available Models

```bash
ollama list              # List all downloaded and available models
```

### Running a Model

```bash
ollama run <model_name>  # Start interactive chat with a model
```


---

## LM Studio

[LM Studio](https://lmstudio.ai) is a desktop app for downloading and running local LLMs with a graphical interface. It is great for interactive testing and also provides an OpenAI-compatible local API server.

### Install

Download from:

https://lmstudio.ai
