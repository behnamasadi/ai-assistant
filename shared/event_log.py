"""Persistent event log per task — stored as Redis lists, unlike fire-and-forget pub/sub.

Each task gets a Redis list at ``task:{task_id}:events`` containing JSON-encoded
event entries.  This provides a full timeline of agent interactions that can be
queried after the fact (unlike pub/sub which is ephemeral).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class EventEntry:
    """One entry in a task's persistent event timeline."""
    ts: float
    agent: str          # "developer", "code_reviewer", "ui_tester", "human"
    event_type: str     # e.g. "code_pushed", "review_verdict", "test_verdict"
    summary: str        # human-readable one-liner
    detail: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str | bytes) -> "EventEntry":
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return EventEntry(**json.loads(raw))


def event_log_key(task_id: str) -> str:
    """Redis key for a task's persistent event log."""
    return f"task:{task_id}:events"


def make_entry(
    agent: str,
    event_type: str,
    summary: str,
    **detail: Any,
) -> EventEntry:
    return EventEntry(
        ts=time.time(),
        agent=agent,
        event_type=event_type,
        summary=summary,
        detail=detail,
    )
