#!/usr/bin/env python3
"""Small SQLite-backed server for the Agent Dashboard."""

from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import os
import sqlite3
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


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
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    with sqlite3.connect(AGENT_LOGS_DB_PATH) as con:
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
    with connect() as con:
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


def get_payload(key: str) -> dict:
    with connect() as con:
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
    server_version = "AgentDashboard/0.1.6"

    def log_message(self, fmt: str, *args: object) -> None:
        message = "%s - %s\n" % (self.log_date_time_string(), fmt % args)
        log_path = APP_DIR / "server.log"
        with log_path.open("a", encoding="utf-8") as log:
            log.write(message)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
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
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

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

    def send_json(self, payload: dict) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


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
