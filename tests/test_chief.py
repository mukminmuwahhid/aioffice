import threading
import unittest
from unittest.mock import patch

from office import chief
from office.schema import AgentOutput, SubTask


class TestDecomposeMission(unittest.TestCase):
    @patch("office.chief.llm_client.call_tool")
    def test_decompose_mission_builds_subtasks(self, mock_call_tool):
        mock_call_tool.return_value = {
            "subtasks": [
                {"id": "T1", "description": "Design UI", "role": "ui_ux_designer", "depends_on": []},
                {"id": "T2", "description": "Build UI", "role": "frontend_developer", "depends_on": ["T1"]},
            ]
        }
        subtasks = chief.decompose_mission("Build a website")
        self.assertEqual(len(subtasks), 2)
        self.assertEqual(subtasks[1].depends_on, ["T1"])


class TestRunSubtasks(unittest.TestCase):
    @patch("office.chief.llm_client.call_text")
    def test_runs_in_dependency_order(self, mock_call_text):
        calls = []

        def fake_call_text(system, user, model, max_tokens=4096):
            calls.append(user)
            return "draft output"

        mock_call_text.side_effect = fake_call_text

        t1 = SubTask(id="T1", description="Design", role="ui_ux_designer")
        t2 = SubTask(id="T2", description="Build", role="frontend_developer", depends_on=["T1"])

        outputs = chief.run_subtasks([t2, t1])  # deliberately out of order
        self.assertEqual({o.subtask_id for o in outputs}, {"T1", "T2"})
        t2_call = next(u for u in calls if "Build" in u)
        self.assertIn("draft output", t2_call)

    @patch("office.chief.llm_client.call_text")
    def test_failed_subtask_is_captured_not_raised(self, mock_call_text):
        mock_call_text.side_effect = RuntimeError("rate limited")
        t1 = SubTask(id="T1", description="Design", role="ui_ux_designer")
        outputs = chief.run_subtasks([t1])
        self.assertEqual(len(outputs), 1)
        self.assertIsNotNone(outputs[0].error)


class TestSynthesize(unittest.TestCase):
    @patch("office.chief.llm_client.call_text")
    def test_synthesize_includes_role_outputs(self, mock_call_text):
        mock_call_text.return_value = "final deliverable text"
        t1 = SubTask(id="T1", description="Design", role="ui_ux_designer")
        outputs = [AgentOutput(subtask_id="T1", role="ui_ux_designer", content="wireframe here")]
        result = chief.synthesize("Build a site", [t1], outputs)
        self.assertEqual(result, "final deliverable text")

    @patch("office.chief.llm_client.call_text")
    def test_synthesize_skips_subtasks_with_no_output(self, mock_call_text):
        """A cancelled run leaves subtasks without outputs - don't blow up."""
        mock_call_text.return_value = "partial deliverable"
        t1 = SubTask(id="T1", description="Design", role="ui_ux_designer")
        t2 = SubTask(id="T2", description="Build", role="frontend_developer")
        outputs = [AgentOutput(subtask_id="T1", role="ui_ux_designer", content="wireframe")]
        self.assertEqual(chief.synthesize("Build a site", [t1, t2], outputs), "partial deliverable")


class TestCancellation(unittest.TestCase):
    @patch("office.chief.llm_client.call_text")
    def test_cancel_stops_dispatching_later_levels(self, mock_call_text):
        cancel = threading.Event()

        def fake_call_text(system, user, model, max_tokens=4096):
            cancel.set()  # cancel as soon as the first level runs
            return "draft output"

        mock_call_text.side_effect = fake_call_text

        t1 = SubTask(id="T1", description="Design", role="ui_ux_designer")
        t2 = SubTask(id="T2", description="Build", role="frontend_developer", depends_on=["T1"])

        outputs = chief.run_subtasks([t1, t2], cancel_event=cancel)
        self.assertEqual([o.subtask_id for o in outputs], ["T1"])

    @patch("office.chief.llm_client.call_text")
    def test_cancel_before_start_runs_nothing(self, mock_call_text):
        cancel = threading.Event()
        cancel.set()
        t1 = SubTask(id="T1", description="Design", role="ui_ux_designer")
        self.assertEqual(chief.run_subtasks([t1], cancel_event=cancel), [])
        mock_call_text.assert_not_called()


class TestDisabledAgents(unittest.TestCase):
    @patch("office.chief.roles.enabled_role_ids")
    def test_disabled_roles_are_dropped_with_their_dependents_unlinked(self, mock_enabled):
        mock_enabled.return_value = ["ui_ux_designer", "frontend_developer"]
        t1 = SubTask(id="T1", description="Design", role="ui_ux_designer")
        t2 = SubTask(id="T2", description="Build", role="frontend_developer", depends_on=["T1", "T9"])
        t9 = SubTask(id="T9", description="Price it", role="pricing_proposal_agent")

        kept = chief._drop_disabled([t1, t2, t9])
        self.assertEqual([t.id for t in kept], ["T1", "T2"])
        # The dependency on the dropped subtask is stripped so the graph stays runnable.
        self.assertEqual(kept[1].depends_on, ["T1"])

    @patch("office.chief.roles.enabled_role_ids")
    def test_all_enabled_returns_list_untouched(self, mock_enabled):
        mock_enabled.return_value = ["ui_ux_designer"]
        t1 = SubTask(id="T1", description="Design", role="ui_ux_designer")
        self.assertEqual(chief._drop_disabled([t1]), [t1])

    @patch("office.chief.roles.enabled_role_ids")
    def test_unknown_dependency_survives_disabled_filter_for_validation(self, mock_enabled):
        mock_enabled.return_value = ["ui_ux_designer"]
        task = SubTask(
            id="T1", description="Design", role="ui_ux_designer", depends_on=["UNKNOWN"]
        )
        kept = chief._drop_disabled([task])
        self.assertEqual(kept[0].depends_on, ["UNKNOWN"])


class TestTaskGraphValidation(unittest.TestCase):
    def test_rejects_duplicate_ids(self):
        tasks = [
            SubTask(id="T1", description="One", role="ui_ux_designer"),
            SubTask(id="T1", description="Two", role="frontend_developer"),
        ]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            chief._validate_task_graph(tasks)

    def test_rejects_unknown_dependency(self):
        task = SubTask(
            id="T1", description="Build", role="frontend_developer", depends_on=["T9"]
        )
        with self.assertRaisesRegex(ValueError, "unknown dependency"):
            chief._validate_task_graph([task])

    def test_rejects_self_dependency(self):
        task = SubTask(
            id="T1", description="Build", role="frontend_developer", depends_on=["T1"]
        )
        with self.assertRaisesRegex(ValueError, "itself"):
            chief._validate_task_graph([task])

    def test_rejects_cycle(self):
        tasks = [
            SubTask(id="T1", description="One", role="ui_ux_designer", depends_on=["T2"]),
            SubTask(id="T2", description="Two", role="frontend_developer", depends_on=["T1"]),
        ]
        with self.assertRaisesRegex(ValueError, "cycle"):
            chief._validate_task_graph(tasks)

    def test_accepts_valid_graph(self):
        tasks = [
            SubTask(id="T1", description="One", role="ui_ux_designer"),
            SubTask(id="T2", description="Two", role="frontend_developer", depends_on=["T1"]),
        ]
        chief._validate_task_graph(tasks)


class TestProgressEvents(unittest.TestCase):
    @patch("office.chief.llm_client.call_text", return_value="completed draft")
    def test_done_event_contains_live_output(self, _mock_call):
        events = []
        task = SubTask(id="T1", description="Design", role="ui_ux_designer")
        chief.run_subtasks([task], on_event=lambda event, data: events.append((event, data)))
        done = next(data for event, data in events if event == "subtask_done")
        self.assertEqual(done["output"], "completed draft")
        self.assertIsInstance(done["duration_seconds"], float)


if __name__ == "__main__":
    unittest.main()
