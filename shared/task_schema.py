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
    REVIEW_IN_PROGRESS = "review_in_progress"
    REVIEW_DONE = "review_done"
    UI_TEST_IN_PROGRESS = "ui_test_in_progress"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    DEPLOYED = "deployed"
    REJECTED = "rejected"
    FAILED = "failed"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"


class EventType(str, Enum):
    TASK_CREATED = "TASK_CREATED"
    # Developer agent
    DEV_STARTED = "DEV_STARTED"
    DEV_COMPLETE = "DEV_COMPLETE"
    DEV_ERROR = "DEV_ERROR"
    # Code reviewer agent
    REVIEW_STARTED = "REVIEW_STARTED"
    REVIEW_PASSED = "REVIEW_PASSED"
    REVIEW_FEEDBACK = "REVIEW_FEEDBACK"
    REVIEW_ERROR = "REVIEW_ERROR"
    # UI tester agent
    UI_TEST_STARTED = "UI_TEST_STARTED"
    UI_TEST_PASSED = "UI_TEST_PASSED"
    UI_TEST_FEEDBACK = "UI_TEST_FEEDBACK"
    UI_TEST_ERROR = "UI_TEST_ERROR"
    # Human review
    AWAITING_REVIEW = "AWAITING_REVIEW"
    MERGED = "MERGED"
    DEPLOY_PROD = "DEPLOY_PROD"
    REJECTED = "REJECTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    # Legacy aliases (keep for backwards compat with any stored events)
    QA_STARTED = "QA_STARTED"
    QA_APPROVED = "QA_APPROVED"
    QA_FEEDBACK = "QA_FEEDBACK"
    QA_ERROR = "QA_ERROR"


# Redis key namespaces
TASK_QUEUE_KEY = "tasks:queue"              # FIFO list of task_ids for dev agent
REVIEW_QUEUE_KEY = "tasks:review_queue"     # FIFO list of task_ids for code reviewer
UI_TEST_QUEUE_KEY = "tasks:ui_test_queue"   # FIFO list of task_ids for UI tester
TASK_HASH_KEY = "tasks:data"                # hash: task_id -> json
EVENT_CHANNEL = "events"                    # pub/sub channel for all events

# Legacy alias
QA_QUEUE_KEY = REVIEW_QUEUE_KEY


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
    review_feedback: str | None = None
    ui_test_feedback: str | None = None
    commit_hash: str | None = None
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    error: str | None = None
    health_score: int | None = None

    # Legacy alias for backwards compat with stored tasks
    @property
    def qa_feedback(self) -> str | None:
        return self.review_feedback or self.ui_test_feedback

    @qa_feedback.setter
    def qa_feedback(self, value: str | None) -> None:
        self.review_feedback = value

    @staticmethod
    def new(prompt: str, **kwargs: Any) -> "Task":
        return Task(task_id=f"t-{uuid.uuid4().hex[:12]}", prompt=prompt, **kwargs)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str | bytes) -> "Task":
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        # Handle legacy tasks that have qa_feedback instead of review_feedback
        if "qa_feedback" in data and "review_feedback" not in data:
            data["review_feedback"] = data.pop("qa_feedback")
        # Drop unknown fields from old tasks
        known = {f.name for f in Task.__dataclass_fields__.values()}
        data = {k: v for k, v in data.items() if k in known}
        return Task(**data)

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
