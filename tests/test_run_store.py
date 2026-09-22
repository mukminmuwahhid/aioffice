import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from office import run_store
from office.schema import AgentOutput, Deliverable, SubTask


def _deliverable(mission="Build a site", **kwargs):
    return Deliverable(
        mission=mission,
        subtasks=[SubTask(id="T1", description="Design", role="ui_ux_designer")],
        outputs=[AgentOutput(subtask_id="T1", role="ui_ux_designer", content="wireframe")],
        synthesis="synthesized",
        next_actions="review it",
        **kwargs,
    )


class RunStoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.runs_dir = Path(self._tmp.name) / "runs"
        self._patch = patch("office.config.runs_dir", return_value=self.runs_dir)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()


class TestSaveAndLoad(RunStoreTestCase):
    def test_save_writes_both_formats(self):
        run_dir = run_store.save_run(_deliverable())
        self.assertTrue((run_dir / "deliverable.md").is_file())
        self.assertTrue((run_dir / "run.json").is_file())

    def test_same_mission_saved_twice_gets_unique_ids(self):
        first = run_store.save_run(_deliverable())
        second = run_store.save_run(_deliverable())
        self.assertNotEqual(first.name, second.name)

    def test_list_runs_summarises(self):
        run_store.save_run(_deliverable("First mission"))
        runs = run_store.list_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["mission"], "First mission")
        self.assertEqual(runs[0]["subtask_count"], 1)
        self.assertEqual(runs[0]["failed_count"], 0)
        self.assertTrue(runs[0]["readable"])

    def test_list_runs_counts_failures(self):
        d = _deliverable()
        d.outputs[0].error = "rate limited"
        run_store.save_run(d)
        self.assertEqual(run_store.list_runs()[0]["failed_count"], 1)

    def test_list_runs_on_missing_directory(self):
        self.assertEqual(run_store.list_runs(), [])

    def test_load_run_returns_payload(self):
        run_dir = run_store.save_run(_deliverable("Loadable"))
        data = run_store.load_run(run_dir.name)
        self.assertEqual(data["mission"], "Loadable")
        self.assertEqual(data["id"], run_dir.name)

    def test_load_missing_run_returns_none(self):
        self.assertIsNone(run_store.load_run("does-not-exist"))

    def test_unreadable_run_still_listed(self):
        run_dir = run_store.save_run(_deliverable())
        (run_dir / "run.json").write_text("{corrupt", encoding="utf-8")
        summary = run_store.list_runs()[0]
        self.assertFalse(summary["readable"])

    def test_cancelled_and_usage_surface_in_summary(self):
        d = _deliverable(cancelled=True, duration_seconds=4.2,
                         usage={"cost_usd": 0.0123, "calls": 3})
        run_store.save_run(d)
        summary = run_store.list_runs()[0]
        self.assertTrue(summary["cancelled"])
        self.assertEqual(summary["duration_seconds"], 4.2)
        self.assertEqual(summary["cost_usd"], 0.0123)


class TestRunIdSafety(RunStoreTestCase):
    def test_traversal_ids_are_rejected(self):
        run_store.save_run(_deliverable())
        for bad in ("../secret", "..\\secret", "a/b", "", "."):
            self.assertIsNone(run_store.load_run(bad), f"{bad!r} should be rejected")
            self.assertFalse(run_store.delete_run(bad), f"{bad!r} should be rejected")

    def test_delete_removes_run(self):
        run_dir = run_store.save_run(_deliverable())
        self.assertTrue(run_store.delete_run(run_dir.name))
        self.assertFalse(run_dir.exists())
        self.assertEqual(run_store.list_runs(), [])


if __name__ == "__main__":
    unittest.main()
