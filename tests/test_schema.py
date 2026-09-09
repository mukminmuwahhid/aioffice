import unittest

from office.schema import AgentOutput, Deliverable, SubTask


class TestSchema(unittest.TestCase):
    def test_subtask_defaults_no_dependencies(self):
        t = SubTask(id="T1", description="x", role="qa_engineer")
        self.assertEqual(t.depends_on, [])

    def test_deliverable_default_review_banner(self):
        d = Deliverable(mission="m", subtasks=[], outputs=[], synthesis="s", next_actions="n")
        self.assertIn("Draft only", d.review_banner)

    def test_agent_output_error_defaults_none(self):
        o = AgentOutput(subtask_id="T1", role="qa_engineer", content="ok")
        self.assertIsNone(o.error)


if __name__ == "__main__":
    unittest.main()
