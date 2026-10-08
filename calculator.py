
import math

from prompt import Operation


MAX_NUMBER = 1e100


def calculate(operation: Operation) -> float:
    if operation.outcome != "calculation":
        raise ValueError("Cannot execute a non-calculation response")

    if operation.operation is None:
        raise ValueError("Missing operation")

    if operation.a is None:
        raise ValueError("Missing first number")

    if operation.b is None:
        raise ValueError("Missing second number")

    if not math.isfinite(operation.a):
        raise ValueError("First number must be finite")

    if not math.isfinite(operation.b):
        raise ValueError("Second number must be finite")

    if abs(operation.a) > MAX_NUMBER:
        raise ValueError("First number is too large")

    if abs(operation.b) > MAX_NUMBER:
        raise ValueError("Second number is too large")

    if operation.operation == "add":
        result = operation.a + operation.b

    elif operation.operation == "subtract":
        result = operation.a - operation.b

    elif operation.operation == "multiply":
        result = operation.a * operation.b

    elif operation.operation == "divide":
        if operation.b == 0:
            raise ValueError("Cannot divide by zero")

        result = operation.a / operation.b

    else:
        raise ValueError(f"Unknown operation: {operation.operation}")

    if not math.isfinite(result):
        raise ValueError("Calculation result is not finite")

    return round(result, 12)

