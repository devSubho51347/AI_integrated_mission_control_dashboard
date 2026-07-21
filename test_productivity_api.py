import json
import calendar
import tempfile
import threading
import unittest
import sqlite3
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import server


class ProductivityApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = server.DATA_DIR
        self.original_db_path = server.DB_PATH
        self.original_docs_dir = server.DOCS_DIR
        self.original_openclaw_dir = server.OPENCLAW_DIR
        self.original_openclaw_config_path = server.OPENCLAW_CONFIG_PATH
        self.original_agent_logs_db_path = server.AGENT_LOGS_DB_PATH
        server.DATA_DIR = Path(self.temp_dir.name)
        server.DB_PATH = server.DATA_DIR / "dashboard.db"
        server.DOCS_DIR = Path(self.temp_dir.name) / "docs"
        server.OPENCLAW_DIR = Path(self.temp_dir.name) / "openclaw"
        server.OPENCLAW_CONFIG_PATH = server.OPENCLAW_DIR / "openclaw.json"
        server.AGENT_LOGS_DB_PATH = server.OPENCLAW_DIR / "agent-logs.db"
        self.create_openclaw_fixture()
        server.init_db()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.DashboardHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.httpd.server_address

    def tearDown(self):
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()
        server.DATA_DIR = self.original_data_dir
        server.DB_PATH = self.original_db_path
        server.DOCS_DIR = self.original_docs_dir
        server.OPENCLAW_DIR = self.original_openclaw_dir
        server.OPENCLAW_CONFIG_PATH = self.original_openclaw_config_path
        server.AGENT_LOGS_DB_PATH = self.original_agent_logs_db_path
        self.temp_dir.cleanup()

    def request(self, method, path, payload=None):
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload)
            headers["Content-Type"] = "application/json"
        conn = HTTPConnection(self.host, self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read().decode("utf-8")
        conn.close()
        return response.status, json.loads(raw) if raw else None

    def create_note(self, body="Prepare the weekly project update", color=2):
        status, payload = self.request("POST", "/api/notes", {"body": body, "color": color})
        self.assertEqual(status, 201)
        return payload

    def create_goal(self, body="Record the dashboard walkthrough video"):
        status, payload = self.request("POST", "/api/goals", {"body": body})
        self.assertEqual(status, 201)
        return payload

    def create_task(self, title="Publish task system smoke test", **overrides):
        payload = {
            "title": title,
            "category": "Work",
            "priority": "Normal",
            "status": "todo",
            "due_date": "2026-07-25",
            "notes": "Created by the API test suite.",
        }
        payload.update(overrides)
        status, task = self.request("POST", "/api/tasks", payload)
        self.assertEqual(status, 201)
        return task

    def write_doc_file(self, agent="scout", filename="market-brief.md", content="# Market Brief\n\nSignals."):
        path = server.DOCS_DIR / agent
        path.mkdir(parents=True, exist_ok=True)
        (path / filename).write_text(content, encoding="utf-8")

    def create_openclaw_fixture(self):
        agents = [
            {"id": "scout", "name": "Scout", "identity": {"theme": "Research"}, "model": "test-scout"},
            {"id": "scribe", "name": "Scribe", "identity": {"theme": "Writing"}, "model": "test-scribe"},
        ]
        for agent in agents:
            (server.OPENCLAW_DIR / "agents" / agent["id"] / "sessions").mkdir(parents=True, exist_ok=True)
            (server.OPENCLAW_DIR / "agents" / agent["id"] / "agent" / "codex-home" / "sessions").mkdir(parents=True, exist_ok=True)
        server.OPENCLAW_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        server.OPENCLAW_CONFIG_PATH.write_text(
            json.dumps(
                {
                    "agents": {
                        "defaults": {
                            "model": {"primary": "test-model"},
                            "models": {
                                "test-model": {"alias": "Default"},
                                "test-scout": {"alias": "Scout"},
                                "test-scribe": {"alias": "Scribe"},
                                "test-upgrade": {"alias": "Upgrade"},
                            },
                        },
                        "list": agents,
                    }
                }
            ),
            encoding="utf-8",
        )

    def test_fetching_seeded_notes(self):
        status, payload = self.request("GET", "/api/notes")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload), 4)
        self.assertEqual(len({note["color"] for note in payload}), 4)

        server.init_db()
        status, payload = self.request("GET", "/api/notes")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload), 4)

    def test_fetching_seeded_tasks_and_filtering_by_category(self):
        status, payload = self.request("GET", "/api/tasks")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload), 8)
        counts = {}
        for task in payload:
            counts[task["status"]] = counts.get(task["status"], 0) + 1
        self.assertEqual(counts, {"todo": 3, "in_progress": 3, "done": 2})

        status, payload = self.request("GET", "/api/tasks?category=Development")
        self.assertEqual(status, 200)
        self.assertTrue(payload)
        self.assertTrue(all(task["category"] == "Development" for task in payload))

    def test_creating_a_task(self):
        task = self.create_task(category="Marketing", priority="Urgent", notes="Needs a crisp CTA.")
        self.assertEqual(task["title"], "Publish task system smoke test")
        self.assertEqual(task["category"], "Marketing")
        self.assertEqual(task["priority"], "Urgent")
        self.assertEqual(task["status"], "todo")
        self.assertFalse(task["completed"])
        self.assertEqual(task["notes"], "Needs a crisp CTA.")

    def test_moving_task_to_done_sets_completed_automatically(self):
        task = self.create_task()
        status, updated = self.request("PATCH", f"/api/tasks/{task['id']}", {"status": "done"})
        self.assertEqual(status, 200)
        self.assertEqual(updated["status"], "done")
        self.assertTrue(updated["completed"])

    def test_moving_done_task_away_clears_completed_automatically(self):
        task = self.create_task(status="done")
        self.assertTrue(task["completed"])
        status, updated = self.request("PATCH", f"/api/tasks/{task['id']}", {"status": "in_progress"})
        self.assertEqual(status, 200)
        self.assertEqual(updated["status"], "in_progress")
        self.assertFalse(updated["completed"])

    def test_updating_task_fields_and_deleting_task(self):
        task = self.create_task()
        status, updated = self.request(
            "PATCH",
            f"/api/tasks/{task['id']}",
            {"title": "Updated task title", "priority": "Someday", "due_date": None, "notes": "Updated notes"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["title"], "Updated task title")
        self.assertEqual(updated["priority"], "Someday")
        self.assertIsNone(updated["due_date"])

        status, payload = self.request("DELETE", f"/api/tasks/{task['id']}")
        self.assertEqual(status, 200)
        self.assertTrue(payload["deleted"])

    def test_clearing_done_tasks(self):
        self.create_task(status="done")
        status, payload = self.request("DELETE", "/api/tasks/done")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(payload["deleted"], 3)
        status, tasks = self.request("GET", "/api/tasks")
        self.assertEqual(status, 200)
        self.assertTrue(all(task["status"] != "done" and not task["completed"] for task in tasks))

    def test_rejecting_invalid_task_fields(self):
        status, payload = self.request("POST", "/api/tasks", {"title": "Bad", "category": "Chores"})
        self.assertEqual(status, 400)
        self.assertIn("category", payload["error"]["message"])

        status, payload = self.request("PATCH", "/api/tasks/1", {"status": "blocked"})
        self.assertEqual(status, 400)
        self.assertIn("status", payload["error"]["message"])

    def test_agent_sessions_report_total_session_file_size(self):
        session_file = server.OPENCLAW_DIR / "agents" / "scout" / "sessions" / "session.jsonl"
        session_file.write_text("abcd", encoding="utf-8")
        codex_session = server.OPENCLAW_DIR / "agents" / "scout" / "agent" / "codex-home" / "sessions" / "rollout.jsonl"
        codex_session.write_text("efghij", encoding="utf-8")

        status, payload = self.request("GET", "/api/agent-sessions")
        self.assertEqual(status, 200)
        scout = next(agent for agent in payload if agent["agent"] == "Scout")
        self.assertEqual(scout["agent_id"], "scout")
        self.assertEqual(scout["file_count"], 2)
        self.assertGreater(scout["total_size_kb"], 0)

    def test_agent_activity_returns_log_ids_and_task_descriptions(self):
        server.AGENT_LOGS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(server.AGENT_LOGS_DB_PATH)
        try:
            con.execute(
                """
                CREATE TABLE agent_logs (
                    agent_name TEXT NOT NULL,
                    task_description TEXT NOT NULL,
                    model_used TEXT,
                    status TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                INSERT INTO agent_logs (agent_name, task_description, model_used, status, created_at)
                VALUES ('Scout', 'Finished signal scan', 'test-model', 'completed', '2026-07-20T10:00:00+00:00')
                """
            )
            con.commit()
        finally:
            con.close()

        status, payload = self.request("GET", "/api/agent-activity")
        self.assertEqual(status, 200)
        self.assertEqual(payload[0]["agent"], "Scout")
        self.assertEqual(payload[0]["task_description"], "Finished signal scan")
        self.assertIn("id", payload[0])

    def test_bootstrap_includes_configured_models_and_model_options(self):
        status, payload = self.request("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        scout = next(agent for agent in payload["agents"] if agent["id"] == "scout")
        self.assertEqual(scout["model"], "test-scout")
        self.assertEqual(scout["configuredModel"], "test-scout")
        self.assertIn({"id": "test-upgrade", "alias": "Upgrade"}, payload["modelOptions"])

    def test_agent_model_can_be_changed_to_an_available_model(self):
        status, payload = self.request("PATCH", "/api/agents/scout/model", {"model": "test-upgrade"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["id"], "scout")
        self.assertEqual(payload["model"], "test-upgrade")

        config = json.loads(server.OPENCLAW_CONFIG_PATH.read_text(encoding="utf-8"))
        scout = next(agent for agent in config["agents"]["list"] if agent["id"] == "scout")
        self.assertEqual(scout["model"], "test-upgrade")

        status, payload = self.request("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        scout = next(agent for agent in payload["agents"] if agent["id"] == "scout")
        self.assertEqual(scout["configuredModel"], "test-upgrade")

    def test_agent_model_rejects_unavailable_models(self):
        status, payload = self.request("PATCH", "/api/agents/scout/model", {"model": "unknown-model"})
        self.assertEqual(status, 400)
        self.assertIn("available models", payload["error"]["message"])

    def test_listing_documents_uses_existing_agent_files_without_body(self):
        self.write_doc_file("scout", "market-brief.md", "# Market Brief\n\nSignals.")
        self.write_doc_file("scribe", "draft.txt", "Plain text draft")
        status, payload = self.request("GET", "/api/documents")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload), 2)
        first = next(item for item in payload if item["filename"] == "market-brief.md")
        self.assertEqual(first["title"], "Market Brief")
        self.assertEqual(first["agent"], "scout")
        self.assertIn("size", first)
        self.assertIn("modified_at", first)
        self.assertNotIn("content", first)

    def test_reading_a_single_document_returns_full_content(self):
        self.write_doc_file("scribe", "launch-note.md", "# Launch Note\n\nBody copy.")
        status, payload = self.request("GET", "/api/documents/scribe/launch-note.md")
        self.assertEqual(status, 200)
        self.assertEqual(payload["title"], "Launch Note")
        self.assertEqual(payload["content"], "# Launch Note\n\nBody copy.")

    def test_creating_updating_and_deleting_a_document(self):
        status, created = self.request(
            "POST",
            "/api/documents/scout",
            {"filename": "trend-scan.md", "content": "# Trend Scan\n\nInitial"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(created["agent"], "scout")
        self.assertEqual(created["filename"], "trend-scan.md")
        self.assertEqual(created["content"], "# Trend Scan\n\nInitial")

        status, updated = self.request(
            "PUT",
            "/api/documents/scout/trend-scan.md",
            {"content": "# Trend Scan\n\nUpdated"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["content"], "# Trend Scan\n\nUpdated")

        status, payload = self.request("DELETE", "/api/documents/scout/trend-scan.md")
        self.assertEqual(status, 200)
        self.assertTrue(payload["deleted"])

        status, payload = self.request("GET", "/api/documents/scout/trend-scan.md")
        self.assertEqual(status, 404)

    def test_rejecting_unsafe_or_unsupported_document_paths(self):
        status, payload = self.request(
            "POST",
            "/api/documents/scout",
            {"filename": "../escape.md", "content": "bad"},
        )
        self.assertEqual(status, 400)
        self.assertIn("filename", payload["error"]["message"])

        status, payload = self.request(
            "POST",
            "/api/documents/scout",
            {"filename": "image.png", "content": "bad"},
        )
        self.assertEqual(status, 400)
        self.assertIn("filename", payload["error"]["message"])

    def test_creating_a_valid_note(self):
        note = self.create_note()
        self.assertEqual(note["body"], "Prepare the weekly project update")
        self.assertEqual(note["color"], 2)
        self.assertIn("created_at", note)
        self.assertIn("updated_at", note)

    def test_rejecting_an_empty_note(self):
        status, payload = self.request("POST", "/api/notes", {"body": "   ", "color": 2})
        self.assertEqual(status, 400)
        self.assertIn("empty", payload["error"]["message"])

    def test_rejecting_an_invalid_colour(self):
        status, payload = self.request("POST", "/api/notes", {"body": "Valid body", "color": 8})
        self.assertEqual(status, 400)
        self.assertIn("color", payload["error"]["message"])

    def test_updating_only_the_note_body(self):
        note = self.create_note()
        status, payload = self.request("PATCH", f"/api/notes/{note['id']}", {"body": "Updated body"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["body"], "Updated body")
        self.assertEqual(payload["color"], note["color"])
        self.assertNotEqual(payload["updated_at"], note["updated_at"])

    def test_updating_only_the_note_colour(self):
        note = self.create_note(color=1)
        status, payload = self.request("PATCH", f"/api/notes/{note['id']}", {"color": 4})
        self.assertEqual(status, 200)
        self.assertEqual(payload["body"], note["body"])
        self.assertEqual(payload["color"], 4)

    def test_updating_both_note_fields(self):
        note = self.create_note(color=1)
        status, payload = self.request(
            "PATCH",
            f"/api/notes/{note['id']}",
            {"body": "Prepare and send the weekly project update", "color": 4},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["body"], "Prepare and send the weekly project update")
        self.assertEqual(payload["color"], 4)

    def test_updating_a_nonexistent_note(self):
        status, payload = self.request("PATCH", "/api/notes/9999", {"body": "Missing"})
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["message"], "note not found")

    def test_deleting_a_note(self):
        note = self.create_note()
        status, payload = self.request("DELETE", f"/api/notes/{note['id']}")
        self.assertEqual(status, 200)
        self.assertTrue(payload["deleted"])

    def test_deleting_a_nonexistent_note(self):
        status, payload = self.request("DELETE", "/api/notes/9999")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["message"], "note not found")

    def test_fetching_goals(self):
        complete = self.create_goal("Already done")
        self.request("PATCH", f"/api/goals/{complete['id']}/toggle")
        incomplete = self.create_goal("Still open")

        status, payload = self.request("GET", "/api/goals")
        self.assertEqual(status, 200)
        self.assertEqual([goal["id"] for goal in payload], [incomplete["id"], complete["id"]])

    def test_creating_a_goal(self):
        goal = self.create_goal()
        self.assertEqual(goal["body"], "Record the dashboard walkthrough video")
        self.assertFalse(goal["completed"])
        self.assertIn("created_at", goal)

    def test_rejecting_an_empty_goal(self):
        status, payload = self.request("POST", "/api/goals", {"body": "\t"})
        self.assertEqual(status, 400)
        self.assertIn("empty", payload["error"]["message"])

    def test_toggling_a_goal_from_incomplete_to_complete(self):
        goal = self.create_goal()
        status, payload = self.request("PATCH", f"/api/goals/{goal['id']}/toggle")
        self.assertEqual(status, 200)
        self.assertTrue(payload["completed"])

    def test_toggling_a_goal_back_to_incomplete(self):
        goal = self.create_goal()
        self.request("PATCH", f"/api/goals/{goal['id']}/toggle")
        status, payload = self.request("PATCH", f"/api/goals/{goal['id']}/toggle")
        self.assertEqual(status, 200)
        self.assertFalse(payload["completed"])

    def test_toggling_a_nonexistent_goal(self):
        status, payload = self.request("PATCH", "/api/goals/9999/toggle")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["message"], "goal not found")

    def test_deleting_a_goal(self):
        goal = self.create_goal()
        status, payload = self.request("DELETE", f"/api/goals/{goal['id']}")
        self.assertEqual(status, 200)
        self.assertTrue(payload["deleted"])

    def test_deleting_a_nonexistent_goal(self):
        status, payload = self.request("DELETE", "/api/goals/9999")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["message"], "goal not found")

    def test_fetching_productivity_state_uses_current_period_labels(self):
        status, payload = self.request("GET", "/api/productivity")
        self.assertEqual(status, 200)
        self.assertIn("monthName", payload)
        self.assertIn("weekNumber", payload)
        self.assertEqual(len(payload["monthProgress"]), calendar.monthrange(2026, 7)[1])
        self.assertEqual(len(payload["monthly"]), 5)
        self.assertEqual(len(payload["weekly"]), 5)
        self.assertEqual(len(payload["defaults"]), 6)
        self.assertEqual(len(payload["weekDays"]), 7)

    def test_productivity_items_can_be_created_toggled_and_deleted(self):
        status, item = self.request("POST", "/api/productivity/items", {"scope": "monthly", "body": "Ship the planner"})
        self.assertEqual(status, 201)
        self.assertEqual(item["scope"], "monthly")
        self.assertFalse(item["completed"])

        status, item = self.request("PATCH", f"/api/productivity/items/{item['id']}/toggle", {})
        self.assertEqual(status, 200)
        self.assertTrue(item["completed"])

        status, payload = self.request("DELETE", f"/api/productivity/items/{item['id']}")
        self.assertEqual(status, 200)
        self.assertTrue(payload["deleted"])

    def test_productivity_progress_updates_default_task_for_a_day(self):
        status, payload = self.request("GET", "/api/productivity")
        self.assertEqual(status, 200)
        task_id = payload["defaults"][0]["id"]
        date_key = payload["weekDays"][0]["dateKey"]

        status, progress = self.request(
            "POST",
            "/api/productivity/progress",
            {"date": date_key, "item_id": task_id, "completed": True},
        )
        self.assertEqual(status, 200)
        self.assertTrue(progress["completed"])

        status, payload = self.request("GET", "/api/productivity")
        self.assertEqual(status, 200)
        day = next(day for day in payload["weekDays"] if day["dateKey"] == date_key)
        self.assertTrue(day["done"][0])

    def test_productivity_notes_can_be_saved_cleared_and_deleted(self):
        status, payload = self.request(
            "PATCH",
            "/api/productivity/notes/2026-07-20",
            {
                "title": "Planning",
                "items": [
                    {"body": "Done item", "completed": True},
                    {"body": "Open item", "completed": False},
                ],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["title"], "Planning")
        self.assertEqual(len(payload["items"]), 2)

        status, state = self.request("GET", "/api/productivity?date=2026-07-20")
        self.assertEqual(status, 200)
        day_stats = next(record for record in state["monthProgress"] if record["date"] == "2026-07-20")
        self.assertEqual(day_stats["customCompleted"], 1)
        self.assertEqual(day_stats["customTotal"], 2)
        self.assertGreaterEqual(day_stats["value"], 0)
        self.assertLessEqual(day_stats["value"], 1)

        status, payload = self.request(
            "PATCH",
            "/api/productivity/notes/2026-07-20",
            {"action": "clear_completed"},
        )
        self.assertEqual(status, 200)
        self.assertEqual([item["body"] for item in payload["items"]], ["Open item"])

        status, payload = self.request("DELETE", "/api/productivity/notes/2026-07-20")
        self.assertEqual(status, 200)
        self.assertTrue(payload["deleted"])

    def test_validation_errors(self):
        status, payload = self.request("PATCH", "/api/notes/not-an-id", {"body": "Valid"})
        self.assertEqual(status, 400)
        self.assertIn("id", payload["error"]["message"])

        status, payload = self.request("PATCH", "/api/notes/1", {"unexpected": True})
        self.assertEqual(status, 400)
        self.assertIn("unsupported", payload["error"]["message"])

        status, payload = self.request("PATCH", "/api/notes/1", {})
        self.assertEqual(status, 400)
        self.assertIn("required", payload["error"]["message"])


if __name__ == "__main__":
    unittest.main()
