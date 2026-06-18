"""Full-access Claude Code agent over Telegram — your assistant on your phone.

Where ``project_inspector`` is the hard *read-only* path (answer questions, never
touch code), this module is the opposite: a **full-access Claude Code session**
that can read, write, run commands, and spawn sub-agents against ``GIT_REPO_PATH``
— the same kind of agentic discussion you have in the Claude Code CLI, driven from
a Telegram chat.

Safety model: the bot is already locked to a single Telegram user
(``TELEGRAM_ALLOWED_USER_ID``), so only the owner can ever reach this. That single
gate is what makes it acceptable to expose Write/Edit/Bash here. Agent mode is
**off by default** and turned on per chat with ``/agent on`` — see ``bot/main.py``.

Conversation continuity: each Telegram chat keeps its own Claude session id, so
follow-up messages continue the same conversation (the agent remembers context)
instead of starting cold every time. ``reset()`` drops the session for a fresh start.

If the Claude Agent SDK isn't installed or the repo path is missing, ``is_available``
returns False and callers should fall back to the read-only conversation path.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Awaitable, Callable

from shared.logger import get_logger, log

logger = get_logger("agent_session")

REPO_PATH = os.environ.get("GIT_REPO_PATH", "/workspace/project")
# Real agentic work (read, edit, run tests, sub-agents) takes far longer than a
# read-only inspection, so this timeout is generous compared to INSPECT_TIMEOUT.
AGENT_TURN_TIMEOUT = int(os.environ.get("AGENT_TURN_TIMEOUT_SECONDS", "900"))

# chat_id -> Claude session id, so each chat is one continuous conversation.
_SESSIONS: dict[int, str] = {}

_SYSTEM_PROMPT = (
    "You are a full-access software engineering assistant embedded in a Telegram "
    "bot, acting as the user's remote Claude Code on their machine. Your working "
    "directory is a real code repository and you may read, write, edit, run shell "
    "commands, and delegate to sub-agents to get the job done — exactly like the "
    "Claude Code CLI.\n\n"
    "GUIDELINES:\n"
    "- This is a continuous conversation: remember what was said earlier in the chat.\n"
    "- Default to acting. When the user asks for a change, make it. Don't ask for "
    "permission to proceed on reversible local work.\n"
    "- For anything genuinely destructive or irreversible (rm -rf of real data, "
    "force-push, dropping a database), confirm first in your reply before doing it.\n"
    "- Keep replies concise and Telegram-friendly: short Markdown, cite paths as "
    "path:line, no giant code dumps unless asked.\n"
    "- The user is often eyes-free (driving, away from the screen), so lead with the "
    "outcome in the first sentence."
)


def is_available() -> bool:
    """True if the SDK is importable and the repo path exists."""
    try:
        import claude_agent_sdk  # noqa: F401
    except Exception:
        return False
    return Path(REPO_PATH).is_dir()


def has_session(chat_id: int) -> bool:
    """True if this chat already has a live Claude conversation."""
    return chat_id in _SESSIONS


def reset(chat_id: int) -> bool:
    """Forget this chat's conversation so the next turn starts fresh.

    Returns True if there was a session to drop."""
    return _SESSIONS.pop(chat_id, None) is not None


def _short(value, limit: int = 120) -> str:
    """Compact a tool input for a progress line."""
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _describe_tool(name: str, tool_input: dict) -> str:
    """A one-line, human-friendly 'what the agent is doing' progress string."""
    tool_input = tool_input or {}
    if name == "Bash":
        return f"🖥 `{_short(tool_input.get('command', ''))}`"
    if name in ("Read", "Glob", "Grep"):
        target = tool_input.get("file_path") or tool_input.get("pattern") or ""
        return f"🔍 {name} {_short(target, 80)}"
    if name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return f"✏️ {name} {_short(tool_input.get('file_path', ''), 80)}"
    if name in ("Task", "Agent"):
        sub = tool_input.get("subagent_type") or tool_input.get("description") or ""
        return f"🤖 sub-agent {_short(sub, 80)}"
    return f"⚙️ {name}"


async def run_turn(
    chat_id: int,
    prompt: str,
    on_progress: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Run one agentic turn for a chat, continuing its conversation if it has one.

    ``on_progress`` (optional, async) is called with a short status line each time
    the agent invokes a tool, so the bot can stream "what it's doing" to Telegram.
    Returns the agent's final text reply.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
        query,
    )

    resume_id = _SESSIONS.get(chat_id)
    options = ClaudeAgentOptions(
        system_prompt=_SYSTEM_PROMPT,
        cwd=REPO_PATH,
        # Telegram can't render interactive permission prompts, so we run
        # non-interactively. Safe because access is locked to one Telegram user.
        permission_mode="bypassPermissions",
        # Full toolset (Read/Write/Edit/Bash/Task/...) is available by default; we
        # deliberately don't restrict allowed_tools so sub-agents work too.
        resume=resume_id,
    )

    async def _run() -> str:
        parts: list[str] = []
        result_text: str | None = None
        async for message in query(prompt=prompt, options=options):
            # Capture/refresh the session id from any message that carries one so
            # the next turn resumes this exact conversation.
            sid = getattr(message, "session_id", None)
            if sid:
                _SESSIONS[chat_id] = sid

            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
                    elif isinstance(block, ToolUseBlock) and on_progress is not None:
                        try:
                            await on_progress(_describe_tool(block.name, block.input))
                        except Exception:  # progress is best-effort, never fatal
                            pass
            elif isinstance(message, ResultMessage):
                if getattr(message, "result", None):
                    result_text = message.result

        return (result_text or "\n".join(parts)).strip()

    log(logger, "info", "agent turn", chat_id=chat_id, resumed=bool(resume_id),
        preview=prompt[:80])
    answer = await asyncio.wait_for(_run(), timeout=AGENT_TURN_TIMEOUT)
    return answer or "✅ Done (no text to show)."
