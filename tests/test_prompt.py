import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

import prompt
from prompt import Operation, get_operation, validate_operation


class ValidateOperationTests(unittest.TestCase):
    def test_accepts_valid_calculation_clarification_and_out_of_scope(self):
        operations = [
            Operation(outcome="calculation", message="", operation="add", a=2.0, b=3.0),
            Operation(outcome="clarification", message="Which number?", operation=None, a=None, b=None),
            Operation(outcome="out_of_scope", message="I only calculate.", operation=None, a=None, b=None),
        ]
        for item in operations:
            with self.subTest(outcome=item.outcome):
                self.assertEqual(validate_operation(item), item)

    def test_rejects_invalid_model_schema(self):
        invalid = Operation.model_construct(outcome="not-valid", message="", operation="add", a=1.0, b=2.0)
        with self.assertRaisesRegex(ValueError, "Invalid model response"):
            validate_operation(invalid)

    def test_rejects_missing_calculation_fields(self):
        cases = [
            ({"operation": None, "a": 1.0, "b": 2.0}, "missing operation"),
            ({"operation": "add", "a": None, "b": 2.0}, "missing first number"),
            ({"operation": "add", "a": 1.0, "b": None}, "missing second number"),
        ]
        for fields, message in cases:
            invalid = Operation.model_construct(outcome="calculation", message="", **fields)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                validate_operation(invalid)

    def test_rejects_operands_on_non_calculation_response(self):
        invalid = Operation.model_construct(outcome="clarification", message="Which?", operation="add", a=1.0, b=2.0)
        with self.assertRaisesRegex(ValueError, "operation must be null"):
            validate_operation(invalid)


class GetOperationTests(unittest.TestCase):
    def test_passes_user_question_to_model_and_validates_parsed_result(self):
        parsed = Operation(outcome="calculation", message="", operation="multiply", a=4.0, b=5.0)
        response = SimpleNamespace(output_parsed=parsed)
        with patch.object(prompt.client.responses, "parse", return_value=response) as parse:
            result = get_operation("four times five")
        self.assertEqual(result, parsed)
        self.assertEqual(parse.call_args.kwargs["input"][1]["content"], "four times five")
        self.assertIs(parse.call_args.kwargs["text_format"], Operation)

    def test_rejects_missing_parsed_response(self):
        with patch.object(prompt.client.responses, "parse", return_value=SimpleNamespace(output_parsed=None)):
            with self.assertRaisesRegex(ValueError, "did not return a valid structured response"):
                get_operation("three plus four")

    def test_rejects_invalid_parsed_operation(self):
        parsed = Operation.model_construct(outcome="calculation", message="", operation=None, a=3.0, b=4.0)
        with patch.object(prompt.client.responses, "parse", return_value=SimpleNamespace(output_parsed=parsed)):
            with self.assertRaisesRegex(ValueError, "missing operation"):
                get_operation("three plus four")


if __name__ == "__main__":
    unittest.main()
