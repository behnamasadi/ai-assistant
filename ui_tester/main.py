"""UI tester agent: pops review-passed tasks, deploys to dev, runs browser tests, produces verdict."""
from __future__ import annotations

import asyncio
import os
import signal
import shlex
import subprocess
import time
from pathlib import Path

import aiohttp
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query
from dotenv import load_dotenv

from ui_tester.browser_tester import BrowserReport, run_browser_smoke
from shared.event_log import make_entry
from shared.git_manager import GitManager
from shared.logger import get_logger, log
from shared.redis_client import TaskStore
from shared.task_schema import Event, EventType, Task, TaskStatus

load_dotenv()

logger = get_logger("ui_tester")

REPO_PATH = os.environ.get("GIT_REPO_PATH", "/workspace/project")
WEB_APP_URL = os.environ.get("WEB_APP_URL", "http://web-app:3000")
WEB_APP_HEALTH = os.environ.get("WEB_APP_HEALTHCHECK_PATH", "/")
WEB_APP_CMD = os.environ.get("WEB_APP_START_COMMAND", "")
MAX_ITER = int(os.environ.get("MAX_FEEDBACK_ITERATIONS", "3"))
TIMEOUT = int(os.environ.get("UI_TEST_AGENT_TIMEOUT_SECONDS", "1200"))

PROMPT_PATH = Path(__file__).parent / "prompts" / "tester.md"
ARTIFACTS_ROOT = Path(os.environ.get("UI_TEST_ARTIFACTS_DIR", "/tmp/ui_test_artifacts"))


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


async def _run_claude_ui_review(user_prompt: str, system_prompt: str) -> str:
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


def _build_test_prompt(task: Task, report: BrowserReport) -> str:
    return (
        f"TASK ID: {task.task_id}\n"
        f"ORIGINAL REQUEST:\n{task.prompt}\n\n"
        f"DEVELOPER SUMMARY:\n{task.dev_summary or '(none)'}\n\n"
        f"CODE REVIEW RESULT:\n{task.review_feedback or '(passed without comments)'}\n\n"
        f"AUTOMATED BROWSER REPORT:\n{report.to_markdown()}\n\n"
        f"Branch: {task.branch}\n"
        f"Iteration: {task.iteration}\n\n"
        "Now browse http://localhost:7870, test the feature manually, and produce your verdict."
    )


def _parse_verdict(text: str) -> tuple[str, str, int]:
    """Return (verdict, body, health_score)."""
    # Extract health score if present
    health_score = 0
    for line in text.splitlines():
        stripped = line.strip().upper()
        if stripped.startswith("HEALTH:") or stripped.startswith("HEALTH SCORE:"):
            try:
                health_score = int("".join(c for c in stripped.split(":")[-1] if c.isdigit()))
            except ValueError:
                pass

    upper = text.strip().upper()
    for keyword in ("PASSED", "FEEDBACK", "BLOCKED"):
        if upper.startswith(f"STATUS: {keyword}") or upper.startswith(keyword):
            return keyword, text.strip(), health_score
    for line in text.splitlines():
        stripped = line.strip().upper()
        for keyword in ("PASSED", "FEEDBACK", "BLOCKED"):
            if stripped.startswith(f"STATUS: {keyword}"):
                return keyword, text.strip(), health_score
    return "FEEDBACK", text.strip(), health_score


async def _process_task(store: TaskStore, task: Task) -> None:
    task.status = TaskStatus.UI_TEST_IN_PROGRESS.value
    await store.save(task)
    await store.publish(Event(EventType.UI_TEST_STARTED.value, task.task_id,
                              {"iteration": task.iteration}))
    await store.log_event(task.task_id, make_entry(
        "ui_tester", "test_started",
        f"Starting UI testing (iteration {task.iteration})",
        iteration=task.iteration,
    ))

    git = GitManager(REPO_PATH)
    git.ensure_feature_branch(task.branch or f"feature/{task.task_id}")

    artifacts_dir = ARTIFACTS_ROOT / task.task_id / f"iter-{task.iteration}"

    # Run automated browser smoke test first
    async with WebAppProcess(WEB_APP_CMD, REPO_PATH):
        report = await run_browser_smoke(WEB_APP_URL, artifacts_dir)

    # Then run Claude for intelligent UI review with browser access
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    user_prompt = _build_test_prompt(task, report)

    log(logger, "info", "running UI test review", task_id=task.task_id)
    review = await asyncio.wait_for(
        _run_claude_ui_review(user_prompt, system_prompt),
        timeout=TIMEOUT,
    )
    verdict, body, health_score = _parse_verdict(review)

    if verdict == "PASSED":
        # Take a final screenshot for human review
        screenshot_path = ""
        try:
            shot_dir = artifacts_dir / "review"
            shot_dir.mkdir(parents=True, exist_ok=True)
            shot_file = shot_dir / "dev_screenshot.png"
            from playwright.async_api import async_playwright as _apw
            async with _apw() as pw_inst:
                br = await pw_inst.chromium.launch(headless=True)
                pg = await br.new_page(viewport={"width": 1920, "height": 1080})
                dev_url = os.environ.get("DEV_DIRECT_URL", "http://localhost:7870")
                await pg.goto(dev_url, wait_until="networkidle", timeout=30_000)
                await pg.screenshot(path=str(shot_file), full_page=True)
                await br.close()
            screenshot_path = str(shot_file)
        except Exception as exc:
            log(logger, "warning", "failed to take review screenshot",
                task_id=task.task_id, error=str(exc))

        task.status = TaskStatus.AWAITING_REVIEW.value
        task.ui_test_feedback = body
        task.health_score = health_score
        await store.save(task)
        await store.log_event(task.task_id, make_entry(
            "ui_tester", "test_verdict",
            f"UI test PASSED (health: {health_score}/100) — awaiting human review",
            verdict="PASSED", health_score=health_score, feedback=body[:500],
        ))
        await store.publish(Event(EventType.UI_TEST_PASSED.value, task.task_id,
                                  {"summary": body[:1500], "health_score": health_score}))
        await store.publish(Event(EventType.AWAITING_REVIEW.value, task.task_id,
                                  {"branch": task.branch,
                                   "summary": body[:1500],
                                   "health_score": health_score,
                                   "screenshot": screenshot_path}))
        log(logger, "info", "UI test passed, awaiting human review",
            task_id=task.task_id, health_score=health_score)
        return

    # FEEDBACK or BLOCKED — send back to developer
    if task.iteration >= MAX_ITER:
        task.status = TaskStatus.NEEDS_MANUAL_REVIEW.value
        task.ui_test_feedback = body
        task.health_score = health_score
        await store.save(task)
        await store.log_event(task.task_id, make_entry(
            "ui_tester", "test_verdict",
            f"UI test {verdict} (health: {health_score}/100) — max iterations, needs manual review",
            verdict=verdict, health_score=health_score, feedback=body[:500],
        ))
        await store.publish(Event(EventType.MANUAL_REVIEW.value, task.task_id,
                                  {"feedback": body[:1500]}))
        log(logger, "warning", "max iterations reached at UI testing",
            task_id=task.task_id)
        return

    task.ui_test_feedback = body
    task.health_score = health_score
    task.status = TaskStatus.DEV_DONE.value
    await store.save(task)
    await store.log_event(task.task_id, make_entry(
        "ui_tester", "test_verdict",
        f"UI test {verdict} (health: {health_score}/100) — sending feedback to developer",
        verdict=verdict, health_score=health_score, feedback=body[:500],
    ))
    await store.publish(Event(
        EventType.UI_TEST_FEEDBACK.value,
        task.task_id,
        {"feedback": body[:1500], "iteration": task.iteration,
         "health_score": health_score},
    ))
    await store.enqueue_dev(task.task_id)
    log(logger, "info", "UI test feedback sent to developer",
        task_id=task.task_id)


async def _worker_loop() -> None:
    store = TaskStore()
    log(logger, "info", "UI tester agent started", repo=REPO_PATH, url=WEB_APP_URL)
    try:
        while True:
            task_id = await store.pop_ui_test(timeout=0)
            if not task_id:
                continue
            task = await store.get(task_id)
            if not task:
                log(logger, "error", "task not found", task_id=task_id)
                continue
            try:
                await _process_task(store, task)
            except Exception as exc:
                log(logger, "error", "UI test failed",
                    task_id=task_id, error=str(exc))
                task.status = TaskStatus.FAILED.value
                task.error = str(exc)
                await store.save(task)
                await store.log_event(task_id, make_entry(
                    "ui_tester", "test_error",
                    f"UI test failed: {str(exc)[:200]}",
                ))
                await store.publish(Event(
                    EventType.UI_TEST_ERROR.value,
                    task_id,
                    {"error": str(exc)[:1500]},
                ))
    finally:
        await store.close()


def main() -> None:
    asyncio.run(_worker_loop())


if __name__ == "__main__":
    main()
