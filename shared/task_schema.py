"""Task and event data models shared across all services."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    QUEUED = "queued"
    DEV_IN_PROGRESS = "dev_in_progress"
    DEV_DONE = "dev_done"
    QA_IN_PROGRESS = "qa_in_progress"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    DEPLOYED = "deployed"
    REJECTED = "rejected"
    FAILED = "failed"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"


class EventType(str, Enum):
    TASK_CREATED = "TASK_CREATED"
    DEV_STARTED = "DEV_STARTED"
    DEV_COMPLETE = "DEV_COMPLETE"
    DEV_ERROR = "DEV_ERROR"
    QA_STARTED = "QA_STARTED"
    QA_APPROVED = "QA_APPROVED"
    QA_FEEDBACK = "QA_FEEDBACK"
    QA_ERROR = "QA_ERROR"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    MERGED = "MERGED"
    DEPLOY_PROD = "DEPLOY_PROD"
    REJECTED = "REJECTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


# Redis key namespaces
TASK_QUEUE_KEY = "tasks:queue"          # FIFO list of task_ids for dev agent
QA_QUEUE_KEY = "tasks:qa_queue"         # FIFO list of task_ids for qa agent
TASK_HASH_KEY = "tasks:data"            # hash: task_id -> json
EVENT_CHANNEL = "events"                # pub/sub channel for all events


def _now() -> float:
    return time.time()


@dataclass
class Task:
    task_id: str
    prompt: str
    telegram_user_id: int | None = None
    telegram_chat_id: int | None = None
    telegram_message_id: int | None = None
    branch: str | None = None
    status: str = TaskStatus.QUEUED.value
    iteration: int = 0
    dev_summary: str | None = None
    qa_feedback: str | None = None
    commit_hash: str | None = None
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    error: str | None = None

    @staticmethod
    def new(prompt: str, **kwargs: Any) -> "Task":
        return Task(task_id=f"t-{uuid.uuid4().hex[:12]}", prompt=prompt, **kwargs)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str | bytes) -> "Task":
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return Task(**json.loads(raw))

    def touch(self) -> None:
        self.updated_at = _now()


@dataclass
class Event:
    event_type: str
    task_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=_now)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str | bytes) -> "Event":
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return Event(**json.loads(raw))
