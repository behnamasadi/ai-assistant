"""Create tasks from user input and push them onto the dev agent queue."""
from __future__ import annotations

from shared.redis_client import TaskStore
from shared.task_schema import Event, EventType, Task, TaskStatus


async def publish_task(
    store: TaskStore,
    prompt: str,
    telegram_user_id: int,
    telegram_chat_id: int,
    telegram_message_id: int,
) -> Task:
    task = Task.new(
        prompt=prompt,
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
    )
    task.status = TaskStatus.QUEUED.value
    await store.save(task)
    await store.enqueue_dev(task.task_id)
    await store.publish(
        Event(
            event_type=EventType.TASK_CREATED.value,
            task_id=task.task_id,
            payload={"prompt": prompt},
        )
    )
    return task
