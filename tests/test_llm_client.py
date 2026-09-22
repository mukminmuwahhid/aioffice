import unittest
from unittest.mock import patch

from office import llm_client


class FakeStatusError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class TestRetryPolicy(unittest.TestCase):
    def test_transient_statuses_are_retryable(self):
        for status in (408, 409, 429, 500, 502, 503, 504):
            self.assertTrue(llm_client._is_retryable(FakeStatusError(status)))

    def test_permanent_client_errors_are_not_retryable(self):
        for status in (400, 401, 403, 404, 422):
            self.assertFalse(llm_client._is_retryable(FakeStatusError(status)))


class TestMockBudget(unittest.TestCase):
    def setUp(self):
        llm_client.reset_usage()

    @patch("office.llm_client._mock_delay", return_value=None)
    @patch("office.llm_client.config.is_mock_mode", return_value=True)
    def test_text_call_records_projected_tokens_and_cost(self, _mock_mode, _delay):
        llm_client.call_text(
            "You are the QA Engineer.",
            "Create a test plan.",
            model="claude-sonnet-5",
        )
        usage = llm_client.get_usage()
        self.assertEqual(usage["calls"], 1)
        self.assertEqual(usage["api_calls"], 0)
        self.assertTrue(usage["mock_estimate"])
        self.assertGreater(usage["input_tokens"], 0)
        self.assertGreater(usage["output_tokens"], 0)
        self.assertGreater(usage["cost_usd"], 0)
        self.assertEqual(len(usage["call_details"]), 1)
        self.assertEqual(usage["call_details"][0]["model"], "claude-sonnet-5")

    @patch("office.llm_client._mock_delay", return_value=None)
    @patch("office.llm_client.config.is_mock_mode", return_value=True)
    def test_tool_call_is_included_in_budget(self, _mock_mode, _delay):
        llm_client.call_tool(
            "Chief prompt",
            "Break down this mission",
            model="claude-sonnet-5",
            tool_name="submit_task_breakdown",
            tool_description="Submit tasks",
            input_schema={"type": "object"},
        )
        detail = llm_client.get_usage()["call_details"][0]
        self.assertEqual(detail["label"], "submit_task_breakdown")
        self.assertTrue(detail["mock"])


if __name__ == "__main__":
    unittest.main()
