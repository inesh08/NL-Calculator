from prompt import get_operation
from calculator import calculate


def main():
    question = input("What calculation do you want to do? ")

    operation = get_operation(question)

    print("a:", operation.a)
    print("b:", operation.b)
    print("operation:", operation.operation)

    answer = calculate(operation)

    print("answer:", answer)


if __name__ == "__main__":
    main()