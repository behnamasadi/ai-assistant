"""Code reviewer agent: pops review tasks, reads diffs, produces PASSED/FEEDBACK verdict."""
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

logger = get_logger("code_reviewer")

REPO_PATH = os.environ.get("GIT_REPO_PATH", "/workspace/project")
PROMPT_PATH = Path(__file__).parent / "prompts" / "reviewer.md"
TIMEOUT = int(os.environ.get("REVIEW_AGENT_TIMEOUT_SECONDS", "900"))
MAX_ITER = int(os.environ.get("MAX_FEEDBACK_ITERATIONS", "3"))


def _load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _build_review_prompt(task: Task) -> str:
    base = os.environ.get("GIT_DEFAULT_BRANCH", "main")
    return (
        f"TASK ID: {task.task_id}\n"
        f"ORIGINAL REQUEST:\n{task.prompt}\n\n"
        f"DEVELOPER SUMMARY:\n{task.dev_summary or '(none)'}\n\n"
        f"Review the diff: run `git diff {base}...HEAD` in {REPO_PATH}\n"
        f"Branch: {task.branch}\n"
        f"Iteration: {task.iteration}\n\n"
        "Produce your PASSED, FEEDBACK, or BLOCKED verdict now."
    )


async def _run_claude_review(user_prompt: str, system_prompt: str) -> str:
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=["Read", "Glob", "Grep", "Bash"],
        cwd=REPO_PATH,
        permission_mode="default",
    )
    parts: list[str] = []
    async for message in query(prompt=user_prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
    return "\n".join(parts).strip()


def _parse_verdict(text: str) -> tuple[str, str]:
    """Return (verdict, body) where verdict is PASSED, FEEDBACK, or BLOCKED."""
    upper = text.strip().upper()
    for keyword in ("PASSED", "FEEDBACK", "BLOCKED"):
        # Check both "STATUS: PASSED" and bare "PASSED" at start
        if upper.startswith(f"STATUS: {keyword}") or upper.startswith(keyword):
            return keyword, text.strip()
    # Also search for STATUS: line anywhere in the text
    for line in text.splitlines():
        stripped = line.strip().upper()
        for keyword in ("PASSED", "FEEDBACK", "BLOCKED"):
            if stripped.startswith(f"STATUS: {keyword}"):
                return keyword, text.strip()
    # Ambiguous — treat as feedback to be safe
    return "FEEDBACK", text.strip()


async def _process_task(store: TaskStore, task: Task) -> None:
    task.status = TaskStatus.REVIEW_IN_PROGRESS.value
    await store.save(task)
    await store.publish(Event(EventType.REVIEW_STARTED.value, task.task_id,
                              {"iteration": task.iteration}))

    git = GitManager(REPO_PATH)
    git.ensure_feature_branch(task.branch or f"feature/{task.task_id}")

    system_prompt = _load_system_prompt()
    user_prompt = _build_review_prompt(task)

    log(logger, "info", "running code review", task_id=task.task_id)
    review = await asyncio.wait_for(
        _run_claude_review(user_prompt, system_prompt),
        timeout=TIMEOUT,
    )
    verdict, body = _parse_verdict(review)

    if verdict == "PASSED":
        task.status = TaskStatus.REVIEW_DONE.value
        task.review_feedback = body
        await store.save(task)

        # Hand off to UI tester (second gate)
        await store.enqueue_ui_test(task.task_id)
        await store.publish(Event(EventType.REVIEW_PASSED.value, task.task_id,
                                  {"summary": body[:1500]}))
        log(logger, "info", "code review passed, queued for UI testing",
            task_id=task.task_id)
        return

    # FEEDBACK or BLOCKED path — send back to developer
    if task.iteration >= MAX_ITER:
        task.status = TaskStatus.NEEDS_MANUAL_REVIEW.value
        task.review_feedback = body
        await store.save(task)
        await store.publish(Event(EventType.MANUAL_REVIEW.value, task.task_id,
                                  {"feedback": body[:1500]}))
        log(logger, "warning", "max iterations reached at code review",
            task_id=task.task_id)
        return

    task.review_feedback = body
    task.status = TaskStatus.DEV_DONE.value  # back to dev
    await store.save(task)
    await store.publish(Event(
        EventType.REVIEW_FEEDBACK.value,
        task.task_id,
        {"feedback": body[:1500], "iteration": task.iteration},
    ))
    await store.enqueue_dev(task.task_id)
    log(logger, "info", "code review feedback sent to developer",
        task_id=task.task_id)


async def _worker_loop() -> None:
    store = TaskStore()
    log(logger, "info", "code reviewer agent started", repo=REPO_PATH)
    try:
        while True:
            task_id = await store.pop_review(timeout=0)
            if not task_id:
                continue
            task = await store.get(task_id)
            if not task:
                log(logger, "error", "task not found", task_id=task_id)
                continue
            try:
                await _process_task(store, task)
            except Exception as exc:
                log(logger, "error", "code review failed",
                    task_id=task_id, error=str(exc))
                task.status = TaskStatus.FAILED.value
                task.error = str(exc)
                await store.save(task)
                await store.publish(Event(
                    EventType.REVIEW_ERROR.value,
                    task_id,
                    {"error": str(exc)[:1500]},
                ))
    finally:
        await store.close()


def main() -> None:
    asyncio.run(_worker_loop())


if __name__ == "__main__":
    main()
