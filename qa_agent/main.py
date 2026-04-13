"""QA agent worker: pops DEV_DONE tasks, runs browser tests, reviews, decides."""
from __future__ import annotations

import asyncio
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path

import aiohttp
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query
from dotenv import load_dotenv

from qa_agent.browser_tester import BrowserReport, run_browser_smoke
from shared.git_manager import GitManager
from shared.logger import get_logger, log
from shared.redis_client import TaskStore
from shared.task_schema import Event, EventType, Task, TaskStatus

load_dotenv()

logger = get_logger("qa_agent")

REPO_PATH = os.environ.get("GIT_REPO_PATH", "/workspace/project")
WEB_APP_URL = os.environ.get("WEB_APP_URL", "http://web-app:3000")
WEB_APP_HEALTH = os.environ.get("WEB_APP_HEALTHCHECK_PATH", "/")
WEB_APP_CMD = os.environ.get("WEB_APP_START_COMMAND", "")
MAX_ITER = int(os.environ.get("MAX_FEEDBACK_ITERATIONS", "3"))
TIMEOUT = int(os.environ.get("QA_AGENT_TIMEOUT_SECONDS", "1200"))

PROMPT_PATH = Path(__file__).parent / "prompts" / "qa_reviewer.md"
ARTIFACTS_ROOT = Path(os.environ.get("QA_ARTIFACTS_DIR", "/tmp/qa_artifacts"))


async def _wait_for_health(url: str, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    async with aiohttp.ClientSession() as session:
        while time.monotonic() < deadline:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status < 500:
                        return True
            except Exception:
                pass
            await asyncio.sleep(1)
    return False


class WebAppProcess:
    """Starts the web app as a subprocess and terminates it on context exit."""

    def __init__(self, command: str, cwd: str) -> None:
        self.command = command
        self.cwd = cwd
        self.proc: subprocess.Popen | None = None

    async def __aenter__(self) -> "WebAppProcess":
        if not self.command:
            return self
        log(logger, "info", "starting web app", cmd=self.command)
        self.proc = subprocess.Popen(
            shlex.split(self.command),
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )
        healthy = await _wait_for_health(WEB_APP_URL + WEB_APP_HEALTH)
        if not healthy:
            log(logger, "warning", "web app healthcheck failed — proceeding anyway")
        return self

    async def __aexit__(self, *_: object) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()


async def _run_claude_review(user_prompt: str, system_prompt: str) -> str:
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=["Read", "Glob", "Grep", "Bash",
                        "mcp__playwright__browser_navigate",
                        "mcp__playwright__browser_screenshot",
                        "mcp__playwright__browser_click",
                        "mcp__playwright__browser_type",
                        "mcp__playwright__browser_snapshot"],
        cwd=REPO_PATH,
        permission_mode="default",
        mcp_servers={
            "playwright": {
                "type": "stdio",
                "command": "npx",
                "args": ["@playwright/mcp", "--headless"],
            },
        },
    )
    parts: list[str] = []
    async for message in query(prompt=user_prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
    return "\n".join(parts).strip()


def _build_review_prompt(task: Task, report: BrowserReport) -> str:
    base = os.environ.get("GIT_DEFAULT_BRANCH", "main")
    return (
        f"TASK ID: {task.task_id}\n"
        f"ORIGINAL REQUEST:\n{task.prompt}\n\n"
        f"DEVELOPER SUMMARY:\n{task.dev_summary or '(none)'}\n\n"
        f"BROWSER REPORT:\n{report.to_markdown()}\n\n"
        f"Diff to review: `git diff {base}...HEAD` in {REPO_PATH}\n"
        "Produce your APPROVED or FEEDBACK verdict now."
    )


def _parse_verdict(text: str) -> tuple[str, str]:
    """Return (verdict, body) where verdict is 'APPROVED' or 'FEEDBACK'."""
    stripped = text.lstrip()
    if stripped.upper().startswith("APPROVED"):
        return "APPROVED", stripped[len("APPROVED"):].strip()
    if stripped.upper().startswith("FEEDBACK"):
        return "FEEDBACK", stripped[len("FEEDBACK"):].strip()
    # Ambiguous output — treat as feedback to be safe.
    return "FEEDBACK", text


async def _process_task(store: TaskStore, task: Task) -> None:
    task.status = TaskStatus.QA_IN_PROGRESS.value
    await store.save(task)
    await store.publish(Event(EventType.QA_STARTED.value, task.task_id,
                              {"iteration": task.iteration}))

    git = GitManager(REPO_PATH)
    git.ensure_feature_branch(task.branch or f"feature/{task.task_id}")

    artifacts_dir = ARTIFACTS_ROOT / task.task_id / f"iter-{task.iteration}"

    async with WebAppProcess(WEB_APP_CMD, REPO_PATH):
        report = await run_browser_smoke(WEB_APP_URL, artifacts_dir)

    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    user_prompt = _build_review_prompt(task, report)

    log(logger, "info", "running claude review", task_id=task.task_id)
    review = await asyncio.wait_for(
        _run_claude_review(user_prompt, system_prompt),
        timeout=TIMEOUT,
    )
    verdict, body = _parse_verdict(review)

    if verdict == "APPROVED":
        # Deploy branch to dev for human review — don't merge yet.
        deploy_cmd = os.environ.get("WEB_APP_START_COMMAND", "")
        if deploy_cmd:
            import subprocess as _sp
            log(logger, "info", "deploying to dev", task_id=task.task_id)
            try:
                _sp.run(deploy_cmd, shell=True, check=True, timeout=300,
                        capture_output=True, text=True)
            except Exception as exc:
                log(logger, "warning", "dev deploy failed (non-fatal)",
                    task_id=task.task_id, error=str(exc))

        # Take a screenshot of the deployed dev site for the user to review.
        screenshot_path = ""
        try:
            shot_dir = artifacts_dir / "review"
            shot_dir.mkdir(parents=True, exist_ok=True)
            shot_file = shot_dir / "dev_screenshot.png"
            from playwright.async_api import async_playwright as _apw
            async with _apw() as pw:
                br = await pw.chromium.launch(headless=True)
                pg = await br.new_page(viewport={"width": 1920, "height": 1080})
                dev_url = os.environ.get(
                    "DEV_DIRECT_URL", WEB_APP_URL)
                await pg.goto(dev_url, wait_until="networkidle",
                              timeout=30_000)
                await pg.screenshot(path=str(shot_file), full_page=True)
                await br.close()
            screenshot_path = str(shot_file)
            log(logger, "info", "review screenshot taken",
                task_id=task.task_id, path=screenshot_path)
        except Exception as exc:
            log(logger, "warning", "failed to take review screenshot",
                task_id=task.task_id, error=str(exc))

        task.status = TaskStatus.AWAITING_REVIEW.value
        task.qa_feedback = body
        await store.save(task)
        await store.publish(Event(EventType.QA_APPROVED.value, task.task_id,
                                  {"summary": body[:1500]}))
        await store.publish(Event(EventType.AWAITING_REVIEW.value, task.task_id,
                                  {"branch": task.branch,
                                   "summary": body[:1500],
                                   "screenshot": screenshot_path}))
        log(logger, "info", "task approved, awaiting human review",
            task_id=task.task_id)
        return

    # FEEDBACK path
    if task.iteration >= MAX_ITER:
        task.status = TaskStatus.NEEDS_MANUAL_REVIEW.value
        task.qa_feedback = body
        await store.save(task)
        await store.publish(Event(EventType.MANUAL_REVIEW.value, task.task_id,
                                  {"feedback": body[:1500]}))
        log(logger, "warning", "max iterations reached", task_id=task.task_id)
        return

    task.qa_feedback = body
    task.status = TaskStatus.DEV_DONE.value
    await store.save(task)
    await store.publish(Event(
        EventType.QA_FEEDBACK.value,
        task.task_id,
        {"feedback": body[:1500], "iteration": task.iteration},
    ))
    await store.enqueue_dev(task.task_id)


async def _worker_loop() -> None:
    store = TaskStore()
    log(logger, "info", "qa agent started", repo=REPO_PATH, url=WEB_APP_URL)
    try:
        while True:
            task_id = await store.pop_qa(timeout=0)
            if not task_id:
                continue
            task = await store.get(task_id)
            if not task:
                log(logger, "error", "task not found", task_id=task_id)
                continue
            try:
                await _process_task(store, task)
            except Exception as exc:
                log(logger, "error", "qa task failed",
                    task_id=task_id, error=str(exc))
                task.status = TaskStatus.FAILED.value
                task.error = str(exc)
                await store.save(task)
                await store.publish(Event(
                    EventType.QA_ERROR.value,
                    task_id,
                    {"error": str(exc)[:1500]},
                ))
    finally:
        await store.close()


def main() -> None:
    asyncio.run(_worker_loop())


if __name__ == "__main__":
    main()
