"""Telegram bot entry point. Accepts voice + text messages and queues tasks."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.notification_listener import run_notification_loop
from bot.task_publisher import publish_task
from shared.git_manager import GitManager
from shared.logger import get_logger, log
from shared.redis_client import TaskStore
from shared.task_schema import Event, EventType, TaskStatus

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


_whisper_model = None


def _get_whisper_model():
    """Lazy-load the local Whisper model (downloaded on first use)."""
    global _whisper_model
    if _whisper_model is None:
        import whisper
        model_size = os.environ.get("WHISPER_MODEL", "base")
        log(logger, "info", f"loading local whisper model: {model_size}")
        _whisper_model = whisper.load_model(model_size)
        log(logger, "info", "whisper model loaded")
    return _whisper_model


async def _transcribe_voice(path: Path) -> str | None:
    """Transcribe voice using local Whisper (GPU) or OpenAI API fallback.

    Priority:
      1. Local Whisper (free, uses your GPU) — always available
      2. OpenAI Whisper API — only if OPENAI_API_KEY is set
    """
    # Try local Whisper first
    try:
        model = await asyncio.get_event_loop().run_in_executor(
            None, _get_whisper_model,
        )
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: model.transcribe(str(path)),
        )
        text = result.get("text", "").strip()
        if text:
            log(logger, "info", "transcribed via local whisper",
                length=len(text))
            return text
    except Exception as exc:
        log(logger, "error", "local whisper failed, trying API fallback",
            error=str(exc))

    # Fallback to OpenAI API if key is set
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
        log(logger, "error", "openai whisper fallback failed", error=str(exc))
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
    file = await update.message.voice.get_file()
    with tempfile.TemporaryDirectory() as tmp:
        ogg_path = Path(tmp) / "voice.ogg"
        await file.download_to_drive(custom_path=str(ogg_path))
        transcript = await _transcribe_voice(ogg_path)

    if not transcript:
        await update.message.reply_text(
            "🎙 Voice received but transcription failed. "
            "Please try again or send text instead."
        )
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data="voice_confirm"),
            InlineKeyboardButton("✏️ Edit", callback_data="voice_edit"),
            InlineKeyboardButton("❌ Cancel", callback_data="voice_cancel"),
        ]
    ])
    reply = await update.message.reply_text(
        f"🎙 I heard:\n\n_{transcript}_\n\nSubmit this as a task?",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    # Store transcript in bot_data keyed by the reply message id
    context.application.bot_data[f"voice:{reply.message_id}"] = {
        "transcript": transcript,
        "user_id": update.effective_user.id,
        "chat_id": update.effective_chat.id,
        "original_message_id": update.message.message_id,
    }
    log(logger, "info", "voice transcribed, awaiting confirmation",
        length=len(transcript))


async def on_voice_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("voice_"):
        return
    await query.answer()

    msg_id = query.message.message_id
    key = f"voice:{msg_id}"
    pending = context.application.bot_data.get(key)
    if not pending:
        await query.edit_message_text("⚠️ This voice message has expired.")
        return

    if query.data == "voice_cancel":
        context.application.bot_data.pop(key, None)
        await query.edit_message_text("❌ Cancelled.")
        return

    if query.data == "voice_edit":
        context.application.bot_data.pop(key, None)
        await query.edit_message_text(
            f"✏️ Original transcription:\n\n_{pending['transcript']}_\n\n"
            "Send me the corrected text as a regular message.",
            parse_mode="Markdown",
        )
        return

    if query.data == "voice_confirm":
        store: TaskStore = context.application.bot_data["store"]
        task = await publish_task(
            store,
            prompt=pending["transcript"],
            telegram_user_id=pending["user_id"],
            telegram_chat_id=pending["chat_id"],
            telegram_message_id=pending["original_message_id"],
        )
        context.application.bot_data.pop(key, None)
        log(logger, "info", "queued task from voice", task_id=task.task_id)
        await query.edit_message_text(
            f"📥 Queued `{task.task_id}`\n> {pending['transcript']}",
            parse_mode="Markdown",
        )


REPO_PATH = os.environ.get("GIT_REPO_PATH", "/workspace/project")
DEPLOY_PROD_CMD = os.environ.get(
    "DEPLOY_PROD_COMMAND",
    "make -C /home/behnam/workspace/magic-inspection-colmap deploy-prod",
)


async def on_review_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle approve/reject buttons for human-in-the-loop review."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    parts = query.data.split(":", 1)
    if len(parts) != 2:
        return
    action, task_id = parts[0], parts[1]

    store: TaskStore = context.application.bot_data["store"]
    task = await store.get(task_id)
    if not task:
        await query.edit_message_text(f"Task `{task_id}` not found.")
        return
    if task.status != TaskStatus.AWAITING_REVIEW.value:
        await query.edit_message_text(
            f"Task `{task_id}` is no longer awaiting review "
            f"(status: {task.status})."
        )
        return

    if action == "review_reject":
        task.status = TaskStatus.REJECTED.value
        await store.save(task)
        await store.publish(Event(
            EventType.REJECTED.value, task_id, {},
        ))
        await query.edit_message_text(
            f"❌ Rejected `{task_id}`. Branch `{task.branch}` "
            "kept for future rework.",
        )
        log(logger, "info", "task rejected by user", task_id=task_id)
        return

    if action == "review_approve":
        await query.edit_message_text(
            f"⏳ Merging and deploying `{task_id}` to production..."
        )
        try:
            git = GitManager(REPO_PATH)
            commit = git.merge_to_main(task.branch)
            task.status = TaskStatus.APPROVED.value
            task.commit_hash = commit
            await store.save(task)
            await store.publish(Event(
                EventType.MERGED.value, task_id,
                {"commit": commit[:10]},
            ))
            log(logger, "info", "merged to main",
                task_id=task_id, commit=commit[:10])

            # Deploy to production
            import subprocess
            result = subprocess.run(
                DEPLOY_PROD_CMD, shell=True,
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode == 0:
                task.status = TaskStatus.DEPLOYED.value
                await store.save(task)
                await store.publish(Event(
                    EventType.DEPLOY_PROD.value, task_id, {},
                ))
                log(logger, "info", "deployed to prod",
                    task_id=task_id)
            else:
                log(logger, "error", "prod deploy failed",
                    task_id=task_id, stderr=result.stderr[:500])
                await context.application.bot.send_message(
                    chat_id=task.telegram_chat_id,
                    text=(
                        f"⚠️ Merged `{task_id}` to main but "
                        f"prod deploy failed:\n```\n"
                        f"{result.stderr[:500]}\n```"
                    ),
                    parse_mode="Markdown",
                )
        except Exception as exc:
            log(logger, "error", "merge/deploy failed",
                task_id=task_id, error=str(exc))
            await context.application.bot.send_message(
                chat_id=task.telegram_chat_id,
                text=f"❌ Merge/deploy failed for `{task_id}`:\n"
                     f"```\n{str(exc)[:500]}\n```",
                parse_mode="Markdown",
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
    app.add_handler(CallbackQueryHandler(on_voice_callback, pattern=r"^voice_"))
    app.add_handler(CallbackQueryHandler(on_review_callback, pattern=r"^review_"))

    async def _post_init(application: Application) -> None:
        application.create_task(_run_listener(application))

    app.post_init = _post_init
    log(logger, "info", "telegram bot starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
