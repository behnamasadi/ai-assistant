"""Create tasks and plans from user input; push to the right queue."""
from __future__ import annotations

from shared.event_log import make_entry
from shared.redis_client import TaskStore
from shared.task_schema import Event, EventType, Plan, Task, TaskStatus


async def publish_task(
    store: TaskStore,
    prompt: str,
    telegram_user_id: int,
    telegram_chat_id: int,
    telegram_message_id: int,
) -> Task:
    """One-off task that bypasses the planner (used for retries, tests)."""
    task = Task.new(
        prompt=prompt,
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
    )
    task.status = TaskStatus.QUEUED.value
    await store.save(task)
    await store.enqueue_dev(task.task_id)
    await store.log_event(task.task_id, make_entry(
        "human", "task_created",
        f"Task created: {prompt[:100]}",
        prompt=prompt[:500],
    ))
    await store.publish(
        Event(
            event_type=EventType.TASK_CREATED.value,
            task_id=task.task_id,
            payload={"prompt": prompt},
        )
    )
    return task


async def publish_plan(
    store: TaskStore,
    prompt: str,
    telegram_user_id: int,
    telegram_chat_id: int,
    telegram_message_id: int,
) -> Plan:
    """Normal entry point: wrap the request in a Plan and queue it for the
    planner. The planner decides whether to split and, on approval, the
    coordinator dispatches sub-tasks sequentially."""
    plan = Plan.new(
        original_prompt=prompt,
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
    )
    await store.save_plan(plan)
    await store.enqueue_planner(plan.plan_id)
    await store.log_event(plan.plan_id, make_entry(
        "human", "plan_created",
        f"Plan created: {prompt[:100]}",
        prompt=prompt[:500],
    ))
    await store.publish(Event(
        event_type=EventType.PLAN_CREATED.value,
        task_id=plan.plan_id,
        payload={"prompt": prompt},
    ))
    return plan
