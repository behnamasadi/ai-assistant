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
TRIAGE_MODEL = os.environ.get("TRIAGE_MODEL", "gpt-4o-mini")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "gpt-4o-mini")


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
        "👋 Send me a text or voice message describing what you want built.\n\n"
        "Commands:\n"
        "/status — queue and agent status\n"
        "/tasks — list all tasks\n"
        "/task <id> — details for a specific task"
    )


async def on_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    store: TaskStore = context.application.bot_data["store"]
    queues = await store.queue_lengths()
    tasks = await store.get_all_tasks()

    by_status: dict[str, int] = {}
    for t in tasks:
        by_status[t.status] = by_status.get(t.status, 0) + 1

    active = [t for t in tasks if t.status in (
        TaskStatus.DEV_IN_PROGRESS.value,
        TaskStatus.QA_IN_PROGRESS.value,
        TaskStatus.AWAITING_REVIEW.value,
    )]

    lines = ["📊 *System Status*\n"]
    lines.append(f"Dev queue: {queues['dev_queue']} pending")
    lines.append(f"QA queue: {queues['qa_queue']} pending")
    lines.append(f"Total tasks: {len(tasks)}")
    lines.append("")

    if by_status:
        lines.append("*By status:*")
        status_icons = {
            "queued": "⏳", "dev_in_progress": "🛠",
            "dev_done": "✅", "qa_in_progress": "🔍",
            "awaiting_review": "👀", "approved": "✅",
            "deployed": "🚀", "rejected": "❌",
            "failed": "💥", "needs_manual_review": "⚠️",
        }
        for s, count in sorted(by_status.items()):
            icon = status_icons.get(s, "•")
            lines.append(f"  {icon} {s}: {count}")

    if active:
        lines.append("\n*Active now:*")
        for t in active:
            prompt_short = t.prompt[:50].replace('\n', ' ')
            lines.append(f"  `{t.task_id}` — {t.status}\n  _{prompt_short}_")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def on_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    store: TaskStore = context.application.bot_data["store"]
    tasks = await store.get_all_tasks()

    if not tasks:
        await update.message.reply_text("No tasks found.")
        return

    tasks.sort(key=lambda t: t.created_at, reverse=True)

    lines = ["📋 *All Tasks* (newest first)\n"]
    for t in tasks[:20]:
        from datetime import datetime
        ts = datetime.fromtimestamp(t.created_at).strftime("%m/%d %H:%M")
        prompt_short = t.prompt[:40].replace('\n', ' ')
        status_icons = {
            "queued": "⏳", "dev_in_progress": "🛠",
            "dev_done": "✅", "qa_in_progress": "🔍",
            "awaiting_review": "👀", "approved": "✅",
            "deployed": "🚀", "rejected": "❌",
            "failed": "💥", "needs_manual_review": "⚠️",
        }
        icon = status_icons.get(t.status, "•")
        lines.append(f"{icon} `{t.task_id}`\n  {ts} — _{prompt_short}_")

    if len(tasks) > 20:
        lines.append(f"\n_...and {len(tasks) - 20} more_")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def on_task_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _authorized(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /task <task-id>")
        return

    store: TaskStore = context.application.bot_data["store"]
    task = await store.get(context.args[0])
    if not task:
        await update.message.reply_text(f"Task `{context.args[0]}` not found.",
                                        parse_mode="Markdown")
        return

    from datetime import datetime
    created = datetime.fromtimestamp(task.created_at).strftime("%Y-%m-%d %H:%M")
    updated = datetime.fromtimestamp(task.updated_at).strftime("%Y-%m-%d %H:%M")

    lines = [
        f"📝 *Task* `{task.task_id}`\n",
        f"*Status:* {task.status}",
        f"*Branch:* `{task.branch or 'none'}`",
        f"*Iteration:* {task.iteration}",
        f"*Created:* {created}",
        f"*Updated:* {updated}",
        f"\n*Prompt:*\n_{task.prompt[:500]}_",
    ]
    if task.dev_summary:
        lines.append(f"\n*Dev summary:*\n{task.dev_summary[:500]}")
    if task.qa_feedback:
        lines.append(f"\n*QA feedback:*\n{task.qa_feedback[:500]}")
    if task.error:
        lines.append(f"\n*Error:*\n```\n{task.error[:300]}\n```")
    if task.commit_hash:
        lines.append(f"\n*Commit:* `{task.commit_hash[:10]}`")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def _classify_message(text: str) -> str:
    """Classify a message as 'question' or 'task' using a fast LLM call.

    - question: general questions, status inquiries, explanations, advice
    - task: requests to build, fix, change, deploy, or modify code
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        # No API key — default to task mode (original behaviour)
        return "task"

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key)
    try:
        resp = await client.chat.completions.create(
            model=TRIAGE_MODEL,
            temperature=0,
            max_tokens=10,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You classify user messages. Reply with exactly one word:\n"
                        "  question — if the user is asking a question, requesting an explanation, "
                        "asking for advice, or having a conversation.\n"
                        "  task — if the user is requesting code changes, feature implementation, "
                        "bug fixes, deployments, refactoring, or any hands-on development work.\n"
                        "Reply ONLY with 'question' or 'task'."
                    ),
                },
                {"role": "user", "content": text},
            ],
        )
        label = resp.choices[0].message.content.strip().lower()
        if label not in ("question", "task"):
            label = "task"
        log(logger, "info", "message classified", label=label, preview=text[:60])
        return label
    except Exception as exc:
        log(logger, "error", "triage classification failed, defaulting to task",
            error=str(exc))
        return "task"


async def _answer_question(text: str) -> str:
    """Answer a question directly using the chat model."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return "Sorry, I can't answer questions right now (no API key configured)."

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key)
    try:
        resp = await client.chat.completions.create(
            model=CHAT_MODEL,
            temperature=0.7,
            max_tokens=1000,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a helpful software engineering assistant. "
                        "Answer concisely and accurately. Use Markdown formatting. "
                        "If the user seems to be requesting code changes rather than "
                        "asking a question, tell them to rephrase as an explicit task "
                        "request (e.g. 'build X', 'fix Y', 'add Z')."
                    ),
                },
                {"role": "user", "content": text},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        log(logger, "error", "chat answer failed", error=str(exc))
        return f"Sorry, I couldn't process your question: {exc}"


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update) or not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    label = await _classify_message(text)

    if label == "question":
        log(logger, "info", "answering question directly", preview=text[:60])
        answer = await _answer_question(text)
        await update.message.reply_text(answer, parse_mode="Markdown")
        return

    # Coding task — enter the full pipeline
    store: TaskStore = context.application.bot_data["store"]
    task = await publish_task(
        store,
        prompt=text,
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
    app.add_handler(CommandHandler("status", on_status))
    app.add_handler(CommandHandler("tasks", on_tasks))
    app.add_handler(CommandHandler("task", on_task_detail))
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
