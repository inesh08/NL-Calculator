import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import main
from prompt import Operation


class MainTests(unittest.TestCase):
    def run_main(self, inputs, operations, calculate_result=None, calculate_error=None, init_error=None):
        output = io.StringIO()
        with patch("main.initialize_database", side_effect=init_error) as initialize, \
             patch("builtins.input", side_effect=inputs), \
             patch("main.get_tracked_operation", side_effect=operations) as tracked, \
             patch("main.calculate", side_effect=calculate_error, return_value=calculate_result), \
             patch("main.save_exchange") as save, redirect_stdout(output):
            main.main()
        return output.getvalue(), save, initialize, tracked

    def test_calculation_path_saves_question_and_answer(self):
        operation = Operation(outcome="calculation", message="", operation="add", a=2.0, b=3.0)
        output, save, initialize, _ = self.run_main(["2 plus 3"], [operation], calculate_result=5)
        self.assertIn("Result: 5", output)
        initialize.assert_called_once()
        kwargs = save.call_args.kwargs
        self.assertEqual((kwargs["source"], kwargs["question"], kwargs["answer"]),
                         ("terminal", "2 plus 3", "Result: 5"))

    def test_out_of_scope_path_saves_message(self):
        operation = Operation(outcome="out_of_scope", message="Calculator only.", operation=None, a=None, b=None)
        output, save, _, _ = self.run_main(["hello"], [operation])
        self.assertIn("Calculator only.", output)
        self.assertEqual(save.call_args.kwargs["answer"], "Calculator only.")

    def test_clarification_saves_each_visible_turn_not_internal_context(self):
        clarification = Operation(outcome="clarification", message="Which one?", operation=None, a=None, b=None)
        calculation = Operation(outcome="calculation", message="", operation="add", a=2.0, b=3.0)
        output, save, _, tracked = self.run_main(["add two", "two and three"], [clarification, calculation], calculate_result=5)
        self.assertIn("Result: 5", output)
        self.assertEqual(save.call_count, 2)
        self.assertEqual(save.call_args_list[0].kwargs["question"], "add two")
        self.assertEqual(save.call_args_list[0].kwargs["answer"], "Which one?")
        self.assertEqual(save.call_args_list[1].kwargs["question"], "two and three")
        self.assertIn("Original request: add two", tracked.call_args_list[1].args[0])

    def test_value_error_and_general_error_responses_are_saved(self):
        for exception, expected in [(ValueError("bad calculation"), "Unable to calculate: bad calculation"),
                                    (RuntimeError("temporary"), "Unable to process the request: temporary")]:
            with self.subTest(expected=expected):
                output, save, _, _ = self.run_main(["request"], [exception])
                self.assertIn(expected, output)
                self.assertEqual(save.call_args.kwargs["answer"], expected)

    def test_database_initialization_failure_does_not_block_calculation(self):
        operation = Operation(outcome="calculation", message="", operation="add", a=1.0, b=1.0)
        with self.assertLogs("nl_calculator.terminal", level="ERROR"):
            output, save, _, _ = self.run_main(["1 plus 1"], [operation], calculate_result=2,
                                               init_error=RuntimeError("database unavailable"))
        self.assertIn("Result: 2", output)
        save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
