"""Lightweight web dashboard for task monitoring and management.

Run alongside the Telegram bot on port 8095. Shares the same Redis store.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

from shared.event_log import make_entry
from shared.git_manager import GitManager
from shared.redis_client import TaskStore
from shared.task_schema import Event, EventType, Task, TaskStatus

load_dotenv()

app = FastAPI(title="AI Assistant Dashboard")

REPO_PATH = os.environ.get("GIT_REPO_PATH", "/workspace/project")
DEPLOY_PROD_CMD = os.environ.get("DEPLOY_PROD_COMMAND", "")
REGRESSION_DATA_DIR = os.environ.get("REGRESSION_DATA_DIR", "")

_store: TaskStore | None = None


def _get_store() -> TaskStore:
    global _store
    if _store is None:
        _store = TaskStore()
    return _store


# ── API endpoints ──────────────────────────────────────────


@app.get("/api/dashboard/status")
async def api_status():
    store = _get_store()
    queues = await store.queue_lengths()
    tasks = await store.get_all_tasks()
    by_status: dict[str, int] = {}
    for t in tasks:
        by_status[t.status] = by_status.get(t.status, 0) + 1
    return {"queues": queues, "total_tasks": len(tasks), "by_status": by_status}


@app.get("/api/dashboard/tasks")
async def api_tasks():
    store = _get_store()
    tasks = await store.get_all_tasks()
    tasks.sort(key=lambda t: t.created_at, reverse=True)
    return [asdict(t) for t in tasks]


@app.get("/api/dashboard/tasks/{task_id}")
async def api_task_detail(task_id: str):
    store = _get_store()
    task = await store.get(task_id)
    if not task:
        raise HTTPException(404, f"Task {task_id} not found")
    return asdict(task)


@app.get("/api/dashboard/tasks/{task_id}/events")
async def api_task_events(task_id: str):
    store = _get_store()
    events = await store.get_events(task_id)
    return [asdict(e) for e in events]


@app.get("/api/dashboard/events/stream")
async def api_event_stream(request: Request):
    """SSE endpoint — streams real-time events from Redis pub/sub."""
    store = _get_store()

    async def _generate():
        pubsub = store.r.pubsub()
        await pubsub.subscribe("events")
        try:
            while True:
                if await request.is_disconnected():
                    break
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0,
                )
                if msg and msg.get("type") == "message":
                    yield {"event": "agent_event", "data": msg["data"]}
                else:
                    # Send keepalive comment every ~1s to detect disconnects
                    yield {"event": "ping", "data": ""}
        finally:
            await pubsub.unsubscribe("events")
            await pubsub.aclose()

    return EventSourceResponse(_generate())


@app.post("/api/dashboard/tasks/{task_id}/approve")
async def api_approve(task_id: str):
    store = _get_store()
    task = await store.get(task_id)
    if not task:
        raise HTTPException(404, f"Task {task_id} not found")
    if task.status != TaskStatus.AWAITING_REVIEW.value:
        raise HTTPException(400, f"Task is {task.status}, not awaiting_review")

    git = GitManager(REPO_PATH)
    commit = git.merge_to_main(task.branch)
    task.status = TaskStatus.APPROVED.value
    task.commit_hash = commit
    await store.save(task)
    await store.log_event(task_id, make_entry(
        "human", "approved",
        f"Human approved and merged — commit {commit[:10]}",
        commit=commit[:10],
    ))
    await store.publish(Event(EventType.MERGED.value, task_id,
                              {"commit": commit[:10]}))

    result = subprocess.run(DEPLOY_PROD_CMD, shell=True,
                            capture_output=True, text=True, timeout=300)
    if result.returncode == 0:
        task.status = TaskStatus.DEPLOYED.value
        await store.save(task)
        await store.publish(Event(EventType.DEPLOY_PROD.value, task_id, {}))
        return {"status": "deployed", "commit": commit[:10]}
    else:
        return {"status": "merged_but_deploy_failed", "commit": commit[:10],
                "error": result.stderr[:500]}


@app.post("/api/dashboard/tasks/{task_id}/reject")
async def api_reject(task_id: str):
    store = _get_store()
    task = await store.get(task_id)
    if not task:
        raise HTTPException(404, f"Task {task_id} not found")
    if task.status != TaskStatus.AWAITING_REVIEW.value:
        raise HTTPException(400, f"Task is {task.status}, not awaiting_review")

    task.status = TaskStatus.REJECTED.value
    await store.save(task)
    await store.log_event(task_id, make_entry(
        "human", "rejected", "Human rejected the task",
    ))
    await store.publish(Event(EventType.REJECTED.value, task_id, {}))
    return {"status": "rejected"}


@app.delete("/api/dashboard/tasks/{task_id}")
async def api_delete_task(task_id: str):
    store = _get_store()
    task = await store.get(task_id)
    if not task:
        raise HTTPException(404, f"Task {task_id} not found")
    await store.delete(task_id)
    return {"status": "deleted", "task_id": task_id}


@app.post("/api/dashboard/tasks/clear-failed")
async def api_clear_failed():
    store = _get_store()
    tasks = await store.get_all_tasks()
    deleted = []
    for t in tasks:
        if t.status in (TaskStatus.FAILED.value, TaskStatus.REJECTED.value):
            await store.delete(t.task_id)
            deleted.append(t.task_id)
    return {"status": "ok", "deleted": deleted, "count": len(deleted)}


@app.post("/api/dashboard/tasks/clear-all")
async def api_clear_all():
    store = _get_store()
    tasks = await store.get_all_tasks()
    for t in tasks:
        await store.delete(t.task_id)
    return {"status": "ok", "count": len(tasks)}


# ── Regression tests API ──────────────────────────────────


@app.get("/api/dashboard/regression")
async def api_regression():
    """Return regression test data: baselines, results, history."""
    reg_root = Path(REGRESSION_DATA_DIR)
    baselines_dir = reg_root / "baselines"
    results_dir = reg_root / "results"
    history_dir = reg_root / "history"

    datasets = {}
    if baselines_dir.is_dir():
        for f in sorted(baselines_dir.glob("*.json")):
            ds = f.stem
            datasets.setdefault(ds, {})
            datasets[ds]["baseline"] = json.loads(f.read_text())
    if results_dir.is_dir():
        for f in sorted(results_dir.glob("*.json")):
            ds = f.stem
            datasets.setdefault(ds, {})
            datasets[ds]["latest"] = json.loads(f.read_text())
    if history_dir.is_dir():
        for f in sorted(history_dir.glob("*.jsonl")):
            ds = f.stem
            datasets.setdefault(ds, {})
            runs = []
            for line in f.read_text().splitlines():
                line = line.strip()
                if line:
                    runs.append(json.loads(line))
            datasets[ds]["history"] = runs

    return {"datasets": datasets}


# ── HTML dashboard ─────────────────────────────────────────

_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Assistant Dashboard</title>
<style>
  :root {
    --bg: #0f172a; --surface: #1e293b; --border: #334155;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #3b82f6;
    --green: #22c55e; --yellow: #eab308; --red: #ef4444;
    --orange: #f97316; --purple: #a855f7;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.5;
  }
  .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
  header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border);
  }
  header h1 { font-size: 1.5rem; font-weight: 600; }
  #auto-refresh { color: var(--muted); font-size: 0.85rem; }

  .stats {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px; margin-bottom: 24px;
  }
  .stat-card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px; text-align: center;
  }
  .stat-card .value { font-size: 2rem; font-weight: 700; }
  .stat-card .label { font-size: 0.8rem; color: var(--muted); text-transform: uppercase; }

  .task-list { display: flex; flex-direction: column; gap: 12px; }

  .task-card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px; cursor: pointer;
    transition: border-color 0.2s;
  }
  .task-card:hover { border-color: var(--accent); }
  .task-card.expanded .task-details { display: block; }

  .task-header {
    display: flex; justify-content: space-between; align-items: center;
    flex-wrap: wrap; gap: 8px;
  }
  .task-id { font-family: monospace; font-size: 0.9rem; color: var(--accent); }
  .task-prompt {
    flex: 1; margin: 0 16px; font-size: 0.9rem;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    max-width: 500px;
  }
  .task-time { font-size: 0.8rem; color: var(--muted); }

  .badge {
    display: inline-block; padding: 2px 10px; border-radius: 12px;
    font-size: 0.75rem; font-weight: 600; text-transform: uppercase;
  }
  .badge-queued { background: #334155; color: var(--muted); }
  .badge-dev_in_progress { background: #1e3a5f; color: #60a5fa; }
  .badge-dev_done { background: #14532d; color: var(--green); }
  .badge-review_in_progress { background: #422006; color: var(--orange); }
  .badge-review_done { background: #365314; color: #a3e635; }
  .badge-ui_test_in_progress { background: #422006; color: var(--orange); }
  .badge-awaiting_review { background: #3b0764; color: var(--purple); }
  .badge-approved { background: #14532d; color: var(--green); }
  .badge-deployed { background: #052e16; color: #4ade80; }
  .badge-rejected { background: #450a0a; color: var(--red); }
  .badge-failed { background: #450a0a; color: var(--red); }
  .badge-needs_manual_review { background: #451a03; color: var(--yellow); }

  .health-badge {
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 0.75rem; font-weight: 700; margin-left: 8px;
  }
  .health-good { background: #14532d; color: var(--green); }
  .health-warn { background: #422006; color: var(--yellow); }
  .health-bad { background: #450a0a; color: var(--red); }

  .timeline { margin-top: 12px; padding-left: 20px; border-left: 2px solid var(--border); }
  .tl-entry { position: relative; padding: 8px 0 8px 16px; font-size: 0.83rem; }
  .tl-entry::before {
    content: ''; position: absolute; left: -7px; top: 14px;
    width: 12px; height: 12px; border-radius: 50%; border: 2px solid var(--border);
    background: var(--surface);
  }
  .tl-entry.agent-developer::before { border-color: #60a5fa; background: #1e3a5f; }
  .tl-entry.agent-code_reviewer::before { border-color: var(--orange); background: #422006; }
  .tl-entry.agent-ui_tester::before { border-color: var(--purple); background: #3b0764; }
  .tl-entry.agent-human::before { border-color: var(--green); background: #14532d; }
  .tl-agent { font-weight: 600; text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.05em; }
  .tl-agent.developer { color: #60a5fa; }
  .tl-agent.code_reviewer { color: var(--orange); }
  .tl-agent.ui_tester { color: var(--purple); }
  .tl-agent.human { color: var(--green); }
  .tl-summary { color: var(--text); }
  .tl-time { color: var(--muted); font-size: 0.75rem; }
  .tl-detail { color: var(--muted); font-size: 0.78rem; margin-top: 2px; }
  .tl-toggle { color: var(--accent); cursor: pointer; font-size: 0.78rem; border: none; background: none; padding: 0; }
  .tl-feedback { display: none; background: var(--bg); padding: 6px 8px; border-radius: 4px; font-size: 0.78rem; margin-top: 4px; white-space: pre-wrap; max-height: 200px; overflow-y: auto; }

  .task-details {
    display: none; margin-top: 16px; padding-top: 16px;
    border-top: 1px solid var(--border);
  }
  .detail-grid {
    display: grid; grid-template-columns: 120px 1fr; gap: 4px 12px;
    font-size: 0.85rem;
  }
  .detail-label { color: var(--muted); font-weight: 500; }
  .detail-value { word-break: break-word; }
  .detail-value pre {
    background: var(--bg); padding: 8px; border-radius: 4px;
    overflow-x: auto; font-size: 0.8rem; margin-top: 4px;
    white-space: pre-wrap;
  }

  .actions { margin-top: 12px; display: flex; gap: 8px; }
  .btn {
    padding: 8px 20px; border: none; border-radius: 6px;
    font-size: 0.85rem; font-weight: 600; cursor: pointer;
  }
  .btn-approve { background: var(--green); color: #000; }
  .btn-reject { background: var(--red); color: #fff; }
  .btn-approve:hover { opacity: 0.9; }
  .btn-reject:hover { opacity: 0.9; }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }

  .filter-bar {
    display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap;
  }
  .filter-btn {
    padding: 4px 12px; border: 1px solid var(--border); border-radius: 16px;
    background: transparent; color: var(--muted); font-size: 0.8rem;
    cursor: pointer;
  }
  .filter-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }

  .empty { text-align: center; color: var(--muted); padding: 40px; }

  .toolbox {
    display: flex; gap: 8px; margin-bottom: 20px; flex-wrap: wrap;
    padding: 12px 16px; background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; align-items: center;
  }
  .toolbox-label {
    font-size: 0.85rem; font-weight: 600; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.05em; margin-right: 8px;
  }
  .btn-tool {
    padding: 6px 14px; border: 1px solid var(--border); border-radius: 6px;
    background: transparent; color: var(--text); font-size: 0.8rem;
    cursor: pointer; transition: all 0.2s;
  }
  .btn-tool:hover { background: var(--border); }
  .btn-tool.danger { border-color: var(--red); color: var(--red); }
  .btn-tool.danger:hover { background: var(--red); color: #fff; }
  .btn-delete {
    padding: 4px 10px; border: 1px solid var(--border); border-radius: 4px;
    background: transparent; color: var(--muted); font-size: 0.75rem;
    cursor: pointer; margin-left: 8px;
  }
  .btn-delete:hover { background: var(--red); color: #fff; border-color: var(--red); }

  /* Tab navigation */
  .tabs { display: flex; gap: 0; margin-bottom: 24px; border-bottom: 2px solid var(--border); }
  .tab-btn {
    padding: 10px 24px; background: transparent; border: none; border-bottom: 2px solid transparent;
    color: var(--muted); font-size: 0.95rem; font-weight: 600; cursor: pointer;
    margin-bottom: -2px; transition: all 0.2s;
  }
  .tab-btn:hover { color: var(--text); }
  .tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }
  .tab-content { display: none; }
  .tab-content.active { display: block; }

  /* Regression dashboard */
  .reg-card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px; margin-bottom: 16px;
  }
  .reg-card-header {
    display: flex; align-items: center; gap: 12px; margin-bottom: 12px;
  }
  .reg-ds-name { font-size: 1.1rem; font-weight: 700; }
  .reg-badge {
    display: inline-block; padding: 2px 10px; border-radius: 10px;
    font-size: 0.75rem; font-weight: 700; color: #fff;
  }
  .reg-badge.pass { background: var(--green); }
  .reg-badge.fail { background: var(--red); }
  .reg-time { font-size: 0.8rem; color: var(--muted); margin-left: auto; }
  .reg-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  .reg-table th {
    text-align: left; padding: 6px 10px; font-size: 0.75rem;
    text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted);
    border-bottom: 1px solid var(--border);
  }
  .reg-table td { padding: 6px 10px; border-bottom: 1px solid var(--border); }
  .reg-metric { font-weight: 500; }
  .reg-val { font-family: monospace; }
  .reg-change { font-family: monospace; font-size: 0.8rem; }
  .reg-change.pass { color: var(--green); }
  .reg-change.fail { color: var(--red); }
  .reg-change.neutral { color: var(--muted); }
  .reg-status { font-size: 1.1rem; text-align: center; }
  .reg-status.pass { color: var(--green); }
  .reg-status.fail { color: var(--red); }
  .reg-runs { font-size: 0.8rem; color: var(--muted); margin-top: 8px; }
  .reg-info { color: var(--muted); font-size: 0.85rem; margin-bottom: 16px; }
  .reg-info code {
    background: var(--surface); padding: 2px 6px; border-radius: 3px;
    font-size: 0.8rem;
  }
  .reg-empty { color: var(--muted); font-style: italic; padding: 40px; text-align: center; }

  /* Live connection indicator */
  .conn-status { display: flex; align-items: center; gap: 6px; font-size: 0.82rem; }
  .conn-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--muted); transition: background 0.3s;
  }
  .conn-dot.live { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .conn-dot.dead { background: var(--red); }
  .conn-label { color: var(--muted); }
  .conn-label.live { color: var(--green); }

  /* Toast notification for real-time events */
  .toast-container {
    position: fixed; bottom: 20px; right: 20px; z-index: 1000;
    display: flex; flex-direction: column-reverse; gap: 8px; max-width: 380px;
  }
  .toast {
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    padding: 10px 14px; font-size: 0.82rem; color: var(--text);
    box-shadow: 0 4px 12px rgba(0,0,0,0.4); animation: slideIn 0.3s ease-out;
    display: flex; align-items: center; gap: 8px;
  }
  .toast .agent-dot {
    width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  }
  .toast .agent-dot.developer { background: #60a5fa; }
  .toast .agent-dot.code_reviewer { background: var(--orange); }
  .toast .agent-dot.ui_tester { background: var(--purple); }
  .toast .agent-dot.human { background: var(--green); }
  @keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>AI Assistant Dashboard</h1>
    <div class="conn-status">
      <span class="conn-dot" id="conn-dot"></span>
      <span class="conn-label" id="conn-label">Connecting...</span>
    </div>
  </header>
  <div class="toast-container" id="toasts"></div>

  <div class="tabs">
    <button class="tab-btn active" data-tab="tasks">Tasks</button>
    <button class="tab-btn" data-tab="regression">Regression Tests</button>
  </div>

  <div class="tab-content active" id="tab-tasks">
  <div class="stats" id="stats"></div>

  <div class="toolbox">
    <span class="toolbox-label">Toolbox</span>
    <button class="btn-tool" onclick="clearFailed()">Clear Failed &amp; Rejected</button>
    <button class="btn-tool danger" onclick="clearAll()">Clear All Tasks</button>
  </div>

  <div class="filter-bar" id="filters">
    <button class="filter-btn active" data-filter="all">All</button>
    <button class="filter-btn" data-filter="active">Active</button>
    <button class="filter-btn" data-filter="awaiting_review">Awaiting Review</button>
    <button class="filter-btn" data-filter="deployed">Deployed</button>
    <button class="filter-btn" data-filter="failed">Failed</button>
  </div>

  <div class="task-list" id="task-list"></div>
  </div>

  <div class="tab-content" id="tab-regression">
    <div class="reg-info">
      Run tests: <code>python scripts/test_regression.py</code>
      &nbsp;&middot;&nbsp;
      Save baselines: <code>python scripts/test_regression.py --save-baseline</code>
    </div>
    <div id="reg-content"><div class="reg-empty">Loading...</div></div>
  </div>
</div>

<script>
(function() {
  let tasks = [];
  let currentFilter = 'all';

  function fmtDate(ts) {
    if (!ts) return '-';
    const d = new Date(ts * 1000);
    return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
  }

  function elapsed(ts) {
    if (!ts) return '';
    const s = Math.floor(Date.now()/1000 - ts);
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.floor(s/60) + 'm ago';
    if (s < 86400) return Math.floor(s/3600) + 'h ago';
    return Math.floor(s/86400) + 'd ago';
  }

  const ACTIVE = ['queued','dev_in_progress','dev_done','review_in_progress','review_done','ui_test_in_progress','awaiting_review'];

  function filterTasks(list) {
    if (currentFilter === 'all') return list;
    if (currentFilter === 'active') return list.filter(t => ACTIVE.includes(t.status));
    return list.filter(t => t.status === currentFilter);
  }

  async function loadStatus() {
    try {
      const r = await fetch('/api/dashboard/status');
      const d = await r.json();
      document.getElementById('stats').innerHTML =
        '<div class="stat-card"><div class="value">' + d.total_tasks + '</div><div class="label">Total Tasks</div></div>' +
        '<div class="stat-card"><div class="value">' + d.queues.dev_queue + '</div><div class="label">Dev Queue</div></div>' +
        '<div class="stat-card"><div class="value">' + d.queues.review_queue + '</div><div class="label">Review Queue</div></div>' +
        '<div class="stat-card"><div class="value">' + d.queues.ui_test_queue + '</div><div class="label">UI Test Queue</div></div>' +
        '<div class="stat-card"><div class="value">' + (d.by_status.awaiting_review||0) + '</div><div class="label">Awaiting Review</div></div>' +
        '<div class="stat-card"><div class="value">' + (d.by_status.deployed||0) + '</div><div class="label">Deployed</div></div>' +
        '<div class="stat-card"><div class="value">' + (d.by_status.failed||0) + '</div><div class="label">Failed</div></div>';
    } catch(e) {}
  }

  async function loadTasks() {
    try {
      const r = await fetch('/api/dashboard/tasks');
      tasks = await r.json();
      renderTasks();
    } catch(e) {}
  }

  function renderTasks() {
    const filtered = filterTasks(tasks);
    const el = document.getElementById('task-list');
    if (!filtered.length) {
      el.innerHTML = '<div class="empty">No tasks found</div>';
      return;
    }
    el.innerHTML = filtered.map(t => {
      const prompt = (t.prompt||'').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      const promptShort = prompt.length > 80 ? prompt.slice(0,80) + '...' : prompt;
      const reviewActions = t.status === 'awaiting_review'
        ? '<button class="btn btn-approve" onclick="event.stopPropagation();approveTask(\\'' + t.task_id + '\\')">Approve & Deploy</button>' +
          '<button class="btn btn-reject" onclick="event.stopPropagation();rejectTask(\\'' + t.task_id + '\\')">Reject</button>'
        : '';
      const actions = '<div class="actions">' + reviewActions +
          '<button class="btn-delete" onclick="event.stopPropagation();deleteTask(\\'' + t.task_id + '\\')">Delete</button>' +
          '</div>';
      const devSummary = t.dev_summary
        ? '<div class="detail-label">Dev Summary</div><div class="detail-value"><pre>' + esc(t.dev_summary) + '</pre></div>' : '';
      const reviewFb = t.review_feedback
        ? '<div class="detail-label">Code Review</div><div class="detail-value"><pre>' + esc(t.review_feedback) + '</pre></div>' : '';
      const uiTestFb = t.ui_test_feedback
        ? '<div class="detail-label">UI Test</div><div class="detail-value"><pre>' + esc(t.ui_test_feedback) + '</pre></div>' : '';
      const error = t.error
        ? '<div class="detail-label">Error</div><div class="detail-value"><pre style="color:var(--red)">' + esc(t.error) + '</pre></div>' : '';
      // Health score badge
      let healthBadge = '';
      if (t.health_score != null) {
        const hc = t.health_score >= 70 ? 'health-good' : t.health_score >= 40 ? 'health-warn' : 'health-bad';
        healthBadge = '<span class="health-badge ' + hc + '">Health: ' + t.health_score + '</span>';
      }

      return '<div class="task-card" onclick="toggleCard(this,\\'' + t.task_id + '\\')">' +
        '<div class="task-header">' +
          '<span class="task-id">' + t.task_id + '</span>' +
          '<span class="task-prompt" title="' + prompt.replace(/"/g,'&quot;') + '">' + promptShort + '</span>' +
          '<span class="badge badge-' + t.status + '">' + t.status.replace(/_/g,' ') + '</span>' +
          healthBadge +
          '<span class="task-time">' + elapsed(t.updated_at) + '</span>' +
        '</div>' +
        '<div class="task-details">' +
          '<div class="detail-grid">' +
            '<div class="detail-label">Prompt</div><div class="detail-value">' + prompt + '</div>' +
            '<div class="detail-label">Branch</div><div class="detail-value"><code>' + (t.branch||'none') + '</code></div>' +
            '<div class="detail-label">Iteration</div><div class="detail-value">' + t.iteration + '</div>' +
            '<div class="detail-label">Commit</div><div class="detail-value"><code>' + (t.commit_hash ? t.commit_hash.slice(0,10) : 'none') + '</code></div>' +
            '<div class="detail-label">Created</div><div class="detail-value">' + fmtDate(t.created_at) + '</div>' +
            '<div class="detail-label">Updated</div><div class="detail-value">' + fmtDate(t.updated_at) + '</div>' +
            devSummary + reviewFb + uiTestFb + error +
          '</div>' +
          '<div class="detail-label" style="margin-top:12px">Agent Timeline</div>' +
          '<div class="timeline" id="tl-' + t.task_id + '"></div>' +
          actions +
        '</div>' +
      '</div>';
    }).join('');
  }

  function esc(s) {
    return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  window.approveTask = async function(tid) {
    if (!confirm('Approve and deploy ' + tid + ' to production?')) return;
    const btn = event.target;
    btn.disabled = true; btn.textContent = 'Deploying...';
    try {
      const r = await fetch('/api/dashboard/tasks/' + tid + '/approve', {method:'POST'});
      const d = await r.json();
      if (r.ok) { alert('Deployed! Commit: ' + (d.commit||'')); }
      else { alert('Error: ' + (d.detail||r.statusText)); }
    } catch(e) { alert('Failed: ' + e); }
    loadAll();
  };

  window.rejectTask = async function(tid) {
    if (!confirm('Reject ' + tid + '?')) return;
    try {
      const r = await fetch('/api/dashboard/tasks/' + tid + '/reject', {method:'POST'});
      if (!r.ok) { const d = await r.json(); alert('Error: ' + (d.detail||r.statusText)); }
    } catch(e) { alert('Failed: ' + e); }
    loadAll();
  };

  window.deleteTask = async function(tid) {
    if (!confirm('Delete task ' + tid + '? This cannot be undone.')) return;
    try {
      const r = await fetch('/api/dashboard/tasks/' + tid, {method:'DELETE'});
      if (!r.ok) { const d = await r.json(); alert('Error: ' + (d.detail||r.statusText)); }
    } catch(e) { alert('Failed: ' + e); }
    loadAll();
  };

  window.clearFailed = async function() {
    if (!confirm('Delete all failed and rejected tasks?')) return;
    try {
      const r = await fetch('/api/dashboard/tasks/clear-failed', {method:'POST'});
      const d = await r.json();
      alert('Cleared ' + d.count + ' tasks');
    } catch(e) { alert('Failed: ' + e); }
    loadAll();
  };

  window.clearAll = async function() {
    if (!confirm('DELETE ALL TASKS? This cannot be undone!')) return;
    try {
      const r = await fetch('/api/dashboard/tasks/clear-all', {method:'POST'});
      const d = await r.json();
      alert('Cleared ' + d.count + ' tasks');
    } catch(e) { alert('Failed: ' + e); }
    loadAll();
  };

  // Tabs
  document.querySelector('.tabs').addEventListener('click', function(e) {
    const btn = e.target.closest('.tab-btn');
    if (!btn) return;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'regression') loadRegression();
  });

  // Regression dashboard
  const REG_METRICS = [
    {key:'num_registered_images', label:'Registered Images', unit:'', higher:true, fmt:v=>String(v)},
    {key:'num_points3D', label:'3D Points', unit:'', higher:true, fmt:v=>v.toLocaleString()},
    {key:'mean_reprojection_error', label:'Reproj. Error', unit:'px', higher:false, fmt:v=>v.toFixed(4)},
    {key:'duration_seconds', label:'Duration', unit:'s', higher:false, fmt:v=>v.toFixed(1)},
  ];
  const REG_THRESHOLDS = {
    num_registered_images:-0.10, num_points3D:-0.15,
    mean_reprojection_error:0.30, duration_seconds:0.50
  };

  function regCheck(metric, cur, base) {
    if (!base || base===0) return 'neutral';
    const th = REG_THRESHOLDS[metric];
    if (th===undefined) return 'neutral';
    if (th<0) return cur < base*(1+th) ? 'fail' : 'pass';
    return cur > base*(1+th) ? 'fail' : 'pass';
  }

  function regPct(cur, base) {
    if (!base || base===0) return '';
    const pct = ((cur-base)/base)*100;
    return (pct>=0?'+':'') + pct.toFixed(1) + '%';
  }

  function sparkline(history, key, higher) {
    if (!history || history.length<2) return '';
    const vals = history.map(r=>r[key]||0);
    const mn = Math.min(...vals), mx = Math.max(...vals), rng = mx-mn||1;
    const w=160,h=32,p=2;
    const pts = vals.map((v,i)=>{
      const x = p+(i/(vals.length-1))*(w-2*p);
      const y = p+(1-(v-mn)/rng)*(h-2*p);
      return x.toFixed(1)+','+y.toFixed(1);
    }).join(' ');
    const first=vals[0], last=vals[vals.length-1];
    const ok = higher ? last>=first : last<=first;
    const col = ok ? '#22c55e' : '#ef4444';
    const lastPt = pts.split(' ').pop().split(',');
    return '<svg width="'+w+'" height="'+h+'" style="vertical-align:middle">' +
      '<polyline points="'+pts+'" fill="none" stroke="'+col+'" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round"/>' +
      '<circle cx="'+lastPt[0]+'" cy="'+lastPt[1]+'" r="3" fill="'+col+'"/></svg>';
  }

  async function loadRegression() {
    try {
      const r = await fetch('/api/dashboard/regression');
      const data = await r.json();
      const el = document.getElementById('reg-content');
      const datasets = data.datasets||{};
      const names = Object.keys(datasets).sort();
      if (!names.length) {
        el.innerHTML = '<div class="reg-empty">No regression data yet.</div>';
        return;
      }
      let html = '';
      names.forEach(ds => {
        const d = datasets[ds];
        const baseline = d.baseline||{};
        const latest = d.latest||baseline;
        const history = d.history||[];
        const isPass = latest.status==='completed';
        html += '<div class="reg-card">';
        html += '<div class="reg-card-header">';
        html += '<span class="reg-ds-name">'+esc(ds)+'</span>';
        html += '<span class="reg-badge '+(isPass?'pass':'fail')+'">'+(isPass?'PASS':'FAIL')+'</span>';
        if (latest.timestamp) html += '<span class="reg-time">'+esc(latest.timestamp)+'</span>';
        html += '</div>';
        html += '<table class="reg-table"><tr><th>Metric</th><th>Baseline</th><th>Latest</th>' +
          '<th>Change</th><th>Status</th><th>Trend</th></tr>';
        REG_METRICS.forEach(m => {
          const bv=baseline[m.key]||0, lv=latest[m.key]||0;
          const st = regCheck(m.key, lv, bv);
          const icon = st==='pass'?'&#10003;':st==='fail'?'&#10007;':'&mdash;';
          html += '<tr>';
          html += '<td class="reg-metric">'+esc(m.label)+'</td>';
          html += '<td class="reg-val">'+m.fmt(bv)+(m.unit?' '+m.unit:'')+'</td>';
          html += '<td class="reg-val">'+m.fmt(lv)+(m.unit?' '+m.unit:'')+'</td>';
          html += '<td class="reg-change '+st+'">'+regPct(lv,bv)+'</td>';
          html += '<td class="reg-status '+st+'">'+icon+'</td>';
          html += '<td>'+sparkline(history,m.key,m.higher)+'</td>';
          html += '</tr>';
        });
        html += '</table>';
        html += '<div class="reg-runs">'+history.length+' run'+(history.length!==1?'s':'')+' recorded</div>';
        html += '</div>';
      });
      el.innerHTML = html;
    } catch(e) {
      document.getElementById('reg-content').innerHTML =
        '<div class="reg-empty">Failed to load regression data</div>';
    }
  }

  // Filters
  document.getElementById('filters').addEventListener('click', function(e) {
    const btn = e.target.closest('.filter-btn');
    if (!btn) return;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentFilter = btn.dataset.filter;
    renderTasks();
  });

  window.toggleCard = function(el, tid) {
    const wasExpanded = el.classList.contains('expanded');
    el.classList.toggle('expanded');
    if (!wasExpanded) loadTimeline(tid);
  };

  window.loadTimeline = async function(tid) {
    const el = document.getElementById('tl-' + tid);
    if (!el) return;
    el.innerHTML = '<span class="tl-time">Loading...</span>';
    try {
      const r = await fetch('/api/dashboard/tasks/' + tid + '/events');
      const events = await r.json();
      if (!events.length) {
        el.innerHTML = '<span class="tl-time">No events recorded yet</span>';
        return;
      }
      el.innerHTML = events.map((ev, i) => {
        const d = new Date(ev.ts * 1000);
        const time = d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});
        const date = d.toLocaleDateString();
        const agent = ev.agent || 'system';
        const fb = ev.detail && ev.detail.feedback ? ev.detail.feedback : '';
        const fbHtml = fb
          ? '<button class="tl-toggle" onclick="event.stopPropagation();var d=this.nextElementSibling;d.style.display=d.style.display===\\'block\\'?\\'none\\':\\'block\\'">show detail</button><div class="tl-feedback">' + esc(fb) + '</div>'
          : '';
        return '<div class="tl-entry agent-' + agent + '">' +
          '<span class="tl-agent ' + agent + '">' + agent.replace(/_/g,' ') + '</span> ' +
          '<span class="tl-time">' + date + ' ' + time + '</span><br>' +
          '<span class="tl-summary">' + esc(ev.summary) + '</span>' +
          fbHtml +
        '</div>';
      }).join('');
    } catch(e) {
      el.innerHTML = '<span class="tl-time">Failed to load timeline</span>';
    }
  };

  function loadAll() { loadStatus(); loadTasks(); }

  // ── SSE real-time updates with polling fallback ───────────
  const dot = document.getElementById('conn-dot');
  const lbl = document.getElementById('conn-label');
  const toastBox = document.getElementById('toasts');
  let sseConnected = false;
  let pollTimer = null;

  function setConn(live) {
    sseConnected = live;
    dot.className = 'conn-dot ' + (live ? 'live' : 'dead');
    lbl.className = 'conn-label ' + (live ? 'live' : '');
    lbl.textContent = live ? 'Live' : 'Polling (30s)';
  }

  function showToast(agent, text) {
    const t = document.createElement('div');
    t.className = 'toast';
    t.innerHTML = '<span class="agent-dot ' + (agent||'') + '"></span>' + esc(text);
    toastBox.appendChild(t);
    setTimeout(() => t.remove(), 6000);
    // Keep max 5 toasts
    while (toastBox.children.length > 5) toastBox.firstChild.remove();
  }

  const EVENT_LABELS = {
    TASK_CREATED: 'New task created',
    DEV_STARTED: 'Developer started',
    DEV_COMPLETE: 'Developer finished',
    DEV_ERROR: 'Developer error',
    REVIEW_STARTED: 'Code review started',
    REVIEW_PASSED: 'Code review passed',
    REVIEW_FEEDBACK: 'Code review feedback',
    REVIEW_ERROR: 'Code review error',
    UI_TEST_STARTED: 'UI testing started',
    UI_TEST_PASSED: 'UI tests passed',
    UI_TEST_FEEDBACK: 'UI test feedback',
    UI_TEST_ERROR: 'UI test error',
    AWAITING_REVIEW: 'Ready for human review',
    MERGED: 'Merged to main',
    DEPLOY_PROD: 'Deployed to production',
    REJECTED: 'Rejected',
  };

  function agentFromEvent(type) {
    if (type.startsWith('DEV_')) return 'developer';
    if (type.startsWith('REVIEW_')) return 'code_reviewer';
    if (type.startsWith('UI_TEST_')) return 'ui_tester';
    return 'human';
  }

  function connectSSE() {
    const es = new EventSource('/api/dashboard/events/stream');
    es.addEventListener('agent_event', function(e) {
      try {
        const ev = JSON.parse(e.data);
        const label = EVENT_LABELS[ev.event_type] || ev.event_type;
        const agent = agentFromEvent(ev.event_type);
        showToast(agent, ev.task_id.slice(0,14) + ': ' + label);
        // Refresh data on any real event
        loadAll();
      } catch(_) {}
    });
    es.onopen = function() {
      setConn(true);
      // Reduce polling when SSE is connected
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(loadAll, 60000);
    };
    es.onerror = function() {
      setConn(false);
      es.close();
      // Fall back to faster polling
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(loadAll, 30000);
      // Retry SSE after 5s
      setTimeout(connectSSE, 5000);
    };
  }

  // Initial load, then start SSE
  loadAll();
  pollTimer = setInterval(loadAll, 30000);
  connectSSE();
})();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return _DASHBOARD_HTML


def main() -> None:
    import uvicorn
    port = int(os.environ.get("DASHBOARD_PORT", "8095"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
