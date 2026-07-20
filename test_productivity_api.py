import json
import calendar
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import server


class ProductivityApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = server.DATA_DIR
        self.original_db_path = server.DB_PATH
        server.DATA_DIR = Path(self.temp_dir.name)
        server.DB_PATH = server.DATA_DIR / "dashboard.db"
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

    def test_fetching_seeded_notes(self):
        status, payload = self.request("GET", "/api/notes")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload), 4)
        self.assertEqual(len({note["color"] for note in payload}), 4)

        server.init_db()
        status, payload = self.request("GET", "/api/notes")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload), 4)

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
