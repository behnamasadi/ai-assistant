"""Developer agent worker: pops tasks, runs Claude Agent SDK, commits, hands off to QA."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query
from dotenv import load_dotenv

from shared.git_manager import GitManager
from shared.logger import get_logger, log
from shared.redis_client import TaskStore
from shared.task_schema import Event, EventType, Task, TaskStatus

load_dotenv()

logger = get_logger("developer_agent")

REPO_PATH = os.environ.get("GIT_REPO_PATH", "/workspace/project")
PROMPT_PATH = Path(__file__).parent / "prompts" / "developer.md"
TIMEOUT = int(os.environ.get("DEV_AGENT_TIMEOUT_SECONDS", "1800"))


def _load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _build_user_prompt(task: Task) -> str:
    parts = [f"TASK ID: {task.task_id}", f"REQUEST:\n{task.prompt}"]
    if task.iteration > 0 and task.qa_feedback:
        parts.append(
            "QA FEEDBACK from previous iteration — address every item:\n"
            f"{task.qa_feedback}"
        )
    return "\n\n".join(parts)


async def _run_claude(user_prompt: str, system_prompt: str, cwd: str) -> str:
    """Run Claude Agent SDK in headless mode and return the assistant's final text."""
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep",
                        "mcp__playwright__browser_navigate",
                        "mcp__playwright__browser_screenshot",
                        "mcp__playwright__browser_click",
                        "mcp__playwright__browser_type",
                        "mcp__playwright__browser_snapshot"],
        cwd=cwd,
        permission_mode="acceptEdits",
        mcp_servers={
            "playwright": {
                "type": "stdio",
                "command": "npx",
                "args": ["@playwright/mcp", "--headless"],
            },
        },
    )
    final_text_parts: list[str] = []
    async for message in query(prompt=user_prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    final_text_parts.append(block.text)
    return "\n".join(final_text_parts).strip()


async def _process_task(store: TaskStore, task: Task) -> None:
    task.status = TaskStatus.DEV_IN_PROGRESS.value
    task.iteration += 1
    branch = task.branch or f"feature/{task.task_id}"
    task.branch = branch
    await store.save(task)
    await store.publish(Event(EventType.DEV_STARTED.value, task.task_id,
                              {"branch": branch, "iteration": task.iteration}))

    git = GitManager(REPO_PATH)
    git.ensure_feature_branch(branch)

    system_prompt = _load_system_prompt()
    user_prompt = _build_user_prompt(task)

    log(logger, "info", "running claude agent", task_id=task.task_id, branch=branch)
    summary = await asyncio.wait_for(
        _run_claude(user_prompt, system_prompt, REPO_PATH),
        timeout=TIMEOUT,
    )

    # Use the agent's summary as commit message (cleaner than raw voice transcript).
    # Fall back to a truncated prompt if summary is empty.
    if summary:
        # First line of summary as commit subject.
        subject = summary.split("\n")[0][:72]
    else:
        subject = task.prompt[:72]
    commit_msg = f"[{task.task_id}] {subject}"
    commit = git.commit_all(commit_msg)
    if commit is None:
        # No changes produced — treat as an error so the user is notified.
        raise RuntimeError("developer agent produced no file changes")
    git.push(branch)

    task.commit_hash = commit
    task.dev_summary = summary
    task.status = TaskStatus.DEV_DONE.value
    await store.save(task)

    await store.enqueue_qa(task.task_id)
    await store.publish(Event(
        EventType.DEV_COMPLETE.value,
        task.task_id,
        {
            "branch": branch,
            "commit": commit[:10],
            "summary": summary[-1500:],  # avoid giant Telegram messages
            "iteration": task.iteration,
        },
    ))
    log(logger, "info", "dev complete", task_id=task.task_id, commit=commit[:10])


async def _worker_loop() -> None:
    store = TaskStore()
    log(logger, "info", "developer agent started", repo=REPO_PATH)
    try:
        while True:
            task_id = await store.pop_dev(timeout=0)
            if not task_id:
                continue
            task = await store.get(task_id)
            if not task:
                log(logger, "error", "task not found", task_id=task_id)
                continue
            try:
                await _process_task(store, task)
            except Exception as exc:
                log(logger, "error", "dev task failed",
                    task_id=task_id, error=str(exc))
                task.status = TaskStatus.FAILED.value
                task.error = str(exc)
                await store.save(task)
                await store.publish(Event(
                    EventType.DEV_ERROR.value,
                    task_id,
                    {"error": str(exc)[:1500]},
                ))
    finally:
        await store.close()


def main() -> None:
    asyncio.run(_worker_loop())


if __name__ == "__main__":
    main()
