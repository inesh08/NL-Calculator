import unittest
from unittest.mock import patch

from prompt import Operation

import interactions


class TrackedOperationTests(unittest.TestCase):
    def test_records_success_and_returns_operation(self):
        result = Operation(outcome="calculation", message="", operation="add", a=1.0, b=2.0)
        with patch("interactions.get_operation", return_value=result), \
             patch("interactions.record_llm_activity") as record:
            self.assertIs(interactions.get_tracked_operation("1+2"), result)
        record.assert_called_once_with("success")

    def test_records_failure_and_reraises(self):
        with patch("interactions.get_operation", side_effect=RuntimeError("model down")), \
             patch("interactions.record_llm_activity") as record:
            with self.assertRaisesRegex(RuntimeError, "model down"):
                interactions.get_tracked_operation("1+2")
        record.assert_called_once_with("failure")


if __name__ == "__main__":
    unittest.main()
