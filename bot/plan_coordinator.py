"""Plan coordinator — reacts to plan & task events and drives the plan forward.

Runs as a background task inside the bot process, alongside the
notification listener. Responsibilities:

- On PLAN_APPROVED → acquire the active-plan lock (or queue) and
  dispatch the first pending sub-task.
- On MERGED for a plan sub-task → mark it done and either re-plan or
  mark the plan complete.
- On PLAN_READY after a re-plan → dispatch the next pending sub-task.
- On AWAITING_REVIEW with a task that is in trust mode and has a healthy
  score → auto-approve (merge + deploy) without waiting for the user.
- On REJECTED / MANUAL_REVIEW for a plan sub-task → pause the plan.
- When a plan finishes/aborts, activate the next pending plan.
"""
from __future__ import annotations

import os
import subprocess

from telegram import Bot
from telegram.constants import ParseMode

from shared.event_log import make_entry
from shared.git_manager import GitManager
from shared.logger import get_logger, log
from shared.redis_client import TaskStore
from shared.task_schema import (
    Event,
    EventType,
    Plan,
    PlanStatus,
    Task,
    TaskStatus,
)

logger = get_logger("bot.plan_coordinator")

REPO_PATH = os.environ.get("GIT_REPO_PATH", "/workspace/project")
DEPLOY_PROD_CMD = os.environ.get("DEPLOY_PROD_COMMAND", "")
AUTO_APPROVE_MIN_HEALTH = int(
    os.environ.get("PLAN_AUTO_APPROVE_MIN_HEALTH", "85"),
)


# ── Helpers ────────────────────────────────────────────────────


async def _dispatch_next_subtask(store: TaskStore, plan: Plan) -> bool:
    """Create the next pending sub-task as a Task and enqueue to dev.

    Returns True if a sub-task was dispatched. False if the plan has no
    pending sub-tasks left (caller should mark the plan complete).
    """
    subtasks = plan.get_subtasks()
    next_idx = None
    for i, s in enumerate(subtasks):
        if s.status == "pending":
            next_idx = i
            break
    if next_idx is None:
        return False

    s = subtasks[next_idx]
    task = Task.subtask(
        plan_id=plan.plan_id,
        index=s.index,
        prompt=s.prompt,
        telegram_user_id=plan.telegram_user_id,
        telegram_chat_id=plan.telegram_chat_id,
        telegram_message_id=plan.telegram_message_id,
        auto_approve=plan.trust_mode,
    )
    task.status = TaskStatus.QUEUED.value
    await store.save(task)
    await store.enqueue_dev(task.task_id)

    s.status = "dispatched"
    s.task_id = task.task_id
    subtasks[next_idx] = s
    plan.set_subtasks(subtasks)
    plan.current_index = s.index
    plan.status = PlanStatus.RUNNING.value
    await store.save_plan(plan)

    await store.log_event(plan.plan_id, make_entry(
        "plan_coordinator", "subtask_dispatched",
        f"Dispatched sub-task [{s.index}] {s.title} → {task.task_id}",
        subtask_index=s.index, task_id=task.task_id,
    ))
    await store.publish(Event(
        EventType.PLAN_SUBTASK_DISPATCHED.value, plan.plan_id,
        {"task_id": task.task_id, "index": s.index, "title": s.title,
         "prompt": s.prompt[:500]},
    ))
    log(logger, "info", "subtask dispatched",
        plan_id=plan.plan_id, subtask_index=s.index, task_id=task.task_id)
    return True


async def _activate_or_queue(store: TaskStore, plan: Plan) -> None:
    """Either activate the plan now or park it in the pending queue."""
    if await store.try_acquire_plan_lock(plan.plan_id):
        plan.status = PlanStatus.RUNNING.value
        await store.save_plan(plan)
        dispatched = await _dispatch_next_subtask(store, plan)
        if not dispatched:
            # Plan has no pending items — mark it complete immediately.
            await _mark_plan_complete(store, plan)
    else:
        plan.status = PlanStatus.QUEUED_TO_RUN.value
        await store.save_plan(plan)
        await store.enqueue_pending_plan(plan.plan_id)
        await store.log_event(plan.plan_id, make_entry(
            "plan_coordinator", "plan_queued",
            "Another plan is active; this plan is waiting in the queue",
        ))


async def _activate_next_pending(store: TaskStore) -> None:
    """Called when the active plan finishes. Start the next pending plan."""
    # Skip any pending plan ids that are no longer valid.
    while True:
        plan_id = await store.pop_pending_plan()
        if not plan_id:
            return
        plan = await store.get_plan(plan_id)
        if not plan:
            continue
        if plan.status not in (
            PlanStatus.QUEUED_TO_RUN.value,
            PlanStatus.AWAITING_APPROVAL.value,
        ):
            continue
        # Attempt to take the lock; if someone else already did, re-park.
        if await store.try_acquire_plan_lock(plan.plan_id):
            plan.status = PlanStatus.RUNNING.value
            await store.save_plan(plan)
            dispatched = await _dispatch_next_subtask(store, plan)
            if not dispatched:
                await _mark_plan_complete(store, plan)
            return
        await store.enqueue_pending_plan(plan.plan_id)
        return


async def _mark_plan_complete(store: TaskStore, plan: Plan) -> None:
    plan.status = PlanStatus.COMPLETE.value
    await store.save_plan(plan)
    await store.log_event(plan.plan_id, make_entry(
        "plan_coordinator", "plan_complete",
        f"Plan {plan.plan_id} complete — all sub-tasks merged",
    ))
    await store.publish(Event(
        EventType.PLAN_COMPLETE.value, plan.plan_id, {},
    ))
    await store.release_plan_lock(plan.plan_id)
    await _activate_next_pending(store)


async def _pause_plan(
    store: TaskStore, plan: Plan, reason: str, blocked_task_id: str,
) -> None:
    plan.status = PlanStatus.PAUSED.value
    plan.error = reason[:500]
    await store.save_plan(plan)
    await store.log_event(plan.plan_id, make_entry(
        "plan_coordinator", "plan_paused",
        f"Plan paused on sub-task {blocked_task_id}: {reason[:200]}",
        blocked_task_id=blocked_task_id, reason=reason[:500],
    ))
    await store.publish(Event(
        EventType.PLAN_PAUSED.value, plan.plan_id,
        {"blocked_task_id": blocked_task_id, "reason": reason[:1500]},
    ))
    # Keep the lock — a paused plan still owns main until the user resumes
    # or aborts. Other plans wait in pending.


async def _auto_approve_task(
    store: TaskStore, bot: Bot, task: Task,
) -> None:
    """Merge + deploy the task on behalf of the user (trust mode)."""
    if task.status != TaskStatus.AWAITING_REVIEW.value:
        return
    if not task.branch:
        return
    log(logger, "info", "auto-approving trust-mode task",
        task_id=task.task_id, health_score=task.health_score)
    try:
        git = GitManager(REPO_PATH)
        commit = git.merge_to_main(task.branch)
        task.status = TaskStatus.APPROVED.value
        task.commit_hash = commit
        await store.save(task)
        await store.log_event(task.task_id, make_entry(
            "human", "auto_approved",
            f"Auto-approved (trust mode, health {task.health_score}/100) "
            f"— merged {commit[:10]}",
            commit=commit[:10], auto=True,
        ))
        await store.publish(Event(
            EventType.MERGED.value, task.task_id, {"commit": commit[:10]},
        ))
    except Exception as exc:
        log(logger, "error", "auto-merge failed",
            task_id=task.task_id, error=str(exc))
        if task.telegram_chat_id is not None:
            await bot.send_message(
                chat_id=task.telegram_chat_id,
                text=(f"❌ Auto-merge failed for <code>{task.task_id}</code>"
                      f"\n<pre>{str(exc)[:400]}</pre>"),
                parse_mode=ParseMode.HTML,
            )
        return

    if not DEPLOY_PROD_CMD:
        return
    try:
        result = subprocess.run(
            DEPLOY_PROD_CMD, shell=True,
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            task.status = TaskStatus.DEPLOYED.value
            await store.save(task)
            await store.publish(Event(
                EventType.DEPLOY_PROD.value, task.task_id, {},
            ))
        else:
            if task.telegram_chat_id is not None:
                await bot.send_message(
                    chat_id=task.telegram_chat_id,
                    text=(f"⚠️ Auto-merged <code>{task.task_id}</code> but "
                          f"prod deploy failed:\n<pre>"
                          f"{result.stderr[:500]}</pre>"),
                    parse_mode=ParseMode.HTML,
                )
    except Exception as exc:
        log(logger, "error", "auto-deploy crashed",
            task_id=task.task_id, error=str(exc))


# ── Event handlers ─────────────────────────────────────────────


async def _on_plan_approved(store: TaskStore, event: Event) -> None:
    plan = await store.get_plan(event.task_id)
    if not plan:
        return
    if plan.status != PlanStatus.AWAITING_APPROVAL.value:
        return
    await _activate_or_queue(store, plan)


async def _on_plan_ready(store: TaskStore, event: Event) -> None:
    """After a re-plan the planner puts the plan back into state. Kick off
    the next sub-task if this was a re-plan mid-execution."""
    if not event.payload.get("replan", False):
        return
    plan = await store.get_plan(event.task_id)
    if not plan:
        return
    if plan.status != PlanStatus.RUNNING.value:
        return
    dispatched = await _dispatch_next_subtask(store, plan)
    if not dispatched:
        await _mark_plan_complete(store, plan)


async def _on_subtask_merged(store: TaskStore, event: Event) -> None:
    task = await store.get(event.task_id)
    if not task or not task.parent_plan_id:
        return
    plan = await store.get_plan(task.parent_plan_id)
    if not plan:
        return

    # Mark sub-task done in the plan's list.
    subtasks = plan.get_subtasks()
    for i, s in enumerate(subtasks):
        if s.task_id == task.task_id:
            s.status = "done"
            subtasks[i] = s
            break
    plan.set_subtasks(subtasks)
    await store.save_plan(plan)

    await store.log_event(plan.plan_id, make_entry(
        "plan_coordinator", "subtask_merged",
        f"Sub-task {task.task_id} merged to main",
        task_id=task.task_id, commit=event.payload.get("commit", ""),
    ))
    await store.publish(Event(
        EventType.PLAN_SUBTASK_COMPLETE.value, plan.plan_id,
        {"task_id": task.task_id, "commit": event.payload.get("commit", "")},
    ))

    # More work to do? Re-plan before dispatching next so the planner can
    # adapt to what actually landed on main.
    if any(s.status == "pending" for s in subtasks):
        await store.enqueue_planner(plan.plan_id)
    else:
        await _mark_plan_complete(store, plan)


async def _on_subtask_halted(
    store: TaskStore, event: Event, reason: str,
) -> None:
    task = await store.get(event.task_id)
    if not task or not task.parent_plan_id:
        return
    plan = await store.get_plan(task.parent_plan_id)
    if not plan:
        return
    # Mark sub-task failed in the plan list.
    subtasks = plan.get_subtasks()
    for i, s in enumerate(subtasks):
        if s.task_id == task.task_id:
            s.status = "failed"
            subtasks[i] = s
            break
    plan.set_subtasks(subtasks)
    await store.save_plan(plan)
    await _pause_plan(store, plan, reason, task.task_id)


async def _on_awaiting_review(
    store: TaskStore, bot: Bot, event: Event,
) -> None:
    task = await store.get(event.task_id)
    if not task:
        return
    if not task.auto_approve:
        return  # standard manual-review path
    if task.health_score is None:
        return
    if task.health_score < AUTO_APPROVE_MIN_HEALTH:
        # Trust mode enabled but health fell below threshold — fall back
        # to manual review. The AWAITING_REVIEW notification has already
        # been posted by the notifier; nothing more to do.
        log(logger, "info", "trust-mode task below health threshold, "
            "falling back to manual review",
            task_id=task.task_id, health_score=task.health_score,
            threshold=AUTO_APPROVE_MIN_HEALTH)
        return
    await _auto_approve_task(store, bot, task)


# ── Dispatcher loop ────────────────────────────────────────────


async def _dispatch(store: TaskStore, bot: Bot, event: Event) -> None:
    et = event.event_type
    if et == EventType.PLAN_APPROVED.value:
        await _on_plan_approved(store, event)
    elif et == EventType.PLAN_READY.value:
        await _on_plan_ready(store, event)
    elif et == EventType.MERGED.value:
        await _on_subtask_merged(store, event)
    elif et == EventType.AWAITING_REVIEW.value:
        await _on_awaiting_review(store, bot, event)
    elif et == EventType.REJECTED.value:
        await _on_subtask_halted(store, event, "user rejected the change")
    elif et == EventType.MANUAL_REVIEW.value:
        await _on_subtask_halted(
            store, event, "max feedback iterations reached",
        )
    elif et == EventType.DEV_ERROR.value:
        await _on_subtask_halted(
            store, event, f"dev error: {event.payload.get('error', '')[:200]}",
        )
    elif et == EventType.REVIEW_ERROR.value:
        await _on_subtask_halted(
            store, event,
            f"review error: {event.payload.get('error', '')[:200]}",
        )
    elif et == EventType.UI_TEST_ERROR.value:
        await _on_subtask_halted(
            store, event,
            f"ui-test error: {event.payload.get('error', '')[:200]}",
        )


async def run_coordinator_loop(bot: Bot, store: TaskStore) -> None:
    log(logger, "info", "plan coordinator started")
    async for event in store.subscribe():
        try:
            await _dispatch(store, bot, event)
        except Exception as exc:
            log(logger, "error", "plan coordinator event handler failed",
                event_type=event.event_type, task_id=event.task_id,
                error=str(exc))


# ── User-facing actions (called from bot/main.py) ──────────────


async def handle_plan_approve(
    store: TaskStore, plan_id: str, trust_mode: bool,
) -> Plan | None:
    """Called by the Telegram button handler when the user clicks Approve.

    Returns the updated plan or None if the plan can't be approved."""
    plan = await store.get_plan(plan_id)
    if not plan:
        return None
    if plan.status != PlanStatus.AWAITING_APPROVAL.value:
        return None
    plan.trust_mode = trust_mode
    plan.status = PlanStatus.AWAITING_APPROVAL.value  # still, until lock taken
    await store.save_plan(plan)
    await store.log_event(plan_id, make_entry(
        "human", "plan_approved",
        f"User approved the plan (trust_mode={trust_mode})",
        trust_mode=trust_mode,
    ))
    await store.publish(Event(
        EventType.PLAN_APPROVED.value, plan_id,
        {"trust_mode": trust_mode},
    ))
    return plan


async def handle_plan_reject(
    store: TaskStore, plan_id: str,
) -> Plan | None:
    plan = await store.get_plan(plan_id)
    if not plan:
        return None
    plan.status = PlanStatus.ABORTED.value
    await store.save_plan(plan)
    await store.log_event(plan_id, make_entry(
        "human", "plan_rejected", "User rejected the plan",
    ))
    await store.publish(Event(
        EventType.PLAN_REJECTED.value, plan_id, {},
    ))
    await store.release_plan_lock(plan_id)
    await _activate_next_pending(store)
    return plan


async def handle_plan_edit(
    store: TaskStore, plan_id: str, revision_note: str,
) -> Plan | None:
    """User replied to the plan message with a free-text revision. Stash
    the note in original_prompt and re-enqueue to the planner."""
    plan = await store.get_plan(plan_id)
    if not plan:
        return None
    if plan.status != PlanStatus.AWAITING_APPROVAL.value:
        return None
    plan.original_prompt = (
        f"{plan.original_prompt}\n\n"
        f"USER REVISION REQUEST:\n{revision_note}"
    )
    # Reset replan_count so the planner treats this like a fresh draft.
    plan.replan_count = 0
    plan.subtasks = []
    plan.status = PlanStatus.DRAFTING.value
    await store.save_plan(plan)
    await store.log_event(plan_id, make_entry(
        "human", "plan_edit_request",
        f"User requested edit: {revision_note[:200]}",
        revision=revision_note[:500],
    ))
    await store.enqueue_planner(plan_id)
    return plan


async def handle_plan_resume(
    store: TaskStore, plan_id: str,
) -> Plan | None:
    """User invoked /resume on a paused plan. Re-plan based on current
    main state, then continue with the next pending sub-task."""
    plan = await store.get_plan(plan_id)
    if not plan:
        return None
    if plan.status != PlanStatus.PAUSED.value:
        return None
    plan.status = PlanStatus.RUNNING.value
    plan.error = None
    # Any sub-task marked 'failed' becomes 'pending' again so the
    # planner can re-evaluate it in light of whatever the user fixed.
    subtasks = plan.get_subtasks()
    for i, s in enumerate(subtasks):
        if s.status == "failed":
            s.status = "pending"
            subtasks[i] = s
    plan.set_subtasks(subtasks)
    await store.save_plan(plan)
    await store.log_event(plan_id, make_entry(
        "human", "plan_resumed",
        "User resumed the plan — re-planning before next dispatch",
    ))
    await store.publish(Event(
        EventType.PLAN_RESUMED.value, plan_id, {},
    ))
    # Make sure we still hold the lock (we did — pause doesn't release it)
    # and queue a re-plan.
    await store.enqueue_planner(plan_id)
    return plan


async def handle_plan_abort(
    store: TaskStore, plan_id: str,
) -> Plan | None:
    plan = await store.get_plan(plan_id)
    if not plan:
        return None
    plan.status = PlanStatus.ABORTED.value
    await store.save_plan(plan)
    await store.log_event(plan_id, make_entry(
        "human", "plan_aborted", "User aborted the plan",
    ))
    await store.release_plan_lock(plan_id)
    await _activate_next_pending(store)
    return plan
