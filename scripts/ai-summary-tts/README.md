# AI summary → speech → lights (engine-agnostic)

Takes the **final reply** of an AI assistant turn, compresses it to **one spoken
sentence**, speaks it with a neural voice, and — when the OpenRGB `rgbfan talking`
mode is active — pulses the case fans with the speech loudness.

This is the version-controlled, engine-agnostic implementation of the summarizer
described in [`docs/openrgb/openrgb-setup-notes.md`](../../docs/openrgb/openrgb-setup-notes.md)
(see *"The AI summary → speech → lights pipeline"*).

## The one idea

Only **one** step is engine-specific — the *summarize* call. Everything else
(extracting the final reply, speaking it, driving the lights) is shared. So you can
point it at any AI assistant by changing a single env var.

```
final AI reply ──▶ summarize (engine) ──▶ neural TTS ──▶ MP3 ──▶ ffplay
                                                          └─▶ loudness envelope ──▶ fans
                   └── claude | openai | ollama ──┘        └────── engine-independent ──────┘
```

## Usage

```bash
# Test / scripting: print the one-line summary, no audio.
echo "long assistant reply…" | ./summarize-speak.sh --print

# As a Claude Code Stop hook (default): receives {"transcript_path": …} on stdin,
# speaks the summary, returns immediately. This is what ~/.claude/hooks/
# tts-summarize.sh delegates to.
```

Input on stdin is either a Claude Stop-hook JSON object (with `.transcript_path`)
or **raw text** = the assistant's final reply (generic mode — how any other engine
feeds it).

## Choosing the engine

Default is `claude` (byte-for-byte the original Claude-only hook). Override per-run
or in `~/.config/ai-summary-tts.conf`:

```bash
AI_ENGINE=claude    # default: claude -p --model haiku
AI_ENGINE=openai    # OpenAI chat-completions via curl; needs OPENAI_API_KEY
AI_ENGINE=ollama    # ollama run $OLLAMA_SUMMARY_MODEL (default llama3.2)
```

Other knobs (all optional; CLAUDE_TTS_* names kept for back-compat):

| Var | Default | Meaning |
|-----|---------|---------|
| `SUMMARY_MAX_WORDS` | `20` | max words in the spoken sentence |
| `CLAUDE_SUMMARY_MODEL` | `haiku` | model for the `claude` engine |
| `OPENAI_SUMMARY_MODEL` | `gpt-4o-mini` | model for the `openai` engine |
| `OLLAMA_SUMMARY_MODEL` | `llama3.2` | model for the `ollama` engine |
| `CLAUDE_TTS_VOICE` | `en-US-AvaNeural` | edge-tts voice |
| `CLAUDE_TTS_RATE` | `+10%` | speech rate |
| `OPENAI_API_KEY` | — | required only for `AI_ENGINE=openai` |

Disable all speech: `touch ~/.claude/tts-disabled`.

## How the live Claude hook is wired

`~/.claude/hooks/tts-summarize.sh` is a thin shim that `exec`s this script. The
original Claude-only hook is backed up next to it.

```bash
# Revert to the original Claude-only hook at any time:
cp ~/.claude/hooks/tts-summarize.sh.bak-2026-06-14 ~/.claude/hooks/tts-summarize.sh
```

## Adapting to a non-Claude assistant

The `claude` engine only differs in **how the final text is obtained** and **which
model summarizes**:

- **Trigger** — Claude fires this via its `Stop` hook. Another assistant would call
  the script when its turn ends (its own hook, a wrapper around its CLI, or a
  log/`tmux` capture).
- **Final text** — pipe that assistant's final reply (or its console's last block)
  into the script on stdin. No `transcript_path` → the script treats stdin as the
  reply text directly.
- **Summarize** — set `AI_ENGINE` to `openai`/`ollama`, or add a new branch in the
  `summarize()` function for any other API.

Speech (edge-tts) and the light envelope (`rgbfan-envelope` + `rgbfan-daemon`) are
unchanged across engines.
