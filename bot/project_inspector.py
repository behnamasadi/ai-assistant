"""Read-only project inspection for the Telegram bot's conversation mode.

This lets the bot actually look at the target repo (GIT_REPO_PATH) and answer
real questions about it — "what does the auth middleware do?", "rate this file",
"is X handled?" — by reading the real files, NOT from generic model knowledge.

Hard read-only guarantee (the whole point — the bot must never change code unless
the user explicitly asks via /build):
  - Only Read / Glob / Grep / Bash are offered; Write / Edit / NotebookEdit are
    in `disallowed_tools`.
  - A `can_use_tool` permission gate rejects any Bash command that isn't a known
    read-only command (git log/status/diff/show…, ls, cat, grep, find, …) and
    rejects redirections and command substitution outright.

If the Claude Agent SDK isn't installed or the repo path is missing, callers
should fall back to a generic (repo-blind) answer.
"""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from shared.logger import get_logger, log

logger = get_logger("project_inspector")

REPO_PATH = os.environ.get("GIT_REPO_PATH", "/workspace/project")
INSPECT_TIMEOUT = int(os.environ.get("INSPECT_TIMEOUT_SECONDS", "180"))

# Tools the inspector may use. Anything that mutates the repo is deliberately absent.
_ALLOWED_TOOLS = ["Read", "Glob", "Grep", "Bash"]
_DISALLOWED_TOOLS = ["Write", "Edit", "MultiEdit", "NotebookEdit"]

# Bash commands considered read-only. Anything not on these lists is denied.
_READONLY_BINARIES = {
    "ls", "cat", "head", "tail", "wc", "grep", "rg", "egrep", "fgrep",
    "find", "tree", "file", "stat", "sort", "uniq", "cut", "nl", "column",
    "diff", "echo", "pwd", "basename", "dirname", "realpath", "du", "awk",
    "jq", "yq", "which", "env", "date", "whoami", "cloc", "true", "test",
}
_GIT_READONLY_SUBCMDS = {
    "log", "status", "diff", "show", "branch", "ls-files", "ls-tree", "blame",
    "rev-parse", "describe", "remote", "shortlog", "tag", "cat-file", "grep",
    "config", "reflog", "whatchanged", "name-rev", "for-each-ref", "show-ref",
    "rev-list", "merge-base", "count-objects",
}

_SYSTEM_PROMPT = (
    "You are a read-only software engineering assistant embedded in a Telegram bot. "
    "Your working directory is a real code repository. You can READ and ANALYSE it — "
    "read files, search, inspect git history — to answer the user's question, review "
    "or rate code, explain how something works, or check whether something is handled.\n\n"
    "STRICT RULES:\n"
    "- You are READ-ONLY. Never create, edit, move, or delete files. Never commit, "
    "push, checkout, reset, or otherwise change git state. If the user wants a change "
    "made, tell them to send it with the /build command — do not attempt it yourself.\n"
    "- Ground every claim in the actual files. Cite paths and line numbers (path:line) "
    "so the user can click through. If you're unsure, say so rather than guessing.\n"
    "- Be concise and use Telegram-friendly Markdown. Answer the question directly."
)


def is_available() -> bool:
    """True if the SDK is importable and the repo path exists."""
    try:
        import claude_agent_sdk  # noqa: F401
    except Exception:
        return False
    return Path(REPO_PATH).is_dir()


def _is_readonly_bash(command: str) -> bool:
    """Return True only if every segment of a Bash command is read-only."""
    # Block output redirection and command substitution outright — these can hide
    # a write even behind an otherwise-innocent binary.
    if ">" in command or "$(" in command or "`" in command:
        return False

    # Validate each piece of a pipeline / sequence independently.
    segments = re.split(r"\|\||&&|;|\|", command)
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        try:
            tokens = shlex.split(seg)
        except ValueError:
            return False
        # Skip leading VAR=value environment assignments.
        idx = 0
        while idx < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[idx]):
            idx += 1
        if idx >= len(tokens):
            continue
        binary = os.path.basename(tokens[idx])
        rest = tokens[idx + 1:]

        if binary == "git":
            sub = next((t for t in rest if not t.startswith("-")), None)
            if sub not in _GIT_READONLY_SUBCMDS:
                return False
            continue
        if binary == "sed":  # allow stream edits, block in-place writes
            if any(t.startswith("-i") or t == "--in-place" for t in rest):
                return False
            continue
        if binary == "find":  # block side-effecting actions
            if any(t in ("-delete", "-exec", "-execdir", "-ok", "-okdir",
                         "-fprint", "-fprintf", "-fls") for t in rest):
                return False
            continue
        if binary not in _READONLY_BINARIES:
            return False
    return True


async def _readonly_gate(tool_name, tool_input, context):
    """can_use_tool callback: allow read-only tools, gate Bash, deny the rest."""
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    if tool_name in ("Read", "Glob", "Grep"):
        return PermissionResultAllow()
    if tool_name == "Bash":
        command = (tool_input or {}).get("command", "")
        if _is_readonly_bash(command):
            return PermissionResultAllow()
        log(logger, "warning", "blocked non-read-only bash in inspector",
            command=command[:200])
        return PermissionResultDeny(
            message="Read-only mode: that command could modify the repo and is "
                    "not allowed. Use read-only commands (git log/diff/show, ls, "
                    "cat, grep, find) or the Read/Grep/Glob tools.",
        )
    return PermissionResultDeny(
        message=f"Read-only mode: the {tool_name} tool is disabled. You may only "
                "read and analyse this repository, never change it.",
    )


async def inspect(question: str) -> str:
    """Answer a question by reading the real repo. Read-only, never mutates."""
    import asyncio

    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        TextBlock,
        query,
    )

    options = ClaudeAgentOptions(
        system_prompt=_SYSTEM_PROMPT,
        allowed_tools=_ALLOWED_TOOLS,
        disallowed_tools=_DISALLOWED_TOOLS,
        cwd=REPO_PATH,
        permission_mode="default",
        can_use_tool=_readonly_gate,
    )

    # The SDK requires streaming mode (an async iterable of messages) whenever a
    # can_use_tool callback is set — a plain string prompt is rejected.
    async def _prompt_stream():
        yield {
            "type": "user",
            "message": {"role": "user", "content": question},
            "parent_tool_use_id": None,
            "session_id": "inspector",
        }

    async def _run() -> str:
        parts: list[str] = []
        async for message in query(prompt=_prompt_stream(), options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
        return "\n".join(parts).strip()

    log(logger, "info", "inspecting repo", repo=REPO_PATH, preview=question[:80])
    answer = await asyncio.wait_for(_run(), timeout=INSPECT_TIMEOUT)
    return answer or "I looked but couldn't find anything to say about that."
