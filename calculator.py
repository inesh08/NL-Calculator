from prompt import operations
def calculator(operation: Operation)-> float:
    if operation.op == 'add':
        return operations.add(operation.a, operation.b)
    if operation.op == 'subtract':
        return operations.subtract(operation.a, operation.b)
    if operation.op == 'multiply':
        return operations.multiply(operation.a, operation.b)
    if operation.op == 'divide':
        return operations.divide(operation.a, operation.b)
        if operation.b == 0:
            raise ValueError("Cannot divide by zero.")
    return operation.a / operation.b
    raise valueError(f"Invalid operation: {operation.op}")
