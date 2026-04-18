"""Planner agent worker: pops plans, splits prompts into sub-tasks using Claude.

Handles both initial drafts (plan has no sub-tasks yet) and re-plans
(called after a sub-task merges to main, so the planner can adapt the
remaining items based on what actually landed).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query
from dotenv import load_dotenv

from shared.event_log import make_entry
from shared.logger import get_logger, log
from shared.redis_client import TaskStore
from shared.task_schema import (
    Event,
    EventType,
    Plan,
    PlanStatus,
    Subtask,
)

load_dotenv()

logger = get_logger("planner_agent")

REPO_PATH = os.environ.get("GIT_REPO_PATH", "/workspace/project")
PROMPT_PATH = Path(__file__).parent / "prompts" / "planner.md"
TIMEOUT = int(os.environ.get("PLANNER_AGENT_TIMEOUT_SECONDS", "600"))


def _load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _build_user_prompt(plan: Plan) -> str:
    parts = [
        f"PLAN ID: {plan.plan_id}",
        f"ORIGINAL REQUEST:\n{plan.original_prompt}",
    ]
    existing = plan.get_subtasks()
    done = [s for s in existing if s.status == "done"]
    remaining = [s for s in existing if s.status not in ("done", "skipped")]

    if done:
        rows = []
        for s in done:
            rows.append(
                f"  [{s.index}] {s.title}\n"
                f"      task_id: {s.task_id}\n"
                f"      prompt: {s.prompt[:300]}"
            )
        parts.append("DONE (already merged to main):\n" + "\n".join(rows))

    if remaining:
        rows = []
        for s in remaining:
            rows.append(
                f"  [{s.index}] {s.title} (status: {s.status})\n"
                f"      prompt: {s.prompt[:300]}"
            )
        parts.append("REMAINING (current draft — revise as needed):\n"
                     + "\n".join(rows))

    if plan.replan_count == 0:
        parts.append(
            "This is the INITIAL plan. Explore the repo to understand the "
            "codebase, then decide whether to split. Return JSON as specified."
        )
    else:
        parts.append(
            f"This is RE-PLAN #{plan.replan_count}. A sub-task has just "
            "merged. Look at the current state of main (git log / git diff) "
            "and revise the REMAINING list. Drop stale items, rewrite prompts "
            "whose context shifted, and set complete=true if the goal is "
            "already achieved."
        )
    return "\n\n".join(parts)


async def _run_claude(user_prompt: str, system_prompt: str) -> str:
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=["Read", "Glob", "Grep", "Bash"],
        cwd=REPO_PATH,
        permission_mode="default",
    )
    parts: list[str] = []
    async for message in query(prompt=user_prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
    return "\n".join(parts).strip()


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json_block(text: str) -> dict[str, Any] | None:
    """Find the LAST fenced JSON object in the agent response."""
    matches = _JSON_BLOCK_RE.findall(text)
    if not matches:
        # Fallback: try to locate a bare top-level object
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            matches = [stripped]
        else:
            return None
    for block in reversed(matches):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue
    return None


def _merge_subtasks(
    existing: list[Subtask],
    proposed: list[dict[str, Any]],
) -> list[Subtask]:
    """Merge planner output with the already-dispatched sub-tasks.

    Sub-tasks with status "done" or "dispatched" are immutable — the
    planner can only revise pending items. New items from the planner
    fill any indices not already taken by immutable items.
    """
    locked = {
        s.index: s for s in existing
        if s.status in ("done", "dispatched", "skipped", "failed")
    }
    result: list[Subtask] = list(locked.values())

    used_indices = set(locked.keys())
    next_free = 0

    def _next_index() -> int:
        nonlocal next_free
        while next_free in used_indices:
            next_free += 1
        used_indices.add(next_free)
        return next_free

    for raw in proposed:
        idx = raw.get("index")
        if idx in locked:
            # Planner tried to revise an immutable slot; ignore.
            continue
        if idx is None or idx in used_indices:
            idx = _next_index()
        else:
            used_indices.add(idx)
        result.append(Subtask(
            index=idx,
            title=str(raw.get("title", "")).strip() or f"Sub-task {idx}",
            prompt=str(raw.get("prompt", "")).strip(),
            depends_on=list(raw.get("depends_on") or []),
            status="pending",
        ))
    result.sort(key=lambda s: s.index)
    return result


async def _process_plan(store: TaskStore, plan: Plan) -> None:
    plan.status = PlanStatus.DRAFTING.value
    plan.replan_count += 1 if plan.subtasks else 0
    await store.save_plan(plan)
    await store.publish(Event(
        EventType.PLAN_DRAFTING.value, plan.plan_id,
        {"replan_count": plan.replan_count},
    ))
    await store.log_event(plan.plan_id, make_entry(
        "planner", "planner_started",
        f"Planner working on {plan.plan_id} "
        f"(pass #{plan.replan_count + 1})",
        replan_count=plan.replan_count,
    ))

    system_prompt = _load_system_prompt()
    user_prompt = _build_user_prompt(plan)

    log(logger, "info", "running planner", plan_id=plan.plan_id,
        replan_count=plan.replan_count)
    response = await asyncio.wait_for(
        _run_claude(user_prompt, system_prompt),
        timeout=TIMEOUT,
    )

    parsed = _extract_json_block(response)
    if not parsed:
        raise RuntimeError(
            "planner did not produce a parseable JSON block; "
            f"response head: {response[:300]}"
        )

    plan.planner_notes = response[-4000:]

    if not parsed.get("split", False):
        reason = str(parsed.get("reason", "")).strip() or "no split needed"
        # Wrap the original prompt as a single sub-task so downstream flow
        # is uniform for both split and non-split plans.
        sole = Subtask(
            index=0,
            title="Implement the request",
            prompt=plan.original_prompt,
            status="pending",
        )
        plan.set_subtasks([sole])
        plan.status = PlanStatus.AWAITING_APPROVAL.value
        await store.save_plan(plan)
        await store.log_event(plan.plan_id, make_entry(
            "planner", "planner_no_split",
            f"Planner decided not to split: {reason}",
            reason=reason,
        ))
        await store.publish(Event(
            EventType.PLAN_READY.value, plan.plan_id,
            {"split": False, "reason": reason, "subtasks": [asdict(sole)]},
        ))
        return

    if parsed.get("complete", False):
        # Re-plan concluded the goal is already merged.
        plan.status = PlanStatus.COMPLETE.value
        await store.save_plan(plan)
        await store.log_event(plan.plan_id, make_entry(
            "planner", "planner_complete",
            "Planner declared the plan complete on re-plan",
        ))
        await store.publish(Event(
            EventType.PLAN_COMPLETE.value, plan.plan_id,
            {"reason": "re-plan concluded goal already achieved"},
        ))
        # Release lock if we held it — coordinator will pick the next plan.
        await store.release_plan_lock(plan.plan_id)
        return

    raw_subtasks = parsed.get("subtasks") or []
    if not isinstance(raw_subtasks, list) or not raw_subtasks:
        raise RuntimeError("planner returned split=true but no subtasks")

    merged = _merge_subtasks(plan.get_subtasks(), raw_subtasks)
    plan.set_subtasks(merged)

    # On the first pass, plan goes to awaiting_approval. On re-plans the
    # plan is already RUNNING — the coordinator will dispatch the next
    # pending sub-task itself.
    if plan.replan_count == 0:
        plan.status = PlanStatus.AWAITING_APPROVAL.value
    # else keep current status (RUNNING), coordinator picks next item.

    await store.save_plan(plan)
    await store.log_event(plan.plan_id, make_entry(
        "planner", "planner_produced_split",
        f"Planner produced {len(merged)} sub-task(s) "
        f"({sum(1 for s in merged if s.status == 'pending')} pending)",
        reasoning=str(parsed.get("reasoning", ""))[:500],
        count=len(merged),
    ))
    await store.publish(Event(
        EventType.PLAN_READY.value, plan.plan_id,
        {
            "split": True,
            "reasoning": str(parsed.get("reasoning", ""))[:1500],
            "subtasks": [asdict(s) for s in merged],
            "replan": plan.replan_count > 0,
        },
    ))


async def _worker_loop() -> None:
    store = TaskStore()
    log(logger, "info", "planner agent started", repo=REPO_PATH)
    try:
        while True:
            plan_id = await store.pop_planner(timeout=0)
            if not plan_id:
                continue
            plan = await store.get_plan(plan_id)
            if not plan:
                log(logger, "error", "plan not found", plan_id=plan_id)
                continue
            try:
                await _process_plan(store, plan)
            except Exception as exc:
                log(logger, "error", "planner failed",
                    plan_id=plan_id, error=str(exc))
                plan.status = PlanStatus.ERROR.value
                plan.error = str(exc)
                await store.save_plan(plan)
                await store.log_event(plan_id, make_entry(
                    "planner", "planner_error",
                    f"Planner failed: {str(exc)[:200]}",
                ))
                await store.publish(Event(
                    EventType.PLAN_ERROR.value, plan_id,
                    {"error": str(exc)[:1500]},
                ))
                # Release lock if held, to let other plans through.
                await store.release_plan_lock(plan_id)
    finally:
        await store.close()


def main() -> None:
    asyncio.run(_worker_loop())


if __name__ == "__main__":
    main()
