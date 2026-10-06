from prompt import get_operation
from calculator import calculate


def main():
    question = input("What calculation do you want to do? ")

    while True:
        operation = get_operation(question)

        if operation.outcome == "out_of_scope":
            print(operation.message)
            return

        if operation.outcome == "clarification":
            clarification = input(operation.message + " ")
            question = (
                f"Original request: {question}\n"
                f"Clarification question: {operation.message}\n"
                f"User's clarification: {clarification}"
            )
            continue

        if operation.operation is None or operation.a is None or operation.b is None:
            raise ValueError("The model returned an incomplete calculation")

        break

    print("a:", operation.a)
    print("b:", operation.b)
    print("operation:", operation.operation)

    answer = calculate(operation)

    print("answer:", answer)


if __name__ == "__main__":
    main()
