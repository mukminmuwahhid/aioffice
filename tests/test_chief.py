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


if __name__ == "__main__":
    unittest.main()
