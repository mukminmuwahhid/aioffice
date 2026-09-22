import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import webapp
from office import store


class WebAppTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._patches = [
            patch.object(store, "STORE_DIR", root / ".office"),
            patch.object(store, "SETTINGS_FILE", root / ".office" / "settings.json"),
            patch.object(store, "ROLES_FILE", root / ".office" / "roles.json"),
            patch("office.config.runs_dir", return_value=root / "runs"),
        ]
        for item in self._patches:
            item.start()
        with webapp._lock:
            webapp._state["phase"] = "idle"
            webapp._state["mission"] = None
            webapp._state["order"] = []
            webapp._state["subtasks"] = {}
            webapp._state["deliverable"] = None
            webapp._state["started_at"] = None
            webapp._state["finished_at"] = None
        webapp._cancel_event = threading.Event()
        webapp.app.config.update(TESTING=True)
        self.client = webapp.app.test_client()

    def tearDown(self):
        for item in self._patches:
            item.stop()
        self._tmp.cleanup()


class TestSettingsApi(WebAppTestCase):
    def test_rejects_non_object_json(self):
        response = self.client.put("/api/settings", json=["not", "an", "object"])
        self.assertEqual(response.status_code, 400)

    def test_rejects_invalid_settings(self):
        response = self.client.put("/api/settings", json={"max_retries": -2})
        self.assertEqual(response.status_code, 400)
        self.assertIn("max_retries", response.get_json()["errors"])

    def test_saves_valid_settings(self):
        response = self.client.put(
            "/api/settings", json={"max_retries": 3, "mock_mode": True}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["settings"]["max_retries"], 3)

    def test_rejects_edit_during_mission(self):
        webapp._state["phase"] = "running"
        response = self.client.put("/api/settings", json={"max_retries": 3})
        self.assertEqual(response.status_code, 409)


class TestAgentApi(WebAppTestCase):
    def test_rejects_invalid_agent_fields(self):
        response = self.client.put(
            "/api/agents/qa_engineer", json={"accent": "red", "prompt": ""}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("accent", response.get_json()["errors"])

    def test_unknown_agent_is_404(self):
        response = self.client.put("/api/agents/not-real", json={})
        self.assertEqual(response.status_code, 404)

    def test_rejects_non_object_json(self):
        response = self.client.put("/api/agents/qa_engineer", json=["bad"])
        self.assertEqual(response.status_code, 400)


class TestLiveStatus(WebAppTestCase):
    def test_done_event_attaches_output_immediately(self):
        webapp._on_event(
            "decompose_done",
            {"subtasks": [webapp.chief.SubTask("T1", "Draft", "qa_engineer")]},
        )
        webapp._on_event(
            "subtask_done",
            {
                "id": "T1",
                "role": "qa_engineer",
                "error": None,
                "output": "live draft",
                "duration_seconds": 1.2,
            },
        )
        task = self.client.get("/status").get_json()["subtasks"][0]
        self.assertEqual(task["output"], "live draft")

    def test_desk_aggregates_multiple_tasks(self):
        tasks = [
            webapp.chief.SubTask("T1", "First", "qa_engineer"),
            webapp.chief.SubTask("T2", "Second", "qa_engineer"),
        ]
        webapp._on_event("decompose_done", {"subtasks": tasks})
        webapp._on_event("subtask_start", {"id": "T2", "role": "qa_engineer"})
        desk = next(
            item for item in self.client.get("/status").get_json()["desks"]
            if item["id"] == "qa_engineer"
        )
        self.assertEqual(desk["task_count"], 2)
        self.assertEqual(desk["status"], "running")


if __name__ == "__main__":
    unittest.main()
