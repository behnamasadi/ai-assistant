"""Task, plan, and event data models shared across all services."""
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


class PlanStatus(str, Enum):
    """Lifecycle of a plan (a voice/text request split into sub-tasks)."""
    DRAFTING = "drafting"                      # planner agent is producing the split
    AWAITING_APPROVAL = "awaiting_approval"    # plan posted to Telegram, waiting on user
    QUEUED_TO_RUN = "queued_to_run"            # approved but another plan holds the lock
    RUNNING = "running"                        # a sub-task is in flight
    PAUSED = "paused"                          # sub-task failed → manual intervention required
    COMPLETE = "complete"                      # every sub-task merged
    ABORTED = "aborted"                        # user rejected plan or abandoned it
    ERROR = "error"                            # planner agent itself failed


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
    # Planner agent
    PLAN_CREATED = "PLAN_CREATED"
    PLAN_DRAFTING = "PLAN_DRAFTING"
    PLAN_READY = "PLAN_READY"
    PLAN_APPROVED = "PLAN_APPROVED"
    PLAN_REJECTED = "PLAN_REJECTED"
    PLAN_SUBTASK_DISPATCHED = "PLAN_SUBTASK_DISPATCHED"
    PLAN_SUBTASK_COMPLETE = "PLAN_SUBTASK_COMPLETE"
    PLAN_PAUSED = "PLAN_PAUSED"
    PLAN_RESUMED = "PLAN_RESUMED"
    PLAN_COMPLETE = "PLAN_COMPLETE"
    PLAN_ERROR = "PLAN_ERROR"
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

# Planner-related keys
PLAN_HASH_KEY = "plans:data"                # hash: plan_id -> json
PLANNER_QUEUE_KEY = "plans:queue"           # FIFO list of plan_ids for planner agent
PLAN_ACTIVE_KEY = "plans:active"            # single string: currently-running plan_id (lock)
PLAN_PENDING_KEY = "plans:pending"          # FIFO of plan_ids approved but waiting for the lock

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
    # Plan linkage. Populated when a task is a sub-task of a Plan. None for
    # one-off tasks published without going through the planner.
    parent_plan_id: str | None = None
    plan_index: int | None = None
    # When True, the plan coordinator auto-approves (merge + deploy) as soon as
    # gate 2 passes with a sufficient health score — set only in trust mode.
    auto_approve: bool = False

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

    @staticmethod
    def subtask(
        plan_id: str,
        index: int,
        prompt: str,
        **kwargs: Any,
    ) -> "Task":
        """Create a Task tied to a Plan. Task id encodes plan + index."""
        plan_short = plan_id[2:] if plan_id.startswith("p-") else plan_id
        task_id = f"t-{plan_short}-{index:02d}"
        return Task(
            task_id=task_id,
            prompt=prompt,
            parent_plan_id=plan_id,
            plan_index=index,
            **kwargs,
        )

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
class Subtask:
    """One item in a Plan's ordered sub-task list.

    Subtasks are the planner's plan-of-record. A Task row is created from a
    Subtask only at dispatch time — the planner may edit the list before then.
    """
    index: int
    title: str
    prompt: str
    depends_on: list[int] = field(default_factory=list)
    status: str = "pending"          # pending | dispatched | done | failed | skipped
    task_id: str | None = None       # populated when dispatched to dev queue

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Subtask":
        known = {f.name for f in Subtask.__dataclass_fields__.values()}
        return Subtask(**{k: v for k, v in d.items() if k in known})


@dataclass
class Plan:
    plan_id: str
    original_prompt: str
    status: str = PlanStatus.DRAFTING.value
    trust_mode: bool = False
    subtasks: list[dict] = field(default_factory=list)   # list[Subtask.asdict]
    current_index: int = 0              # index of the sub-task currently running / next to run
    planner_notes: str | None = None    # planner's reasoning / commentary
    replan_count: int = 0               # how many times the planner has re-run
    telegram_user_id: int | None = None
    telegram_chat_id: int | None = None
    telegram_message_id: int | None = None
    plan_message_id: int | None = None  # id of the "here is the plan" message (for edits)
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    error: str | None = None

    @staticmethod
    def new(original_prompt: str, **kwargs: Any) -> "Plan":
        return Plan(
            plan_id=f"p-{uuid.uuid4().hex[:12]}",
            original_prompt=original_prompt,
            **kwargs,
        )

    def get_subtasks(self) -> list[Subtask]:
        return [Subtask.from_dict(d) for d in self.subtasks]

    def set_subtasks(self, subtasks: list[Subtask]) -> None:
        self.subtasks = [asdict(s) for s in subtasks]

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str | bytes) -> "Plan":
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        known = {f.name for f in Plan.__dataclass_fields__.values()}
        data = {k: v for k, v in data.items() if k in known}
        return Plan(**data)

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
