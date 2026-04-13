"""Developer agent worker: pops tasks, runs Claude Agent SDK, commits, hands off to code review."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query
from dotenv import load_dotenv

from shared.event_log import make_entry
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
    if task.iteration > 0 and task.review_feedback:
        parts.append(
            "CODE REVIEW FEEDBACK from previous iteration — address every item:\n"
            f"{task.review_feedback}"
        )
    if task.iteration > 0 and task.ui_test_feedback:
        parts.append(
            "UI TEST FEEDBACK from previous iteration — address every item:\n"
            f"{task.ui_test_feedback}"
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
    await store.log_event(task.task_id, make_entry(
        "developer", "dev_started",
        f"Starting iteration {task.iteration} on branch {branch}",
        iteration=task.iteration, branch=branch,
    ))

    git = GitManager(REPO_PATH)
    git.ensure_feature_branch(branch)

    system_prompt = _load_system_prompt()
    user_prompt = _build_user_prompt(task)

    log(logger, "info", "running claude agent", task_id=task.task_id, branch=branch)
    summary = await asyncio.wait_for(
        _run_claude(user_prompt, system_prompt, REPO_PATH),
        timeout=TIMEOUT,
    )

    if summary:
        subject = summary.split("\n")[0][:72]
    else:
        subject = task.prompt[:72]
    commit_msg = f"[{task.task_id}] {subject}"
    commit = git.commit_all(commit_msg)

    if commit is None and summary:
        # Plan-only or analysis task — no file changes but agent produced output.
        # Skip code review and UI test; go straight to awaiting human review.
        task.dev_summary = summary
        task.status = TaskStatus.AWAITING_REVIEW.value
        await store.save(task)
        await store.log_event(task.task_id, make_entry(
            "developer", "plan_only",
            "No code changes — agent produced a plan/analysis. Skipping review gates.",
            summary=summary[:500],
        ))
        await store.publish(Event(
            EventType.DEV_COMPLETE.value, task.task_id,
            {"branch": branch, "summary": summary[-1500:],
             "plan_only": True, "iteration": task.iteration},
        ))
        await store.publish(Event(
            EventType.AWAITING_REVIEW.value, task.task_id,
            {"branch": branch, "plan_only": True},
        ))
        log(logger, "info", "plan-only task, awaiting human review",
            task_id=task.task_id)
        return

    if commit is None:
        raise RuntimeError("developer agent produced no file changes")
    git.push(branch)

    task.commit_hash = commit
    task.dev_summary = summary
    task.status = TaskStatus.DEV_DONE.value
    await store.save(task)

    # Hand off to code reviewer (first gate)
    await store.enqueue_review(task.task_id)
    await store.log_event(task.task_id, make_entry(
        "developer", "code_pushed",
        f"Committed {commit[:10]} and pushed to {branch}",
        commit=commit[:10], branch=branch, summary=summary[:500],
    ))
    await store.publish(Event(
        EventType.DEV_COMPLETE.value,
        task.task_id,
        {
            "branch": branch,
            "commit": commit[:10],
            "summary": summary[-1500:],
            "iteration": task.iteration,
        },
    ))
    log(logger, "info", "dev complete, queued for code review",
        task_id=task.task_id, commit=commit[:10])


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
                await store.log_event(task_id, make_entry(
                    "developer", "dev_error",
                    f"Developer agent failed: {str(exc)[:200]}",
                ))
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
