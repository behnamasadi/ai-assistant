"""Async Redis helper: task queues, plan lifecycle, and event bus."""
from __future__ import annotations

import os
from typing import AsyncIterator

import redis.asyncio as redis

from .event_log import EventEntry, event_log_key
from .task_schema import (
    EVENT_CHANNEL,
    Event,
    PLAN_ACTIVE_KEY,
    PLAN_HASH_KEY,
    PLAN_PENDING_KEY,
    PLANNER_QUEUE_KEY,
    Plan,
    REVIEW_QUEUE_KEY,
    UI_TEST_QUEUE_KEY,
    TASK_HASH_KEY,
    TASK_QUEUE_KEY,
    Task,
)


def _url() -> str:
    host = os.environ.get("REDIS_HOST", "redis")
    port = os.environ.get("REDIS_PORT", "6379")
    password = os.environ.get("REDIS_PASSWORD", "")
    auth = f":{password}@" if password else ""
    return f"redis://{auth}{host}:{port}/0"


def get_client() -> redis.Redis:
    return redis.from_url(_url(), decode_responses=True)


class TaskStore:
    """Persistent task + plan state, queues, events over one Redis conn."""

    def __init__(self, client: redis.Redis | None = None) -> None:
        self.r = client or get_client()

    async def close(self) -> None:
        await self.r.aclose()

    # ── Task state ──────────────────────────────────────────────
    async def save(self, task: Task) -> None:
        task.touch()
        await self.r.hset(TASK_HASH_KEY, task.task_id, task.to_json())

    async def get(self, task_id: str) -> Task | None:
        raw = await self.r.hget(TASK_HASH_KEY, task_id)
        return Task.from_json(raw) if raw else None

    async def get_all_tasks(self) -> list[Task]:
        raw_map = await self.r.hgetall(TASK_HASH_KEY)
        return [Task.from_json(v) for v in raw_map.values()]

    async def delete(self, task_id: str) -> None:
        await self.r.hdel(TASK_HASH_KEY, task_id)
        await self.delete_events(task_id)

    async def queue_lengths(self) -> dict[str, int]:
        dev = await self.r.llen(TASK_QUEUE_KEY)
        review = await self.r.llen(REVIEW_QUEUE_KEY)
        ui_test = await self.r.llen(UI_TEST_QUEUE_KEY)
        planner = await self.r.llen(PLANNER_QUEUE_KEY)
        return {
            "dev_queue": dev,
            "review_queue": review,
            "ui_test_queue": ui_test,
            "planner_queue": planner,
        }

    # ── Task queues ─────────────────────────────────────────────
    async def enqueue_dev(self, task_id: str) -> None:
        await self.r.rpush(TASK_QUEUE_KEY, task_id)

    async def enqueue_review(self, task_id: str) -> None:
        await self.r.rpush(REVIEW_QUEUE_KEY, task_id)

    async def enqueue_ui_test(self, task_id: str) -> None:
        await self.r.rpush(UI_TEST_QUEUE_KEY, task_id)

    async def pop_dev(self, timeout: int = 0) -> str | None:
        result = await self.r.blpop(TASK_QUEUE_KEY, timeout=timeout)
        return result[1] if result else None

    async def pop_review(self, timeout: int = 0) -> str | None:
        result = await self.r.blpop(REVIEW_QUEUE_KEY, timeout=timeout)
        return result[1] if result else None

    async def pop_ui_test(self, timeout: int = 0) -> str | None:
        result = await self.r.blpop(UI_TEST_QUEUE_KEY, timeout=timeout)
        return result[1] if result else None

    # Legacy aliases
    async def enqueue_qa(self, task_id: str) -> None:
        await self.enqueue_review(task_id)

    async def pop_qa(self, timeout: int = 0) -> str | None:
        return await self.pop_review(timeout=timeout)

    # ── Plan state ──────────────────────────────────────────────
    async def save_plan(self, plan: Plan) -> None:
        plan.touch()
        await self.r.hset(PLAN_HASH_KEY, plan.plan_id, plan.to_json())

    async def get_plan(self, plan_id: str) -> Plan | None:
        raw = await self.r.hget(PLAN_HASH_KEY, plan_id)
        return Plan.from_json(raw) if raw else None

    async def get_all_plans(self) -> list[Plan]:
        raw_map = await self.r.hgetall(PLAN_HASH_KEY)
        return [Plan.from_json(v) for v in raw_map.values()]

    async def delete_plan(self, plan_id: str) -> None:
        await self.r.hdel(PLAN_HASH_KEY, plan_id)

    # ── Planner queue ──────────────────────────────────────────
    async def enqueue_planner(self, plan_id: str) -> None:
        """Schedule a plan for planner work (initial draft or re-plan)."""
        await self.r.rpush(PLANNER_QUEUE_KEY, plan_id)

    async def pop_planner(self, timeout: int = 0) -> str | None:
        result = await self.r.blpop(PLANNER_QUEUE_KEY, timeout=timeout)
        return result[1] if result else None

    # ── Active-plan lock ───────────────────────────────────────
    async def get_active_plan_id(self) -> str | None:
        val = await self.r.get(PLAN_ACTIVE_KEY)
        return val if val else None

    async def try_acquire_plan_lock(self, plan_id: str) -> bool:
        """Atomically claim the active-plan slot. Returns True if acquired."""
        return bool(await self.r.set(PLAN_ACTIVE_KEY, plan_id, nx=True))

    async def release_plan_lock(self, plan_id: str) -> None:
        """Release the lock only if the caller currently holds it."""
        current = await self.r.get(PLAN_ACTIVE_KEY)
        if current == plan_id:
            await self.r.delete(PLAN_ACTIVE_KEY)

    # ── Pending-plan queue (waiting for the lock) ──────────────
    async def enqueue_pending_plan(self, plan_id: str) -> None:
        await self.r.rpush(PLAN_PENDING_KEY, plan_id)

    async def pop_pending_plan(self) -> str | None:
        return await self.r.lpop(PLAN_PENDING_KEY)

    async def pending_plans(self) -> list[str]:
        return await self.r.lrange(PLAN_PENDING_KEY, 0, -1)

    # ── Persistent event log ──────────────────────────────────
    async def log_event(self, task_id: str, entry: EventEntry) -> None:
        """Append an event to the task's (or plan's) persistent timeline."""
        await self.r.rpush(event_log_key(task_id), entry.to_json())

    async def get_events(self, task_id: str) -> list[EventEntry]:
        """Return the full event timeline for a task or plan."""
        raw_list = await self.r.lrange(event_log_key(task_id), 0, -1)
        return [EventEntry.from_json(r) for r in raw_list]

    async def delete_events(self, task_id: str) -> None:
        """Delete the event log for a task or plan."""
        await self.r.delete(event_log_key(task_id))

    # ── Event bus ───────────────────────────────────────────────
    async def publish(self, event: Event) -> None:
        await self.r.publish(EVENT_CHANNEL, event.to_json())

    async def subscribe(self) -> AsyncIterator[Event]:
        pubsub = self.r.pubsub()
        await pubsub.subscribe(EVENT_CHANNEL)
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                yield Event.from_json(msg["data"])
        finally:
            await pubsub.unsubscribe(EVENT_CHANNEL)
            await pubsub.aclose()
