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

# HTML templates — safer than Markdown for agent-generated content.
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
}

# Fields whose values come from agent output and may contain HTML-special chars.
_ESCAPE_FIELDS = {"summary", "feedback", "error"}

# Events that need inline keyboard buttons instead of plain text.
_EVENTS_WITH_BUTTONS = {EventType.AWAITING_REVIEW.value}


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


async def run_notification_loop(bot: Bot, store: TaskStore) -> None:
    log(logger, "info", "notification listener started")
    async for event in store.subscribe():
        task = await store.get(event.task_id)
        if not task or task.telegram_chat_id is None:
            continue
        payload = {"task_id": event.task_id, **event.payload}
        text = _render(event.event_type, payload)
        if not text:
            continue
        try:
            # Send screenshot first if available (AWAITING_REVIEW events).
            screenshot = event.payload.get("screenshot", "")
            if screenshot and Path(screenshot).is_file():
                with open(screenshot, "rb") as f:
                    await bot.send_photo(
                        chat_id=task.telegram_chat_id,
                        photo=f,
                        caption=f"Preview for {event.task_id}",
                    )

            kwargs: dict = {
                "chat_id": task.telegram_chat_id,
                "text": text,
                "parse_mode": ParseMode.HTML,
            }
            if event.event_type in _EVENTS_WITH_BUTTONS:
                kwargs["reply_markup"] = _review_keyboard(event.task_id)
            await bot.send_message(**kwargs)
        except Exception as exc:
            log(logger, "error", "failed to send telegram notification",
                task_id=event.task_id, error=str(exc))
