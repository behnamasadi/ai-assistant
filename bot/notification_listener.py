"""Subscribe to Redis events and push user-facing notifications to Telegram."""
from __future__ import annotations

from telegram import Bot
from telegram.constants import ParseMode

from shared.logger import get_logger, log
from shared.redis_client import TaskStore
from shared.task_schema import EventType

logger = get_logger("bot.notify")


# Human-readable templates for each event type. Missing keys are silently skipped.
_TEMPLATES: dict[str, str] = {
    EventType.DEV_STARTED.value: "🛠 *Dev agent started* on `{task_id}`",
    EventType.DEV_COMPLETE.value: (
        "✅ *Dev agent finished* `{task_id}`\n"
        "Branch: `{branch}`\n"
        "Commit: `{commit}`\n\n"
        "{summary}"
    ),
    EventType.DEV_ERROR.value: "❌ *Dev agent failed* `{task_id}`\n```\n{error}\n```",
    EventType.QA_STARTED.value: "🔍 *QA agent started* on `{task_id}`",
    EventType.QA_APPROVED.value: "🎉 *QA approved* `{task_id}`\n\n{summary}",
    EventType.QA_FEEDBACK.value: (
        "📝 *QA feedback* on `{task_id}` (iteration {iteration})\n\n{feedback}"
    ),
    EventType.QA_ERROR.value: "❌ *QA agent failed* `{task_id}`\n```\n{error}\n```",
    EventType.MERGED.value: "🚀 *Merged to main* `{task_id}`\nCommit: `{commit}`",
    EventType.MANUAL_REVIEW.value: (
        "⚠️ *Manual review required* `{task_id}` — max feedback iterations reached."
    ),
}


def _render(event_type: str, payload: dict) -> str | None:
    template = _TEMPLATES.get(event_type)
    if not template:
        return None
    try:
        return template.format(**payload)
    except KeyError:
        # Fall back to raw payload if some fields are missing.
        return f"*{event_type}* — {payload}"


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
            await bot.send_message(
                chat_id=task.telegram_chat_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as exc:
            log(logger, "error", "failed to send telegram notification",
                task_id=event.task_id, error=str(exc))
