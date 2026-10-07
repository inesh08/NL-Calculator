
from calculator import calculate
from prompt import get_operation


def main():
    question = input("What calculation do you want to do? ")

    while True:
        try:
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

            result = calculate(operation)

            print("Result:", result)
            return

        except ValueError as error:
            print("Unable to calculate:", error)
            return

        except Exception as error:
            print("Unable to process the request:", error)
            return


if __name__ == "__main__":
    main()

