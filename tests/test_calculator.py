import unittest

from calculator import MAX_NUMBER, calculate
from prompt import Operation


def operation(kind, a=8.0, b=2.0, outcome="calculation"):
    return Operation(outcome=outcome, message="", operation=kind, a=a, b=b)


class CalculateTests(unittest.TestCase):
    def test_each_arithmetic_operation(self):
        cases = [("add", 10), ("subtract", 6), ("multiply", 16), ("divide", 4)]
        for kind, expected in cases:
            with self.subTest(kind=kind):
                self.assertEqual(calculate(operation(kind)), expected)

    def test_rounds_to_twelve_decimal_places(self):
        self.assertEqual(calculate(operation("divide", 1.0, 3.0)), 0.333333333333)

    def test_rejects_non_calculation_outcome(self):
        with self.assertRaisesRegex(ValueError, "non-calculation"):
            calculate(operation("add", outcome="clarification"))

    def test_rejects_missing_operation_and_operands(self):
        for values, message in [
            ({"operation": None, "a": 1.0, "b": 2.0}, "Missing operation"),
            ({"operation": "add", "a": None, "b": 2.0}, "Missing first number"),
            ({"operation": "add", "a": 1.0, "b": None}, "Missing second number"),
        ]:
            item = Operation.model_construct(outcome="calculation", message="", **values)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                calculate(item)

    def test_rejects_nonfinite_and_out_of_range_operands(self):
        for value, message in [(float("inf"), "must be finite"), (MAX_NUMBER * 2, "too large")]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                calculate(operation("add", value, 1.0))

    def test_rejects_division_by_zero_and_unknown_operation(self):
        with self.assertRaisesRegex(ValueError, "divide by zero"):
            calculate(operation("divide", 1.0, 0.0))
        unknown = Operation.model_construct(outcome="calculation", message="", operation="mod", a=1.0, b=2.0)
        with self.assertRaisesRegex(ValueError, "Unknown operation"):
            calculate(unknown)
        # The MAX_NUMBER operand guard makes a non-finite result unreachable for
        # supported operations, but the largest accepted product stays finite.
        self.assertEqual(calculate(operation("multiply", MAX_NUMBER, MAX_NUMBER)), 1e200)


if __name__ == "__main__":
    unittest.main()
