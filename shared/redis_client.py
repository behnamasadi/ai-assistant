"""Async Redis helper: task queue, task state, and event bus."""
from __future__ import annotations

import os
from typing import AsyncIterator

import redis.asyncio as redis

from .task_schema import (
    EVENT_CHANNEL,
    Event,
    QA_QUEUE_KEY,
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
    """Persistent task state + queues + event bus over a single Redis connection."""

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

    # ── Queues ──────────────────────────────────────────────────
    async def enqueue_dev(self, task_id: str) -> None:
        await self.r.rpush(TASK_QUEUE_KEY, task_id)

    async def enqueue_qa(self, task_id: str) -> None:
        await self.r.rpush(QA_QUEUE_KEY, task_id)

    async def pop_dev(self, timeout: int = 0) -> str | None:
        result = await self.r.blpop(TASK_QUEUE_KEY, timeout=timeout)
        return result[1] if result else None

    async def pop_qa(self, timeout: int = 0) -> str | None:
        result = await self.r.blpop(QA_QUEUE_KEY, timeout=timeout)
        return result[1] if result else None

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
