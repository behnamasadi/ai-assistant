#!/usr/bin/env bash
# Engine-agnostic "AI summary → speech → lights" pipeline.
#
# Takes the FINAL reply of an AI assistant turn, compresses it to ONE spoken
# sentence, speaks it with a neural voice, and (when rgbfan `talking` mode is on)
# feeds the speech loudness envelope to the fan daemon so the lights pulse.
#
# Only the SUMMARIZE step is engine-specific. Default engine is `claude`, which is
# byte-for-byte the original Claude Stop hook behaviour — so wiring this in cannot
# change anything until you explicitly pick another engine. The speech (edge-tts)
# and the light envelope are 100% engine-independent.
#
#   AI_ENGINE=claude   (default)  : claude -p --model haiku
#   AI_ENGINE=openai              : OpenAI chat-completions via curl ($OPENAI_API_KEY)
#   AI_ENGINE=ollama              : ollama run $OLLAMA_MODEL
#
# Two modes:
#   (default)        full hook behaviour — single-instance lock, dedupe, speak,
#                    lights; backgrounds itself and returns immediately.
#   --print          read text/JSON on stdin, print the one-line summary, exit.
#                    Foreground, no lock/dedupe/speech. For testing & scripting.
#
# Input on stdin is either:
#   - a Claude Stop-hook JSON object with a `.transcript_path` (Claude mode), or
#   - raw text = the assistant's final reply (generic mode, any engine).
#
# Config file (optional, sourced): ~/.config/ai-summary-tts.conf
# Back-compat: ~/.claude/tts.conf and the CLAUDE_TTS_* env vars are still honoured.
# Disable switch: touch ~/.claude/tts-disabled

set -u

PRINT_ONLY=0
[ "${1:-}" = "--print" ] && PRINT_ONLY=1

[ -f "$HOME/.claude/tts-disabled" ] && exit 0
# shellcheck disable=SC1091
[ -f "$HOME/.claude/tts.conf" ]             && . "$HOME/.claude/tts.conf"
# shellcheck disable=SC1091
[ -f "$HOME/.config/ai-summary-tts.conf" ]  && . "$HOME/.config/ai-summary-tts.conf"

# ── Config (defaults preserve the original Claude hook) ──────────────────────
AI_ENGINE="${AI_ENGINE:-claude}"
MAXWORDS="${SUMMARY_MAX_WORDS:-${CLAUDE_TTS_MAX_WORDS:-20}}"
CLAUDE_BIN="${CLAUDE_BIN:-/home/behnam/.local/bin/claude}"
CLAUDE_MODEL="${CLAUDE_SUMMARY_MODEL:-haiku}"
OPENAI_MODEL="${OPENAI_SUMMARY_MODEL:-gpt-4o-mini}"
OLLAMA_MODEL="${OLLAMA_SUMMARY_MODEL:-llama3.2}"
EDGE="${EDGE_TTS_BIN:-/home/behnam/.local/bin/edge-tts}"
VOICE="${CLAUDE_TTS_VOICE:-en-US-AvaNeural}"
RATE="${CLAUDE_TTS_RATE:-+10%}"
PITCH="${CLAUDE_TTS_PITCH:-}"
ENVELOPE_PY="${RGBFAN_ENVELOPE_BIN:-/usr/local/bin/rgbfan-envelope}"
ENVELOPE_PYTHON="${RGBFAN_PYTHON:-/home/behnam/anaconda3/bin/python3}"

PROMPT="Compress the assistant message below into ONE spoken sentence of at most ${MAXWORDS} words. State only the single most important outcome, or the one question being asked of the user. Plain prose, no markdown, no code, no file paths, no symbols, no lists. Output the sentence only — never ask for input, never say you have no message."

# ── (2) EXTRACT: final reply text from stdin (Claude transcript or raw text) ──
extract_final_text() {
  local input="$1" tpath=""
  tpath=$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null)
  if [ -n "$tpath" ] && [ -f "$tpath" ]; then
    # Claude mode: ALL assistant prose from the current turn (back to the last
    # real user message). Concatenating the turn's narration + closing summary
    # makes the spoken line describe *what happened*, and — crucially — it never
    # comes up empty just because the turn ended on a tool call with no trailing
    # text (the old "very-last assistant entry only" logic went silent then).
    python3 - "$tpath" <<'PY'
import json, sys
path = sys.argv[1]
entries = []
try:
    with open(path) as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
except Exception:
    entries = []

def text_of(obj):
    content = (obj.get("message") or {}).get("content", [])
    if isinstance(content, str):
        return content.strip()
    parts = [c.get("text", "") for c in (content or [])
             if isinstance(c, dict) and c.get("type") == "text"]
    return "\n".join(p for p in parts if p).strip()

def is_real_user(obj):
    # A genuine user turn (not a tool_result, which is also role=user).
    if obj.get("type") != "user":
        return False
    content = (obj.get("message") or {}).get("content", [])
    if isinstance(content, str):
        return bool(content.strip())
    has_text = any(isinstance(c, dict) and c.get("type") == "text"
                   for c in (content or []))
    has_tool = any(isinstance(c, dict) and c.get("type") == "tool_result"
                   for c in (content or []))
    return has_text and not has_tool

# Walk back from the end, gathering assistant prose until the last real user msg.
chunks = []
for obj in reversed(entries):
    if is_real_user(obj):
        break
    if obj.get("type") == "assistant":
        t = text_of(obj)
        if t:
            chunks.append(t)
chunks.reverse()
text = "\n".join(chunks).strip()
# Fallback: the most recent assistant entry that has any text at all.
if not text:
    for obj in reversed(entries):
        if obj.get("type") == "assistant":
            t = text_of(obj)
            if t:
                text = t
                break
print(text)
PY
  else
    # Generic mode: stdin IS the final reply text (any AI engine pipes it in).
    printf '%s' "$input"
  fi
}

# ── (3) SUMMARIZE: the only engine-specific step ─────────────────────────────
summarize() {
  local text="$1"
  case "$AI_ENGINE" in
    claude)
      printf '%s' "$text" | timeout 30 "$CLAUDE_BIN" -p --model "$CLAUDE_MODEL" "$PROMPT" 2>/dev/null
      ;;
    openai)
      local key="${OPENAI_API_KEY:-}"
      [ -z "$key" ] && return 1
      local payload
      payload=$(jq -n --arg m "$OPENAI_MODEL" --arg sys "$PROMPT" --arg u "$text" \
        '{model:$m, temperature:0, max_tokens:60,
          messages:[{role:"system",content:$sys},{role:"user",content:$u}]}')
      curl -s --max-time 30 https://api.openai.com/v1/chat/completions \
        -H "Authorization: Bearer $key" -H "Content-Type: application/json" \
        -d "$payload" 2>/dev/null | jq -r '.choices[0].message.content // empty'
      ;;
    ollama)
      command -v ollama >/dev/null 2>&1 || return 1
      printf '%s\n\n%s' "$PROMPT" "$text" | timeout 60 ollama run "$OLLAMA_MODEL" 2>/dev/null
      ;;
    *)
      return 1
      ;;
  esac
}

clean_summary() {
  # Strip wrapping quotes/markdown and cap length.
  sed -e 's/^[[:space:]"`'\''*_-]*//' -e 's/[[:space:]"`'\''*]*$//' | head -c 300
}

is_non_answer() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    *"no assistant message"*|*"no message"*|*"please paste"*|*"please provide"*|\
    *"i need the message"*|*"i don't see"*|"") return 0 ;;
  esac
  return 1
}

INPUT=$(cat)

# ── --print mode: extract + summarize + print, then exit (test / scripting) ──
if [ "$PRINT_ONLY" = 1 ]; then
  TEXT=$(extract_final_text "$INPUT")
  [ -z "$TEXT" ] && { echo "(no final text found)" >&2; exit 1; }
  TEXT=$(printf '%s' "$TEXT" | head -c 16000)
  SUM=$(summarize "$TEXT" | clean_summary)
  if [ -z "$SUM" ] || is_non_answer "$SUM"; then
    echo "(no summary produced by engine '$AI_ENGINE')" >&2
    exit 1
  fi
  printf '%s\n' "$SUM"
  exit 0
fi

# ── default mode: full hook behaviour, backgrounded so the caller returns ────
(
  pkill -9 -f edge-tts             2>/dev/null
  pkill -9 -f 'ffplay.*tts-claude' 2>/dev/null
  rm -f /tmp/tts-claude-*.mp3      2>/dev/null

  exec 9>/tmp/tts-claude.lock
  flock -n 9 || exit 0

  TEXT=$(extract_final_text "$INPUT")
  [ -z "$TEXT" ] && exit 0

  HASH=$(printf '%s' "$TEXT" | sha1sum | cut -d' ' -f1)
  HASH_FILE="/tmp/tts-claude.last-hash"
  if [ -f "$HASH_FILE" ] && [ "$(cat "$HASH_FILE" 2>/dev/null)" = "$HASH" ]; then
    exit 0
  fi
  printf '%s' "$HASH" > "$HASH_FILE"

  TEXT=$(printf '%s' "$TEXT" | head -c 16000)
  SUMMARY=$(summarize "$TEXT" | clean_summary)
  is_non_answer "$SUMMARY" && exit 0

  [ -x "$EDGE" ] || exit 0
  command -v ffplay >/dev/null 2>&1 || exit 0

  MP3="/tmp/tts-claude-$$.mp3"
  EDGE_ARGS=(--voice "$VOICE" --rate "$RATE")
  [ -n "$PITCH" ] && EDGE_ARGS+=(--pitch "$PITCH")
  EDGE_ARGS+=(--text "$SUMMARY" --write-media "$MP3")
  "$EDGE" "${EDGE_ARGS[@]}" >/dev/null 2>&1

  if [ -s "$MP3" ]; then
    # (5) LIGHTS — engine-independent: pulse fans with the spoken summary.
    RGBRUN=/run/rgbfan
    if [ -d "$RGBRUN" ] && [ "$(cat "$RGBRUN/mode" 2>/dev/null)" = talking ]; then
      ffmpeg -v quiet -i "$MP3" -ac 1 -ar 8000 -f s16le - 2>/dev/null \
        | "$ENVELOPE_PYTHON" "$ENVELOPE_PY" > "$RGBRUN/env.json" 2>/dev/null
      date +%s.%N > "$RGBRUN/talk" 2>/dev/null
    fi
    ffplay -nodisp -autoexit -loglevel quiet "$MP3" >/dev/null 2>&1
    : > "$RGBRUN/talk" 2>/dev/null
  fi
  rm -f "$MP3"
) </dev/null >/dev/null 2>&1 &
disown

exit 0
