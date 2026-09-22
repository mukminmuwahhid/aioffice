import unittest

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


if __name__ == "__main__":
    unittest.main()
