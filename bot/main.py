"""Telegram bot entry point. Accepts voice + text messages and queues tasks."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.notification_listener import run_notification_loop
from bot.task_publisher import publish_task
from shared.logger import get_logger, log
from shared.redis_client import TaskStore

load_dotenv()

logger = get_logger("bot")

ALLOWED_USER_ID = int(os.environ.get("TELEGRAM_ALLOWED_USER_ID", "0"))
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]


def _authorized(update: Update) -> bool:
    if not update.effective_user:
        return False
    if ALLOWED_USER_ID and update.effective_user.id != ALLOWED_USER_ID:
        return False
    return True


async def _transcribe_voice(path: Path) -> str | None:
    """Best-effort transcription via OpenAI Whisper (optional).

    Returns None if no OPENAI_API_KEY is set — the caller should send
    a friendly message back to the user in that case.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key)
        with path.open("rb") as fp:
            result = await client.audio.transcriptions.create(
                model="whisper-1",
                file=fp,
            )
        return result.text
    except Exception as exc:
        log(logger, "error", "whisper transcription failed", error=str(exc))
        return None


async def on_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.reply_text(
        "👋 Send me a text or voice message describing what you want built."
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update) or not update.message or not update.message.text:
        return
    store: TaskStore = context.application.bot_data["store"]
    task = await publish_task(
        store,
        prompt=update.message.text.strip(),
        telegram_user_id=update.effective_user.id,
        telegram_chat_id=update.effective_chat.id,
        telegram_message_id=update.message.message_id,
    )
    log(logger, "info", "queued task from text", task_id=task.task_id)
    await update.message.reply_text(f"📥 Queued `{task.task_id}`", parse_mode="Markdown")


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update) or not update.message or not update.message.voice:
        return
    store: TaskStore = context.application.bot_data["store"]
    file = await update.message.voice.get_file()
    with tempfile.TemporaryDirectory() as tmp:
        ogg_path = Path(tmp) / "voice.ogg"
        await file.download_to_drive(custom_path=str(ogg_path))
        transcript = await _transcribe_voice(ogg_path)

    if not transcript:
        await update.message.reply_text(
            "🎙 Voice received but transcription is not configured. "
            "Set OPENAI_API_KEY in .env or send text instead."
        )
        return

    task = await publish_task(
        store,
        prompt=transcript,
        telegram_user_id=update.effective_user.id,
        telegram_chat_id=update.effective_chat.id,
        telegram_message_id=update.message.message_id,
    )
    log(logger, "info", "queued task from voice", task_id=task.task_id)
    await update.message.reply_text(
        f"📥 Queued `{task.task_id}`\n> {transcript}", parse_mode="Markdown"
    )


async def _run_listener(app: Application) -> None:
    store: TaskStore = app.bot_data["store"]
    await run_notification_loop(app.bot, store)


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.bot_data["store"] = TaskStore()

    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))

    async def _post_init(application: Application) -> None:
        application.create_task(_run_listener(application))

    app.post_init = _post_init
    log(logger, "info", "telegram bot starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
