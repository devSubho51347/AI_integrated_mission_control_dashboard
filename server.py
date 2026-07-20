#!/usr/bin/env python3
"""Small SQLite-backed server for the Agent Dashboard."""

from __future__ import annotations

import argparse
import calendar
import copy
import json
import mimetypes
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


APP_DIR = Path(__file__).resolve().parent
INDEX_PATH = APP_DIR / "index.html"
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "dashboard.db"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3000
OPENCLAW_DIR = Path.home() / ".openclaw"
OPENCLAW_CONFIG_PATH = OPENCLAW_DIR / "openclaw.json"
AGENT_LOGS_DB_PATH = OPENCLAW_DIR / "agent-logs.db"
EXCLUDED_DASHBOARD_AGENTS = {"main", "research-agent", "linkedin_trend_scraper"}
AGENT_COLORS = [
    "var(--blue)",
    "var(--purple)",
    "var(--pink)",
    "var(--yellow)",
    "var(--green)",
    "var(--red)",
    "var(--cyan)",
]
NOTE_COLOR_MIN = 0
NOTE_COLOR_MAX = 5
NOTE_SEED_NAME = "sticky_notes_v1"
PRODUCTIVITY_SEED_NAME = "productivity_items_v1"
TASK_SEED_NAME = "dashboard_tasks_v1"
NOTE_SEEDS = [
    ("Prepare the weekly project update before Friday standup.", 0),
    ("Review dashboard walkthrough notes and tighten the demo script.", 1),
    ("Follow up on automation ideas from the productivity planning session.", 2),
    ("Block one focused hour for inbox cleanup and task triage.", 3),
]
PRODUCTIVITY_SCOPES = {"monthly", "weekly", "default"}
TASK_CATEGORIES = {"Work", "Marketing", "Development", "Personal"}
TASK_PRIORITIES = {"Urgent", "Normal", "Someday"}
TASK_STATUSES = {"todo", "in_progress", "done"}
TASK_SEEDS = [
    {
        "title": "Audit OpenClaw gateway reconnect logs",
        "category": "Development",
        "priority": "Urgent",
        "status": "todo",
        "due_date": "2026-07-22",
        "notes": "Check recent disconnect patterns and capture any recurring error messages.",
    },
    {
        "title": "Outline weekly creator update",
        "category": "Marketing",
        "priority": "Normal",
        "status": "todo",
        "due_date": "2026-07-24",
        "notes": "Draft the key wins, shipped dashboard changes, and next experiments.",
    },
    {
        "title": "Plan focused workspace cleanup",
        "category": "Personal",
        "priority": "Someday",
        "status": "todo",
        "due_date": None,
        "notes": "Group loose notes and remove stale scratch files when there is a quiet block.",
    },
    {
        "title": "Record dashboard walkthrough clips",
        "category": "Marketing",
        "priority": "Urgent",
        "status": "in_progress",
        "due_date": "2026-07-21",
        "notes": "Capture Tasks, Productivity, and agent activity sections separately.",
    },
    {
        "title": "Wire live task filters into the Kanban board",
        "category": "Development",
        "priority": "Normal",
        "status": "in_progress",
        "due_date": "2026-07-23",
        "notes": "Keep the existing layout and use category filtering from the API.",
    },
    {
        "title": "Review sponsor outreach shortlist",
        "category": "Work",
        "priority": "Normal",
        "status": "in_progress",
        "due_date": "2026-07-26",
        "notes": "Prioritize the best fit contacts before writing personalized intros.",
    },
    {
        "title": "Ship persistent productivity goals API",
        "category": "Development",
        "priority": "Urgent",
        "status": "done",
        "due_date": "2026-07-20",
        "notes": "Backend, UI wiring, tests, and GitHub backup are complete.",
    },
    {
        "title": "Triage old inbox action items",
        "category": "Personal",
        "priority": "Normal",
        "status": "done",
        "due_date": "2026-07-19",
        "notes": "Cleared stale items and left only follow-ups that still matter.",
    },
]


DEFAULT_BOOTSTRAP = {
    "pages": ["Dashboard", "Content", "Agents", "Tasks", "Chat", "Productivity", "Monitor"],
    "metrics": [
        ["Active Agents", "6", "online now", "orange"],
        ["Agent Tasks", "7", "completed today", "blue"],
        ["To-Dos Open", "8", "in progress + todo", "green"],
        ["Uptime", "7d 0h", "since boot", "purple"],
    ],
    "agents": [
        {
            "name": "Orchestrator",
            "role": "Role not defined locally",
            "icon": "+",
            "color": "var(--blue)",
            "tasks": 12,
            "success": 97,
            "model": "openai-codex/gpt-5.4",
            "active": "3 min ago",
        },
        {
            "name": "Bill",
            "role": "Discord Operations",
            "icon": "B",
            "color": "var(--purple)",
            "tasks": 8,
            "success": 94,
            "model": "minimax/MiniMax-M2.7",
            "active": "18 min ago",
        },
        {
            "name": "Scout",
            "role": "Research & Intelligence",
            "icon": "S",
            "color": "var(--pink)",
            "tasks": 15,
            "success": 98,
            "model": "openai-codex/gpt-5.4",
            "active": "1 min ago",
        },
        {
            "name": "Scribe",
            "role": "Writing & Content Shaping",
            "icon": "Sc",
            "color": "var(--yellow)",
            "tasks": 11,
            "success": 96,
            "model": "minimax/MiniMax-M2.7",
            "active": "5 min ago",
        },
        {
            "name": "Reach",
            "role": "Marketing & Growth",
            "icon": "R",
            "color": "var(--green)",
            "tasks": 6,
            "success": 91,
            "model": "openai-codex/gpt-5.4",
            "active": "31 min ago",
        },
        {
            "name": "Dev",
            "role": "Development & Automation",
            "icon": "D",
            "color": "var(--red)",
            "tasks": 9,
            "success": 95,
            "model": "minimax/MiniMax-M2.7",
            "active": "8 min ago",
        },
    ],
    "activity": {
        "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "total": [50, 56, 44, 64, 70, 36, 61],
        "completed": [48, 54, 42, 61, 68, 35, 58],
        "events": [
            ["Scout", "Morning competitor brief - 8 channels scanned, 5 top picks selected and ranked", "Completed", "1 min ago"],
            ["Orchestrator", "Routed incoming Discord task to Dev and Scribe with context packet attached", "Completed", "3 min ago"],
            ["Scribe", "Drafted full community update post - 620 words, three sections, ready for review", "Completed", "5 min ago"],
        ],
    },
    "tasks": {
        "todo": [
            ["Audit OpenClaw gateway logs for reconnect patterns", "Work", "Urgent", "7d overdue"],
            ["Write YouTube title A/B variants for next 3 uploads", "Marketing", "Normal", "5d overdue"],
            ["Draft outreach email to 5 top AI newsletter sponsors", "Marketing", "Normal", "4d overdue"],
            ["Plan community Discord event for next weekend", "Work", "Someday", ""],
        ],
        "progress": [
            ["Record walkthrough video of the Mission Control dashboard", "Marketing", "Urgent", "6d overdue"],
            ["Review and merge Dev's mobile responsiveness PR", "Development", "Urgent", "7d overdue"],
            ["Set up Tailscale exit-node fallback for VPS failover", "Development", "Normal", "5d overdue"],
        ],
        "done": [
            ["Full mobile responsiveness audit and fix pass", "Development", "Urgent", ""],
            ["Configure nginx reverse proxy on Tailscale IP", "Development", "Normal", ""],
            ["Implement Model Assignment card in Agents tab", "Development", "Urgent", ""],
            ["Fix agent walk-back animation after log entry", "Development", "Urgent", ""],
            ["Publish weekly community update", "Marketing", "Urgent", ""],
            ["Research top AI creator tools for competitive landscape doc", "Work", "Normal", ""],
            ["Ship the live activity chart backend payload", "Development", "Normal", ""],
            ["Personal: set up automated invoice reminders", "Personal", "Someday", ""],
        ],
    },
    "documents": [
        ["Mission Control: Building a Live AI Agent Dashboard", "SCRIBE", "4.7 KB - May 6, 10:55 AM"],
        ["Mobile Responsiveness Report", "DEV", "4.1 KB - May 6, 10:43 AM"],
        ["Morning Competitor Brief - May 6, 2026", "SCOUT", "5.0 KB - May 6, 10:43 AM"],
        ["Community Update - May 2026", "SCRIBE", "3.9 KB - May 6, 10:39 AM"],
        ["Weekly Growth Report - Week of Apr 29", "REACH", "3.5 KB - May 6, 10:13 AM"],
        ["Daily Support Plan - May 6", "SCRIBE", "2.7 KB - May 6, 8:15 AM"],
    ],
    "messages": [
        ["Bill", "Bill -> docs/bill/2026-05-01_test-doc.md - short role-aligned test document for Discord operations storage.", "09:14"],
        ["Bill", "Hello Larry - Bill here. Chat feature looks live on my side.", "08:50"],
        ["Bill", "Doing well - steady and ready on ops. Glad to hear from you. How's your day going?", "08:55"],
        ["Bill", "Nothing specific on my end this evening - Discord operations are quiet. Anything you need me to check on?", "22:18"],
        ["Bill", "Sure - Discord side things are running steady: server activity normal, channels nominal, team routed cleanly.", "23:02"],
    ],
    "monitor": {
        "status": [
            ["Gateway", "Online", "Port 50822 - server 3d 0h 2m", "green"],
            ["Discord", "Connected", "Bindings - 6 agents", "blue"],
            ["Database", "240 KB", "Agent logs - 2.0 MB", "yellow"],
            ["Uptime", "12d 0h", "Since boot - server 3d 0h 2m", "orange"],
        ],
        "resources": [
            ["CPU", "8.3%", "Average across all cores", "purple", 8],
            ["RAM", "31.6%", "2.5 GB used of 8.0 GB", "blue", 32],
            ["Storage", "29.0%", "29.0 GB used of 100 GB", "yellow", 29],
        ],
        "usage": [
            ["Scout", "284", "~961.2K tokens", "$4.81"],
            ["Orchestrator", "261", "~882.2K tokens", "$4.41"],
            ["Scribe", "238", "~804.4K tokens", "$4.02"],
        ],
    },
    "productivity": {
        "monthly": ["Read 4 books", "Work out 20 days", "Save $500", "Learn a new skill", "Meditate daily"],
        "weekly": ["Exercise 4 times", "Eat healthy meals", "No sugar this week", "Daily 30 min reading", "Sleep by 11 PM"],
        "defaults": ["Drink 8 glasses of water", "Morning workout / Walk", "Meditate for 10 minutes", "Read 20 pages", "Plan your day", "No screen time after 10 PM"],
        "weekRange": "May 12 - May 18, 2024",
        "weekDays": [
            {"date": "May 12", "day": "Sun", "done": [True, True, True, True, True, True], "notes": True},
            {"date": "May 13", "day": "Mon", "done": [True, True, False, True, False, False], "notes": True},
            {"date": "May 14", "day": "Tue", "done": [True, True, True, True, True, False], "notes": True},
            {"date": "May 15", "day": "Wed", "done": [True, True, True, True, True, True], "notes": True},
            {"date": "May 16", "day": "Thu", "done": [True, False, True, False, True, False], "notes": True},
            {"date": "May 17", "day": "Fri", "done": [True, True, True, False, True, False], "notes": True},
            {"date": "May 18", "day": "Sat", "done": [True, True, True, True, False, False], "notes": True},
        ],
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def row_to_note(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "body": row["body"],
        "color": row["color"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_goal(row: sqlite3.Row) -> dict:
    payload = {
        "id": row["id"],
        "body": row["body"],
        "completed": bool(row["completed"]),
        "created_at": row["created_at"],
    }
    if "updated_at" in row.keys():
        payload["updated_at"] = row["updated_at"]
    return payload


def row_to_productivity_item(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "scope": row["scope"],
        "body": row["body"],
        "completed": bool(row["completed"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_task(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "category": row["category"],
        "priority": row["priority"],
        "status": row["status"],
        "completed": bool(row["completed"]),
        "due_date": row["due_date"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def validate_body(value: object, field_name: str = "body") -> tuple[str | None, str | None]:
    if not isinstance(value, str):
        return None, f"{field_name} must be a string"
    trimmed = value.strip()
    if not trimmed:
        return None, f"{field_name} must not be empty"
    return trimmed, None


def validate_note_color(value: object) -> tuple[int | None, str | None]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None, f"color must be an integer from {NOTE_COLOR_MIN} to {NOTE_COLOR_MAX}"
    if value < NOTE_COLOR_MIN or value > NOTE_COLOR_MAX:
        return None, f"color must be an integer from {NOTE_COLOR_MIN} to {NOTE_COLOR_MAX}"
    return value, None


def parse_resource_id(raw_id: str) -> tuple[int | None, str | None]:
    try:
        parsed = int(raw_id)
    except ValueError:
        return None, "id must be a positive integer"
    if parsed <= 0:
        return None, "id must be a positive integer"
    return parsed, None


def parse_date_key(raw_date: str) -> tuple[str | None, str | None]:
    try:
        parsed = datetime.strptime(raw_date, "%Y-%m-%d")
    except ValueError:
        return None, "date must use YYYY-MM-DD"
    return parsed.strftime("%Y-%m-%d"), None


def validate_scope(value: object) -> tuple[str | None, str | None]:
    if not isinstance(value, str) or value not in PRODUCTIVITY_SCOPES:
        return None, "scope must be monthly, weekly, or default"
    return value, None


def validate_bool(value: object, field_name: str) -> tuple[bool | None, str | None]:
    if not isinstance(value, bool):
        return None, f"{field_name} must be true or false"
    return value, None


def validate_choice(value: object, allowed: set[str], field_name: str) -> tuple[str | None, str | None]:
    if not isinstance(value, str) or value not in allowed:
        return None, f"{field_name} must be one of: {', '.join(sorted(allowed))}"
    return value, None


def validate_optional_string(value: object, field_name: str) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, f"{field_name} must be a string"
    return value.strip(), None


def validate_due_date(value: object) -> tuple[str | None, str | None]:
    if value in (None, ""):
        return None, None
    if not isinstance(value, str):
        return None, "due_date must use YYYY-MM-DD or null"
    parsed, error = parse_date_key(value)
    if error:
        return None, "due_date must use YYYY-MM-DD or null"
    return parsed, None


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def human_ago(value: str | None) -> str:
    parsed = parse_timestamp(value)
    if parsed is None:
        return "No logs yet"
    seconds = max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


def read_openclaw_config() -> dict:
    if not OPENCLAW_CONFIG_PATH.exists():
        return {}
    raw = OPENCLAW_CONFIG_PATH.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeError, json.JSONDecodeError):
            continue
    return {}


def agent_icon(name: str) -> str:
    parts = [part for part in name.replace("-", " ").split() if part]
    if not parts:
        return "A"
    if len(parts) == 1:
        return parts[0][:2].title()
    return "".join(part[0] for part in parts[:2]).upper()


def configured_dashboard_agents() -> list[dict]:
    config = read_openclaw_config()
    defaults = config.get("agents", {}).get("defaults", {})
    default_model = defaults.get("model", {}).get("primary", "Not logged")
    existing_dirs = {
        item.name
        for item in (OPENCLAW_DIR / "agents").iterdir()
        if item.is_dir() and not item.name.startswith("_")
    } if (OPENCLAW_DIR / "agents").exists() else set()
    agents = []
    for item in config.get("agents", {}).get("list", []):
        agent_id = item.get("id") or item.get("name")
        if not agent_id or agent_id in EXCLUDED_DASHBOARD_AGENTS or agent_id not in existing_dirs:
            continue
        name = item.get("name") or agent_id
        identity = item.get("identity") or {}
        agents.append(
            {
                "id": agent_id,
                "name": name,
                "theme": identity.get("theme") or "Configured OpenClaw agent",
                "model": item.get("model") or default_model,
            }
        )
    return agents


def agent_log_table(con: sqlite3.Connection) -> str | None:
    tables = {
        row["name"]
        for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "agent_logs" in tables:
        return "agent_logs"
    if "agent_task_logs" in tables:
        return "agent_task_logs"
    return None


def load_agent_logs() -> dict:
    if not AGENT_LOGS_DB_PATH.exists():
        return {"by_agent": {}, "events": []}
    logs: dict[str, dict] = {}
    events = []
    with closing(sqlite3.connect(AGENT_LOGS_DB_PATH)) as con:
        con.row_factory = sqlite3.Row
        table = agent_log_table(con)
        if table is None:
            return logs
        rows = con.execute(
            f"""
            SELECT agent_name, task_description, model_used, status, created_at
            FROM {table}
            ORDER BY created_at ASC
            """
        ).fetchall()
    for row in rows:
        event = dict(row)
        events.append(event)
        key = row["agent_name"].strip().lower()
        stats = logs.setdefault(
            key,
            {"total": 0, "completed": 0, "latest": None, "events": []},
        )
        stats["total"] += 1
        status = (row["status"] or "").lower()
        if status in {"complete", "completed", "done", "success", "succeeded"}:
            stats["completed"] += 1
        stats["events"].append(event)
        latest = stats["latest"]
        if latest is None or (row["created_at"] or "") >= (latest["created_at"] or ""):
            stats["latest"] = event
    return {"by_agent": logs, "events": events}


def status_bucket(status: str | None) -> str:
    normalized = (status or "").lower().replace("-", "_").replace(" ", "_")
    if normalized in {"complete", "completed", "done", "success", "succeeded"}:
        return "completed"
    if normalized in {"active", "running", "working", "in_progress", "progress"}:
        return "in_progress"
    if normalized in {"failed", "failure", "error", "errored", "blocked"}:
        return "failed"
    return "pending"


def build_agent_task_stats(events: list[dict]) -> dict:
    now = datetime.now().astimezone()
    today = now.date()
    week_start = today.fromordinal(today.toordinal() - today.weekday())
    buckets = {"completed": 0, "in_progress": 0, "failed": 0, "pending": 0}
    today_count = 0
    week_count = 0
    model_counts: dict[str, int] = {}
    for event in events:
        created = parse_timestamp(event.get("created_at"))
        if created is not None:
            local_created = created.astimezone()
            if local_created.date() == today:
                today_count += 1
            if local_created.date() >= week_start:
                week_count += 1
        bucket = status_bucket(event.get("status"))
        buckets[bucket] += 1
        model = event.get("model_used") or "Not logged"
        model_counts[model] = model_counts.get(model, 0) + 1
    total = len(events)
    distribution = [
        {"label": "Completed", "value": buckets["completed"], "color": "var(--green)"},
        {"label": "In Progress", "value": buckets["in_progress"], "color": "var(--blue)"},
        {"label": "Failed", "value": buckets["failed"], "color": "var(--red)"},
        {"label": "Pending", "value": buckets["pending"], "color": "var(--yellow)"},
    ]
    return {
        "today": today_count,
        "week": week_count,
        "total": total,
        "completed": buckets["completed"],
        "successRate": round((buckets["completed"] / total) * 100) if total else 0,
        "weekRate": round((week_count / total) * 100) if total else 0,
        "distribution": distribution,
        "models": [
            {"model": model, "tasks": count, "share": round((count / total) * 100) if total else 0}
            for model, count in sorted(model_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def with_live_agents(payload: dict) -> dict:
    payload = copy.deepcopy(payload)
    configured_agents = configured_dashboard_agents()
    log_data = load_agent_logs()
    logs = log_data["by_agent"]
    all_events = log_data["events"]
    cards = []
    recent_events = []
    total_logs = 0
    completed_logs = 0
    for index, agent in enumerate(configured_agents):
        keys = {agent["id"].lower(), agent["name"].lower()}
        stats = next((logs[key] for key in keys if key in logs), None)
        latest = stats["latest"] if stats else None
        total = stats["total"] if stats else 0
        completed = stats["completed"] if stats else 0
        total_logs += total
        completed_logs += completed
        cards.append(
            {
                "name": agent["name"],
                "role": latest["task_description"] if latest else agent["theme"],
                "icon": agent_icon(agent["name"]),
                "color": AGENT_COLORS[index % len(AGENT_COLORS)],
                "tasks": total,
                "success": round((completed / total) * 100) if total else 0,
                "model": latest["model_used"] if latest and latest["model_used"] else agent["model"],
                "active": human_ago(latest["created_at"] if latest else None),
                "status": (latest["status"] if latest else "No logs").upper(),
            }
        )
        if stats:
            for event in stats["events"]:
                recent_events.append(
                    [
                        agent["name"],
                        event["task_description"],
                        (event["status"] or "unknown").title(),
                        human_ago(event["created_at"]),
                        event["created_at"] or "",
                    ]
                )
    payload["agents"] = cards
    payload["metrics"][0][1] = str(len(cards))
    payload["metrics"][1][1] = str(total_logs)
    payload["metrics"][1][2] = "logged tasks"
    if recent_events:
        recent_events.sort(key=lambda event: event[4])
        payload["activity"]["events"] = [event[:4] for event in recent_events[-3:][::-1]]
    payload["agentStats"] = build_agent_task_stats(
        [
            event
            for event in all_events
            if any(
                event["agent_name"].strip().lower() in {agent["id"].lower(), agent["name"].lower()}
                for agent in configured_agents
            )
        ]
    )
    return payload


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with closing(connect()) as con, con:
        con.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS dashboard_payloads (
                key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS seed_runs (
                name TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sticky_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                body TEXT NOT NULL,
                color INTEGER NOT NULL CHECK (color BETWEEN 0 AND 5),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                body TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                category TEXT NOT NULL CHECK (category IN ('Work', 'Marketing', 'Development', 'Personal')),
                priority TEXT NOT NULL CHECK (priority IN ('Urgent', 'Normal', 'Someday')),
                status TEXT NOT NULL CHECK (status IN ('todo', 'in_progress', 'done')),
                completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1)),
                due_date TEXT,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS productivity_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL CHECK (scope IN ('monthly', 'weekly', 'default')),
                body TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1)),
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS productivity_progress (
                date_key TEXT NOT NULL,
                item_id INTEGER NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1)),
                updated_at TEXT NOT NULL,
                PRIMARY KEY (date_key, item_id),
                FOREIGN KEY (item_id) REFERENCES productivity_items(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS productivity_notes (
                date_key TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                items_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            );
            """
        )
        row = con.execute("SELECT 1 FROM dashboard_payloads WHERE key = 'bootstrap'").fetchone()
        if row is None:
            con.execute(
                "INSERT INTO dashboard_payloads (key, payload_json, updated_at) VALUES (?, ?, ?)",
                ("bootstrap", json.dumps(DEFAULT_BOOTSTRAP, separators=(",", ":")), now_iso()),
            )
            con.execute(
                "INSERT INTO events (source, kind, message, created_at) VALUES (?, ?, ?, ?)",
                ("server", "seed", "Initialized dashboard bootstrap payload", now_iso()),
            )
        seed_notes(con)
        seed_tasks(con)
        seed_productivity_items(con)


def seed_notes(con: sqlite3.Connection) -> None:
    if con.execute("SELECT 1 FROM seed_runs WHERE name = ?", (NOTE_SEED_NAME,)).fetchone():
        return
    created_at = now_iso()
    con.executemany(
        """
        INSERT INTO sticky_notes (body, color, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        [(body, color, created_at, created_at) for body, color in NOTE_SEEDS],
    )
    con.execute(
        "INSERT INTO seed_runs (name, created_at) VALUES (?, ?)",
        (NOTE_SEED_NAME, created_at),
    )


def seed_productivity_items(con: sqlite3.Connection) -> None:
    if con.execute("SELECT 1 FROM seed_runs WHERE name = ?", (PRODUCTIVITY_SEED_NAME,)).fetchone():
        return
    created_at = now_iso()
    productivity = DEFAULT_BOOTSTRAP["productivity"]
    rows = []
    for scope in ("monthly", "weekly", "default"):
        for index, body in enumerate(productivity[f"{scope}s"] if scope == "default" else productivity[scope]):
            completed = int((scope == "monthly" and index < 2) or (scope == "weekly" and index < 3))
            rows.append((scope, body, completed, index, created_at, created_at))
    con.executemany(
        """
        INSERT INTO productivity_items (scope, body, completed, sort_order, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    con.execute(
        "INSERT INTO seed_runs (name, created_at) VALUES (?, ?)",
        (PRODUCTIVITY_SEED_NAME, created_at),
    )


def seed_tasks(con: sqlite3.Connection) -> None:
    if con.execute("SELECT 1 FROM seed_runs WHERE name = ?", (TASK_SEED_NAME,)).fetchone():
        return
    created_at = now_iso()
    con.executemany(
        """
        INSERT INTO tasks (title, category, priority, status, completed, due_date, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                task["title"],
                task["category"],
                task["priority"],
                task["status"],
                int(task["status"] == "done"),
                task["due_date"],
                task["notes"],
                created_at,
                created_at,
            )
            for task in TASK_SEEDS
        ],
    )
    con.execute(
        "INSERT INTO seed_runs (name, created_at) VALUES (?, ?)",
        (TASK_SEED_NAME, created_at),
    )


def list_tasks(category: str | None = None) -> list[dict]:
    where = ""
    values: tuple[str, ...] = ()
    if category:
        where = "WHERE category = ?"
        values = (category,)
    with closing(connect()) as con:
        rows = con.execute(
            f"""
            SELECT id, title, category, priority, status, completed, due_date, notes, created_at, updated_at
            FROM tasks
            {where}
            ORDER BY
                CASE status WHEN 'todo' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,
                CASE priority WHEN 'Urgent' THEN 0 WHEN 'Normal' THEN 1 ELSE 2 END,
                COALESCE(due_date, '9999-12-31') ASC,
                id ASC
            """,
            values,
        ).fetchall()
    return [row_to_task(row) for row in rows]


def create_task(fields: dict) -> dict:
    timestamp = now_iso()
    status = fields.get("status", "todo")
    completed = int(status == "done")
    with closing(connect()) as con, con:
        cursor = con.execute(
            """
            INSERT INTO tasks (title, category, priority, status, completed, due_date, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fields["title"],
                fields.get("category", "Work"),
                fields.get("priority", "Normal"),
                status,
                completed,
                fields.get("due_date"),
                fields.get("notes", ""),
                timestamp,
                timestamp,
            ),
        )
        row = con.execute(
            """
            SELECT id, title, category, priority, status, completed, due_date, notes, created_at, updated_at
            FROM tasks
            WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    return row_to_task(row)


def update_task(task_id: int, updates: dict) -> dict | None:
    assignments = []
    values = []
    for field in ("title", "category", "priority", "status", "completed", "due_date", "notes"):
        if field in updates:
            assignments.append(f"{field} = ?")
            values.append(int(updates[field]) if field == "completed" else updates[field])
    if "status" in updates:
        if "completed" not in updates:
            assignments.append("completed = ?")
            values.append(int(updates["status"] == "done"))
        else:
            completed_index = assignments.index("completed = ?")
            values[completed_index] = int(updates["status"] == "done")
    timestamp = now_iso()
    assignments.append("updated_at = ?")
    values.append(timestamp)
    values.append(task_id)
    with closing(connect()) as con, con:
        cursor = con.execute(
            f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
        if cursor.rowcount == 0:
            return None
        row = con.execute(
            """
            SELECT id, title, category, priority, status, completed, due_date, notes, created_at, updated_at
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
    return row_to_task(row)


def delete_task(task_id: int) -> bool:
    with closing(connect()) as con, con:
        cursor = con.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cursor.rowcount > 0


def clear_done_tasks() -> int:
    with closing(connect()) as con, con:
        cursor = con.execute("DELETE FROM tasks WHERE status = 'done' OR completed = 1")
        return cursor.rowcount


def list_notes() -> list[dict]:
    with closing(connect()) as con:
        rows = con.execute(
            """
            SELECT id, body, color, created_at, updated_at
            FROM sticky_notes
            ORDER BY updated_at DESC, id DESC
            """
        ).fetchall()
    return [row_to_note(row) for row in rows]


def create_note(body: str, color: int) -> dict:
    timestamp = now_iso()
    with closing(connect()) as con, con:
        cursor = con.execute(
            """
            INSERT INTO sticky_notes (body, color, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (body, color, timestamp, timestamp),
        )
        row = con.execute(
            "SELECT id, body, color, created_at, updated_at FROM sticky_notes WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return row_to_note(row)


def update_note(note_id: int, updates: dict) -> dict | None:
    assignments = []
    values = []
    if "body" in updates:
        assignments.append("body = ?")
        values.append(updates["body"])
    if "color" in updates:
        assignments.append("color = ?")
        values.append(updates["color"])
    timestamp = now_iso()
    assignments.append("updated_at = ?")
    values.append(timestamp)
    values.append(note_id)
    with closing(connect()) as con, con:
        cursor = con.execute(
            f"UPDATE sticky_notes SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
        if cursor.rowcount == 0:
            return None
        row = con.execute(
            "SELECT id, body, color, created_at, updated_at FROM sticky_notes WHERE id = ?",
            (note_id,),
        ).fetchone()
    return row_to_note(row)


def delete_note(note_id: int) -> bool:
    with closing(connect()) as con, con:
        cursor = con.execute("DELETE FROM sticky_notes WHERE id = ?", (note_id,))
        return cursor.rowcount > 0


def list_goals() -> list[dict]:
    with closing(connect()) as con:
        rows = con.execute(
            """
            SELECT id, body, completed, created_at, updated_at
            FROM daily_goals
            ORDER BY completed ASC, created_at ASC, id ASC
            """
        ).fetchall()
    return [row_to_goal(row) for row in rows]


def create_goal(body: str) -> dict:
    timestamp = now_iso()
    with closing(connect()) as con, con:
        cursor = con.execute(
            """
            INSERT INTO daily_goals (body, completed, created_at, updated_at)
            VALUES (?, 0, ?, ?)
            """,
            (body, timestamp, timestamp),
        )
        row = con.execute(
            "SELECT id, body, completed, created_at, updated_at FROM daily_goals WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return row_to_goal(row)


def toggle_goal(goal_id: int) -> dict | None:
    timestamp = now_iso()
    with closing(connect()) as con, con:
        cursor = con.execute(
            """
            UPDATE daily_goals
            SET completed = CASE completed WHEN 1 THEN 0 ELSE 1 END,
                updated_at = ?
            WHERE id = ?
            """,
            (timestamp, goal_id),
        )
        if cursor.rowcount == 0:
            return None
        row = con.execute(
            "SELECT id, body, completed, created_at, updated_at FROM daily_goals WHERE id = ?",
            (goal_id,),
        ).fetchone()
    return row_to_goal(row)


def delete_goal(goal_id: int) -> bool:
    with closing(connect()) as con, con:
        cursor = con.execute("DELETE FROM daily_goals WHERE id = ?", (goal_id,))
        return cursor.rowcount > 0


def list_productivity_items(scope: str | None = None) -> list[dict]:
    where = ""
    values: tuple[str, ...] = ()
    if scope:
        where = "WHERE scope = ?"
        values = (scope,)
    with closing(connect()) as con:
        rows = con.execute(
            f"""
            SELECT id, scope, body, completed, created_at, updated_at
            FROM productivity_items
            {where}
            ORDER BY sort_order ASC, id ASC
            """,
            values,
        ).fetchall()
    return [row_to_productivity_item(row) for row in rows]


def create_productivity_item(scope: str, body: str) -> dict:
    timestamp = now_iso()
    with closing(connect()) as con, con:
        sort_order = con.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM productivity_items WHERE scope = ?",
            (scope,),
        ).fetchone()[0]
        cursor = con.execute(
            """
            INSERT INTO productivity_items (scope, body, completed, sort_order, created_at, updated_at)
            VALUES (?, ?, 0, ?, ?, ?)
            """,
            (scope, body, sort_order, timestamp, timestamp),
        )
        row = con.execute(
            """
            SELECT id, scope, body, completed, created_at, updated_at
            FROM productivity_items
            WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    return row_to_productivity_item(row)


def toggle_productivity_item(item_id: int) -> dict | None:
    timestamp = now_iso()
    with closing(connect()) as con, con:
        cursor = con.execute(
            """
            UPDATE productivity_items
            SET completed = CASE completed WHEN 1 THEN 0 ELSE 1 END,
                updated_at = ?
            WHERE id = ?
            """,
            (timestamp, item_id),
        )
        if cursor.rowcount == 0:
            return None
        row = con.execute(
            """
            SELECT id, scope, body, completed, created_at, updated_at
            FROM productivity_items
            WHERE id = ?
            """,
            (item_id,),
        ).fetchone()
    return row_to_productivity_item(row)


def delete_productivity_item(item_id: int) -> bool:
    with closing(connect()) as con, con:
        cursor = con.execute("DELETE FROM productivity_items WHERE id = ?", (item_id,))
        return cursor.rowcount > 0


def set_productivity_progress(date_key: str, item_id: int, completed: bool) -> dict | None:
    timestamp = now_iso()
    with closing(connect()) as con, con:
        item = con.execute(
            "SELECT id FROM productivity_items WHERE id = ? AND scope = 'default'",
            (item_id,),
        ).fetchone()
        if item is None:
            return None
        con.execute(
            """
            INSERT INTO productivity_progress (date_key, item_id, completed, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date_key, item_id) DO UPDATE SET
                completed = excluded.completed,
                updated_at = excluded.updated_at
            """,
            (date_key, item_id, int(completed), timestamp),
        )
    return {"date": date_key, "item_id": item_id, "completed": completed}


def get_productivity_note(date_key: str) -> dict:
    with closing(connect()) as con:
        row = con.execute(
            "SELECT date_key, title, items_json, updated_at FROM productivity_notes WHERE date_key = ?",
            (date_key,),
        ).fetchone()
    if row is None:
        return {"date": date_key, "title": "", "items": [], "updated_at": None}
    try:
        items = json.loads(row["items_json"])
    except json.JSONDecodeError:
        items = []
    return {"date": row["date_key"], "title": row["title"], "items": items, "updated_at": row["updated_at"]}


def update_productivity_note(date_key: str, title: str, items: list[dict]) -> dict:
    timestamp = now_iso()
    cleaned_items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        body, error = validate_body(item.get("body"), "note item")
        if error:
            continue
        cleaned_items.append({"body": body, "completed": bool(item.get("completed"))})
    with closing(connect()) as con, con:
        con.execute(
            """
            INSERT INTO productivity_notes (date_key, title, items_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date_key) DO UPDATE SET
                title = excluded.title,
                items_json = excluded.items_json,
                updated_at = excluded.updated_at
            """,
            (date_key, title, json.dumps(cleaned_items, separators=(",", ":")), timestamp),
        )
    return get_productivity_note(date_key)


def clear_completed_note_items(date_key: str) -> dict:
    note = get_productivity_note(date_key)
    remaining = [item for item in note["items"] if not item.get("completed")]
    return update_productivity_note(date_key, note["title"], remaining)


def delete_productivity_note(date_key: str) -> bool:
    with closing(connect()) as con, con:
        cursor = con.execute("DELETE FROM productivity_notes WHERE date_key = ?", (date_key,))
        return cursor.rowcount > 0


def productivity_context(today: datetime | None = None) -> dict:
    current = (today or datetime.now().astimezone()).date()
    week_start = current - timedelta(days=current.weekday())
    week_end = week_start + timedelta(days=6)
    _, month_days = calendar.monthrange(current.year, current.month)
    month_start = current.replace(day=1)
    return {
        "today": current.strftime("%Y-%m-%d"),
        "monthName": current.strftime("%B"),
        "weekNumber": current.isocalendar().week,
        "weekRange": f"{week_start.strftime('%b %-d') if os.name != 'nt' else week_start.strftime('%b %#d')} - {week_end.strftime('%b %-d, %Y') if os.name != 'nt' else week_end.strftime('%b %#d, %Y')}",
        "weekDates": [week_start + timedelta(days=offset) for offset in range(7)],
        "monthDates": [month_start + timedelta(days=offset) for offset in range(month_days)],
    }


def build_productivity_state(anchor_date: str | None = None) -> dict:
    anchor = None
    if anchor_date:
        parsed_date, error = parse_date_key(anchor_date)
        if error is None:
            anchor = datetime.strptime(parsed_date, "%Y-%m-%d").replace(tzinfo=datetime.now().astimezone().tzinfo)
    context = productivity_context(anchor)
    items_by_scope = {scope: list_productivity_items(scope) for scope in PRODUCTIVITY_SCOPES}
    defaults = items_by_scope["default"]
    default_ids = [item["id"] for item in defaults]
    week_date_keys = [day.strftime("%Y-%m-%d") for day in context["weekDates"]]
    month_date_keys = [day.strftime("%Y-%m-%d") for day in context["monthDates"]]
    date_keys = sorted(set(week_date_keys + month_date_keys))
    progress: dict[str, dict[int, bool]] = {date_key: {} for date_key in date_keys}
    if default_ids:
        placeholders = ",".join("?" for _ in default_ids)
        with closing(connect()) as con:
            rows = con.execute(
                f"""
                SELECT date_key, item_id, completed
                FROM productivity_progress
                WHERE date_key IN ({",".join("?" for _ in date_keys)})
                  AND item_id IN ({placeholders})
                """,
                (*date_keys, *default_ids),
            ).fetchall()
        for row in rows:
            progress[row["date_key"]][row["item_id"]] = bool(row["completed"])
    week_days = []
    for day in context["weekDates"]:
        date_key = day.strftime("%Y-%m-%d")
        note = get_productivity_note(date_key)
        week_days.append(
            {
                "date": day.strftime("%b %#d" if os.name == "nt" else "%b %-d"),
                "dateKey": date_key,
                "day": day.strftime("%a"),
                "done": [progress[date_key].get(item["id"], False) for item in defaults],
                "notes": bool(note["title"] or note["items"]),
            }
        )
    today_done = progress.get(context["today"], {})
    today_count = sum(1 for item in defaults if today_done.get(item["id"], False))
    month_progress = []
    for date_key in month_date_keys:
        values = progress.get(date_key, {})
        note = get_productivity_note(date_key)
        custom_total = len(note["items"])
        custom_completed = sum(1 for item in note["items"] if item.get("completed"))
        default_total = len(defaults)
        default_completed = sum(1 for item in defaults if values.get(item["id"], False))
        total_tasks = default_total + custom_total
        total_completed = default_completed + custom_completed
        date_value = datetime.strptime(date_key, "%Y-%m-%d")
        month_progress.append(
            {
                "date": date_key,
                "label": date_value.strftime("%b %#d" if os.name == "nt" else "%b %-d"),
                "day": date_value.day,
                "defaultCompleted": default_completed,
                "defaultTotal": default_total,
                "customCompleted": custom_completed,
                "customTotal": custom_total,
                "value": round(total_completed / total_tasks, 3) if total_tasks else 0,
            }
        )
    return {
        "monthly": items_by_scope["monthly"],
        "weekly": items_by_scope["weekly"],
        "defaults": defaults,
        "today": context["today"],
        "monthName": context["monthName"],
        "weekNumber": context["weekNumber"],
        "weekRange": context["weekRange"],
        "weekDays": week_days,
        "todayCompleted": today_count,
        "monthProgress": month_progress,
        "selectedNote": get_productivity_note(context["today"]),
    }


def get_payload(key: str) -> dict:
    with closing(connect()) as con:
        row = con.execute(
            "SELECT payload_json FROM dashboard_payloads WHERE key = ?",
            (key,),
        ).fetchone()
    if row is None:
        return {}
    payload = json.loads(row["payload_json"])
    if key == "bootstrap":
        return with_live_agents(payload)
    return payload


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "AgentDashboard/0.2.0"

    def log_message(self, fmt: str, *args: object) -> None:
        message = "%s - %s\n" % (self.log_date_time_string(), fmt % args)
        log_path = APP_DIR / "server.log"
        with log_path.open("a", encoding="utf-8") as log:
            log.write(message)

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if path in {"/", "/index.html"}:
            self.send_file(INDEX_PATH, "text/html; charset=utf-8")
            return
        if path == "/api/health":
            self.send_json(
                {
                    "ok": True,
                    "db": str(DB_PATH),
                    "index": str(INDEX_PATH),
                    "time": now_iso(),
                }
            )
            return
        if path == "/api/bootstrap":
            self.send_json(get_payload("bootstrap"))
            return
        if path == "/api/tasks":
            query = parse_qs(parsed_url.query)
            category = query.get("category", [None])[0]
            if category:
                category, error = validate_choice(category, TASK_CATEGORIES, "category")
                if error:
                    self.send_error_json(HTTPStatus.BAD_REQUEST, error)
                    return
            self.handle_database(lambda: self.send_json(list_tasks(category)))
            return
        if path == "/api/notes":
            self.handle_database(lambda: self.send_json(list_notes()))
            return
        if path == "/api/goals":
            self.handle_database(lambda: self.send_json(list_goals()))
            return
        if path == "/api/productivity":
            query = parse_qs(parsed_url.query)
            anchor_date = query.get("date", [None])[0]
            self.handle_database(lambda: self.send_json(build_productivity_state(anchor_date)))
            return
        if len(path_parts := [part for part in path.split("/") if part]) == 4 and path_parts[:3] == ["api", "productivity", "notes"]:
            date_key, error = parse_date_key(path_parts[3])
            if error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, error)
                return
            self.handle_database(lambda: self.send_json(get_productivity_note(date_key)))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/notes":
            self.handle_database(self.handle_create_note)
            return
        if path == "/api/tasks":
            self.handle_database(self.handle_create_task)
            return
        if path == "/api/goals":
            self.handle_database(self.handle_create_goal)
            return
        if path == "/api/productivity/items":
            self.handle_database(self.handle_create_productivity_item)
            return
        if path == "/api/productivity/progress":
            self.handle_database(self.handle_set_productivity_progress)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_PATCH(self) -> None:
        path_parts = [part for part in urlparse(self.path).path.split("/") if part]
        if len(path_parts) == 3 and path_parts[:2] == ["api", "notes"]:
            self.handle_database(lambda: self.handle_update_note(path_parts[2]))
            return
        if len(path_parts) == 3 and path_parts[:2] == ["api", "tasks"]:
            self.handle_database(lambda: self.handle_update_task(path_parts[2]))
            return
        if len(path_parts) == 4 and path_parts[:2] == ["api", "goals"] and path_parts[3] == "toggle":
            self.handle_database(lambda: self.handle_toggle_goal(path_parts[2]))
            return
        if len(path_parts) == 5 and path_parts[:3] == ["api", "productivity", "items"] and path_parts[4] == "toggle":
            self.handle_database(lambda: self.handle_toggle_productivity_item(path_parts[3]))
            return
        if len(path_parts) == 4 and path_parts[:3] == ["api", "productivity", "notes"]:
            date_key, error = parse_date_key(path_parts[3])
            if error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, error)
                return
            self.handle_database(lambda: self.handle_update_productivity_note(date_key))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_DELETE(self) -> None:
        path_parts = [part for part in urlparse(self.path).path.split("/") if part]
        if len(path_parts) == 3 and path_parts[:2] == ["api", "notes"]:
            self.handle_database(lambda: self.handle_delete_note(path_parts[2]))
            return
        if len(path_parts) == 3 and path_parts == ["api", "tasks", "done"]:
            self.handle_database(self.handle_clear_done_tasks)
            return
        if len(path_parts) == 3 and path_parts[:2] == ["api", "tasks"]:
            self.handle_database(lambda: self.handle_delete_task(path_parts[2]))
            return
        if len(path_parts) == 3 and path_parts[:2] == ["api", "goals"]:
            self.handle_database(lambda: self.handle_delete_goal(path_parts[2]))
            return
        if len(path_parts) == 4 and path_parts[:3] == ["api", "productivity", "items"]:
            self.handle_database(lambda: self.handle_delete_productivity_item(path_parts[3]))
            return
        if len(path_parts) == 4 and path_parts[:3] == ["api", "productivity", "notes"]:
            date_key, error = parse_date_key(path_parts[3])
            if error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, error)
                return
            self.handle_database(lambda: self.handle_delete_productivity_note(date_key))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def read_json_body(self) -> tuple[dict | None, str | None]:
        content_length = self.headers.get("Content-Length")
        if content_length is None or content_length == "0":
            return None, "request body is required"
        try:
            length = int(content_length)
        except ValueError:
            return None, "invalid content length"
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "request body must be valid JSON"
        if not isinstance(payload, dict):
            return None, "request body must be a JSON object"
        return payload, None

    def handle_create_note(self) -> None:
        payload, error = self.read_json_body()
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        unsupported = set(payload) - {"body", "color"}
        if unsupported:
            self.send_error_json(HTTPStatus.BAD_REQUEST, f"unsupported field: {sorted(unsupported)[0]}")
            return
        body, error = validate_body(payload.get("body"))
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        color, error = validate_note_color(payload.get("color"))
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        self.send_json(create_note(body, color), HTTPStatus.CREATED)

    def handle_update_note(self, raw_id: str) -> None:
        note_id, error = parse_resource_id(raw_id)
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        payload, error = self.read_json_body()
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        unsupported = set(payload) - {"body", "color"}
        if unsupported:
            self.send_error_json(HTTPStatus.BAD_REQUEST, f"unsupported field: {sorted(unsupported)[0]}")
            return
        if not any(field in payload for field in ("body", "color")):
            self.send_error_json(HTTPStatus.BAD_REQUEST, "body or color is required")
            return
        updates = {}
        if "body" in payload:
            body, error = validate_body(payload["body"])
            if error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, error)
                return
            updates["body"] = body
        if "color" in payload:
            color, error = validate_note_color(payload["color"])
            if error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, error)
                return
            updates["color"] = color
        note = update_note(note_id, updates)
        if note is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "note not found")
            return
        self.send_json(note)

    def handle_delete_note(self, raw_id: str) -> None:
        note_id, error = parse_resource_id(raw_id)
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        if not delete_note(note_id):
            self.send_error_json(HTTPStatus.NOT_FOUND, "note not found")
            return
        self.send_json({"ok": True, "deleted": True})

    def validated_task_fields(self, payload: dict, creating: bool) -> tuple[dict | None, str | None]:
        supported = {"title", "category", "priority", "status", "completed", "due_date", "notes"}
        unsupported = set(payload) - supported
        if unsupported:
            return None, f"unsupported field: {sorted(unsupported)[0]}"
        if creating and "title" not in payload:
            return None, "title is required"
        if not creating and not any(field in payload for field in supported):
            return None, "at least one task field is required"
        fields = {}
        if "title" in payload:
            title, error = validate_body(payload["title"], "title")
            if error:
                return None, error
            fields["title"] = title
        if "category" in payload:
            category, error = validate_choice(payload["category"], TASK_CATEGORIES, "category")
            if error:
                return None, error
            fields["category"] = category
        elif creating:
            fields["category"] = "Work"
        if "priority" in payload:
            priority, error = validate_choice(payload["priority"], TASK_PRIORITIES, "priority")
            if error:
                return None, error
            fields["priority"] = priority
        elif creating:
            fields["priority"] = "Normal"
        if "status" in payload:
            status, error = validate_choice(payload["status"], TASK_STATUSES, "status")
            if error:
                return None, error
            fields["status"] = status
        elif creating:
            fields["status"] = "todo"
        if "completed" in payload:
            completed, error = validate_bool(payload["completed"], "completed")
            if error:
                return None, error
            fields["completed"] = completed
        if "due_date" in payload:
            due_date, error = validate_due_date(payload["due_date"])
            if error:
                return None, error
            fields["due_date"] = due_date
        elif creating:
            fields["due_date"] = None
        if "notes" in payload:
            notes, error = validate_optional_string(payload["notes"], "notes")
            if error:
                return None, error
            fields["notes"] = notes or ""
        elif creating:
            fields["notes"] = ""
        return fields, None

    def handle_create_task(self) -> None:
        payload, error = self.read_json_body()
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        fields, error = self.validated_task_fields(payload, creating=True)
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        self.send_json(create_task(fields), HTTPStatus.CREATED)

    def handle_update_task(self, raw_id: str) -> None:
        task_id, error = parse_resource_id(raw_id)
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        payload, error = self.read_json_body()
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        fields, error = self.validated_task_fields(payload, creating=False)
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        task = update_task(task_id, fields)
        if task is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "task not found")
            return
        self.send_json(task)

    def handle_delete_task(self, raw_id: str) -> None:
        task_id, error = parse_resource_id(raw_id)
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        if not delete_task(task_id):
            self.send_error_json(HTTPStatus.NOT_FOUND, "task not found")
            return
        self.send_json({"ok": True, "deleted": True})

    def handle_clear_done_tasks(self) -> None:
        deleted = clear_done_tasks()
        self.send_json({"ok": True, "deleted": deleted})

    def handle_create_goal(self) -> None:
        payload, error = self.read_json_body()
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        unsupported = set(payload) - {"body"}
        if unsupported:
            self.send_error_json(HTTPStatus.BAD_REQUEST, f"unsupported field: {sorted(unsupported)[0]}")
            return
        body, error = validate_body(payload.get("body"))
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        self.send_json(create_goal(body), HTTPStatus.CREATED)

    def handle_toggle_goal(self, raw_id: str) -> None:
        goal_id, error = parse_resource_id(raw_id)
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        goal = toggle_goal(goal_id)
        if goal is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "goal not found")
            return
        self.send_json(goal)

    def handle_delete_goal(self, raw_id: str) -> None:
        goal_id, error = parse_resource_id(raw_id)
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        if not delete_goal(goal_id):
            self.send_error_json(HTTPStatus.NOT_FOUND, "goal not found")
            return
        self.send_json({"ok": True, "deleted": True})

    def handle_create_productivity_item(self) -> None:
        payload, error = self.read_json_body()
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        unsupported = set(payload) - {"scope", "body"}
        if unsupported:
            self.send_error_json(HTTPStatus.BAD_REQUEST, f"unsupported field: {sorted(unsupported)[0]}")
            return
        scope, error = validate_scope(payload.get("scope"))
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        body, error = validate_body(payload.get("body"))
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        self.send_json(create_productivity_item(scope, body), HTTPStatus.CREATED)

    def handle_toggle_productivity_item(self, raw_id: str) -> None:
        item_id, error = parse_resource_id(raw_id)
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        item = toggle_productivity_item(item_id)
        if item is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "productivity item not found")
            return
        self.send_json(item)

    def handle_delete_productivity_item(self, raw_id: str) -> None:
        item_id, error = parse_resource_id(raw_id)
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        if not delete_productivity_item(item_id):
            self.send_error_json(HTTPStatus.NOT_FOUND, "productivity item not found")
            return
        self.send_json({"ok": True, "deleted": True})

    def handle_set_productivity_progress(self) -> None:
        payload, error = self.read_json_body()
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        unsupported = set(payload) - {"date", "item_id", "completed"}
        if unsupported:
            self.send_error_json(HTTPStatus.BAD_REQUEST, f"unsupported field: {sorted(unsupported)[0]}")
            return
        if not isinstance(payload.get("date"), str):
            self.send_error_json(HTTPStatus.BAD_REQUEST, "date must use YYYY-MM-DD")
            return
        date_key, error = parse_date_key(payload["date"])
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        item_id_value = payload.get("item_id")
        if isinstance(item_id_value, bool) or not isinstance(item_id_value, int) or item_id_value <= 0:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "item_id must be a positive integer")
            return
        completed, error = validate_bool(payload.get("completed"), "completed")
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        progress = set_productivity_progress(date_key, item_id_value, completed)
        if progress is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "default task not found")
            return
        self.send_json(progress)

    def handle_update_productivity_note(self, date_key: str) -> None:
        payload, error = self.read_json_body()
        if error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, error)
            return
        unsupported = set(payload) - {"title", "items", "action"}
        if unsupported:
            self.send_error_json(HTTPStatus.BAD_REQUEST, f"unsupported field: {sorted(unsupported)[0]}")
            return
        if payload.get("action") == "clear_completed":
            self.send_json(clear_completed_note_items(date_key))
            return
        title = payload.get("title", "")
        if not isinstance(title, str):
            self.send_error_json(HTTPStatus.BAD_REQUEST, "title must be a string")
            return
        items = payload.get("items", [])
        if not isinstance(items, list):
            self.send_error_json(HTTPStatus.BAD_REQUEST, "items must be an array")
            return
        self.send_json(update_productivity_note(date_key, title.strip(), items))

    def handle_delete_productivity_note(self, date_key: str) -> None:
        delete_productivity_note(date_key)
        self.send_json({"ok": True, "deleted": True})

    def handle_database(self, callback) -> None:
        try:
            callback()
        except sqlite3.DatabaseError:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "database operation failed")

    def send_file(self, path: Path, content_type: str | None = None) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        body = path.read_bytes()
        guessed = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", guessed)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"error": {"message": message}}, status)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the Agent Dashboard.")
    parser.add_argument("--host", default=os.environ.get("HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    init_db()
    httpd = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Agent Dashboard running at http://{args.host}:{args.port}")
    print(f"Serving {INDEX_PATH}")
    print(f"SQLite data at {DB_PATH}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
