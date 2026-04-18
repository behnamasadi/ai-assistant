"""Subscribe to Redis events and push user-facing notifications to Telegram."""
from __future__ import annotations

import html as _html
import os
from pathlib import Path

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from shared.logger import get_logger, log
from shared.redis_client import TaskStore
from shared.task_schema import EventType

logger = get_logger("bot.notify")

WEB_APP_URL = os.environ.get("WEB_APP_URL", "http://localhost:3000")

# Task-level templates.
_TEMPLATES: dict[str, str] = {
    # Developer agent
    EventType.DEV_STARTED.value: "🛠 <b>Dev agent started</b> on <code>{task_id}</code>",
    EventType.DEV_COMPLETE.value: (
        "✅ <b>Dev agent finished</b> <code>{task_id}</code>\n"
        "Branch: <code>{branch}</code>\n"
        "Commit: <code>{commit}</code>\n\n"
        "{summary}"
    ),
    EventType.DEV_ERROR.value: "❌ <b>Dev agent failed</b> <code>{task_id}</code>\n<pre>{error}</pre>",
    # Code reviewer (gate 1)
    EventType.REVIEW_STARTED.value: "🔍 <b>Code review started</b> on <code>{task_id}</code>",
    EventType.REVIEW_PASSED.value: "✅ <b>Code review passed</b> <code>{task_id}</code> → queued for UI testing",
    EventType.REVIEW_FEEDBACK.value: (
        "📝 <b>Code review feedback</b> on <code>{task_id}</code> (iteration {iteration})\n\n{feedback}"
    ),
    EventType.REVIEW_ERROR.value: "❌ <b>Code review failed</b> <code>{task_id}</code>\n<pre>{error}</pre>",
    # UI tester (gate 2)
    EventType.UI_TEST_STARTED.value: "🧪 <b>UI testing started</b> on <code>{task_id}</code>",
    EventType.UI_TEST_PASSED.value: (
        "🎉 <b>UI test passed</b> <code>{task_id}</code>\n"
        "Health: {health_score}/100\n\n{summary}"
    ),
    EventType.UI_TEST_FEEDBACK.value: (
        "📝 <b>UI test feedback</b> on <code>{task_id}</code> (iteration {iteration})\n\n{feedback}"
    ),
    EventType.UI_TEST_ERROR.value: "❌ <b>UI test failed</b> <code>{task_id}</code>\n<pre>{error}</pre>",
    # Legacy QA events (backwards compat)
    EventType.QA_STARTED.value: "🔍 <b>QA agent started</b> on <code>{task_id}</code>",
    EventType.QA_APPROVED.value: "🎉 <b>QA approved</b> <code>{task_id}</code>\n\n{summary}",
    EventType.QA_FEEDBACK.value: (
        "📝 <b>QA feedback</b> on <code>{task_id}</code> (iteration {iteration})\n\n{feedback}"
    ),
    EventType.QA_ERROR.value: "❌ <b>QA agent failed</b> <code>{task_id}</code>\n<pre>{error}</pre>",
    # Human review
    EventType.AWAITING_REVIEW.value: (
        "🔎 <b>Ready for your review</b> <code>{task_id}</code>\n\n"
        "Branch: <code>{branch}</code>\n"
        f"Dev site: {WEB_APP_URL}\n\n"
        "Please review the changes and respond:"
    ),
    EventType.MERGED.value: "🚀 <b>Merged to main</b> <code>{task_id}</code>\nCommit: <code>{commit}</code>",
    EventType.DEPLOY_PROD.value: "🚀 <b>Deployed to production</b> <code>{task_id}</code>",
    EventType.REJECTED.value: "❌ <b>Rejected</b> <code>{task_id}</code> — branch kept for rework.",
    EventType.MANUAL_REVIEW.value: (
        "⚠️ <b>Manual review required</b> <code>{task_id}</code> — max feedback iterations reached."
    ),
    # Plan lifecycle
    EventType.PLAN_CREATED.value: (
        "🧩 <b>Plan created</b> <code>{plan_id}</code> — queued for the planner"
    ),
    EventType.PLAN_DRAFTING.value: (
        "🧠 <b>Planner thinking</b> <code>{plan_id}</code> (pass #{replan_count})"
    ),
    EventType.PLAN_APPROVED.value: (
        "✅ <b>Plan approved</b> <code>{plan_id}</code> "
        "(trust_mode={trust_mode})"
    ),
    EventType.PLAN_REJECTED.value: "❌ <b>Plan rejected</b> <code>{plan_id}</code>",
    EventType.PLAN_SUBTASK_DISPATCHED.value: (
        "▶️ <b>Dispatching sub-task</b> <code>{task_id}</code> "
        "(#{index}) of plan <code>{plan_id}</code>\n"
        "<i>{title}</i>"
    ),
    EventType.PLAN_SUBTASK_COMPLETE.value: (
        "✔️ <b>Sub-task landed</b> <code>{task_id}</code> "
        "of plan <code>{plan_id}</code>"
    ),
    EventType.PLAN_PAUSED.value: (
        "⏸ <b>Plan paused</b> <code>{plan_id}</code>\n"
        "Blocked on <code>{blocked_task_id}</code>: {reason}\n\n"
        "Fix manually, then send <code>/resume {plan_id}</code> "
        "or <code>/abort {plan_id}</code>."
    ),
    EventType.PLAN_RESUMED.value: (
        "▶️ <b>Plan resumed</b> <code>{plan_id}</code> — re-planning"
    ),
    EventType.PLAN_COMPLETE.value: (
        "🎉 <b>Plan complete</b> <code>{plan_id}</code> — all sub-tasks merged"
    ),
    EventType.PLAN_ERROR.value: (
        "❌ <b>Planner failed</b> <code>{plan_id}</code>\n<pre>{error}</pre>"
    ),
}

# Fields whose values come from agent output and may contain HTML-special chars.
_ESCAPE_FIELDS = {"summary", "feedback", "error", "reason", "title"}

# Events that need inline keyboard buttons instead of plain text.
_EVENTS_WITH_REVIEW_BUTTONS = {EventType.AWAITING_REVIEW.value}
# PLAN_READY is handled separately because the message body is generated from
# the sub-task list in the payload rather than a static template.
_PLAN_EVENTS = {
    EventType.PLAN_CREATED.value,
    EventType.PLAN_DRAFTING.value,
    EventType.PLAN_READY.value,
    EventType.PLAN_APPROVED.value,
    EventType.PLAN_REJECTED.value,
    EventType.PLAN_SUBTASK_DISPATCHED.value,
    EventType.PLAN_SUBTASK_COMPLETE.value,
    EventType.PLAN_PAUSED.value,
    EventType.PLAN_RESUMED.value,
    EventType.PLAN_COMPLETE.value,
    EventType.PLAN_ERROR.value,
}


def _render(event_type: str, payload: dict) -> str | None:
    template = _TEMPLATES.get(event_type)
    if not template:
        return None
    safe = {
        k: _html.escape(str(v)) if k in _ESCAPE_FIELDS else v
        for k, v in payload.items()
    }
    try:
        return template.format(**safe)
    except KeyError:
        return f"<b>{_html.escape(event_type)}</b> — {_html.escape(str(payload))}"


def _review_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Approve & Deploy to Prod",
                callback_data=f"review_approve:{task_id}",
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"review_reject:{task_id}",
            ),
        ]
    ])


def _plan_approval_keyboard(plan_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Approve (review each)",
                callback_data=f"plan_approve_manual:{plan_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "🚀 Approve & auto-merge",
                callback_data=f"plan_approve_trust:{plan_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"plan_reject:{plan_id}",
            ),
        ],
    ])


def _render_plan_ready(plan_id: str, payload: dict) -> str:
    """Compose the PLAN_READY message body from the sub-task list."""
    if not payload.get("split", False):
        reason = _html.escape(str(payload.get("reason", "")))
        return (
            f"🧩 <b>Plan ready</b> <code>{plan_id}</code>\n"
            f"Single atomic task (no split): {reason}\n\n"
            "Approve to dispatch, or reply to this message with edits."
        )
    subtasks = payload.get("subtasks") or []
    reasoning = _html.escape(str(payload.get("reasoning", "")))
    lines = [
        f"🧩 <b>Plan ready</b> <code>{plan_id}</code>",
        f"<i>{reasoning}</i>" if reasoning else "",
        "",
        "<b>Sub-tasks:</b>",
    ]
    for s in subtasks:
        status = s.get("status", "pending")
        idx = s.get("index", "?")
        title = _html.escape(str(s.get("title", "")))
        marker = "✓" if status == "done" else "•"
        lines.append(f"  {marker} [{idx}] <b>{title}</b>")
        body = str(s.get("prompt", "")).strip()
        if body:
            preview = _html.escape(body[:180])
            lines.append(f"      <i>{preview}</i>")
    lines.append("")
    lines.append(
        "Approve to dispatch the first sub-task, or reply to this "
        "message with edits."
    )
    return "\n".join([line for line in lines if line is not None])


async def _handle_task_event(
    bot: Bot, store: TaskStore, event_type: str,
    task_id: str, payload: dict,
) -> None:
    task = await store.get(task_id)
    if not task or task.telegram_chat_id is None:
        return
    payload = {"task_id": task_id, **payload}
    text = _render(event_type, payload)
    if not text:
        return
    try:
        screenshot = payload.get("screenshot", "")
        if screenshot and Path(screenshot).is_file():
            with open(screenshot, "rb") as f:
                await bot.send_photo(
                    chat_id=task.telegram_chat_id,
                    photo=f,
                    caption=f"Preview for {task_id}",
                )
        kwargs: dict = {
            "chat_id": task.telegram_chat_id,
            "text": text,
            "parse_mode": ParseMode.HTML,
        }
        if event_type in _EVENTS_WITH_REVIEW_BUTTONS:
            kwargs["reply_markup"] = _review_keyboard(task_id)
        await bot.send_message(**kwargs)
    except Exception as exc:
        log(logger, "error", "failed to send task notification",
            task_id=task_id, error=str(exc))


async def _handle_plan_event(
    bot: Bot, store: TaskStore, event_type: str,
    plan_id: str, payload: dict,
) -> None:
    plan = await store.get_plan(plan_id)
    if not plan or plan.telegram_chat_id is None:
        return
    try:
        if event_type == EventType.PLAN_READY.value:
            text = _render_plan_ready(plan_id, payload)
            msg = await bot.send_message(
                chat_id=plan.telegram_chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=_plan_approval_keyboard(plan_id),
            )
            # Remember the message_id so free-text replies can find the plan.
            plan.plan_message_id = msg.message_id
            await store.save_plan(plan)
            return
        payload = {"plan_id": plan_id, **payload}
        text = _render(event_type, payload)
        if not text:
            return
        await bot.send_message(
            chat_id=plan.telegram_chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        log(logger, "error", "failed to send plan notification",
            plan_id=plan_id, error=str(exc))


async def run_notification_loop(bot: Bot, store: TaskStore) -> None:
    log(logger, "info", "notification listener started")
    async for event in store.subscribe():
        event_type = event.event_type
        target_id = event.task_id  # reused field: holds plan_id for plan events
        payload = event.payload or {}
        if event_type in _PLAN_EVENTS:
            await _handle_plan_event(bot, store, event_type,
                                     target_id, payload)
        else:
            await _handle_task_event(bot, store, event_type,
                                     target_id, payload)
