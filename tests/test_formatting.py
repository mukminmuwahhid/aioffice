import unittest

from office.formatting import render_markdown
from office.schema import AgentOutput, Deliverable, SubTask


class TestRenderMarkdown(unittest.TestCase):
    def test_all_six_sections_present(self):
        subtasks = [SubTask(id="T1", description="Design homepage", role="ui_ux_designer")]
        outputs = [
            AgentOutput(subtask_id="T1", role="ui_ux_designer", content="Wireframe: header, hero, footer.")
        ]
        deliverable = Deliverable(
            mission="Build a homepage",
            subtasks=subtasks,
            outputs=outputs,
            synthesis="Combined plan.",
            next_actions="Review and approve.",
        )
        md = render_markdown(deliverable)
        for heading in [
            "Mission Summary", "Task Breakdown", "Agent Outputs",
            "Synthesized Deliverable", "Next Actions", "Review Banner",
        ]:
            self.assertIn(heading, md)
        self.assertIn("Draft only", md)
        self.assertIn("Wireframe: header, hero, footer.", md)

    def test_failed_subtask_shown_as_failed(self):
        subtasks = [SubTask(id="T1", description="x", role="qa_engineer")]
        outputs = [AgentOutput(subtask_id="T1", role="qa_engineer", content="", error="timeout")]
        deliverable = Deliverable(
            mission="m", subtasks=subtasks, outputs=outputs,
            synthesis="s", next_actions="n",
        )
        md = render_markdown(deliverable)
        self.assertIn("Failed: timeout", md)


if __name__ == "__main__":
    unittest.main()
