from prompt import Operation


def calculate(operation: Operation) -> float:
    if operation.operation == "add":
        return operation.a + operation.b

    if operation.operation == "subtract":
        return operation.a - operation.b

    if operation.operation == "multiply":
        return operation.a * operation.b

    if operation.operation == "divide":
        if operation.b == 0:
            raise ValueError("Cannot divide by zero")
        return operation.a / operation.b

    raise ValueError("Invalid operation")